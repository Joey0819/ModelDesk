"""Execute user-configured agents/tasks with the installed upstream frameworks.

The parent passes an immutable JSON input file, fixed script and output path.
No shell commands, arbitrary Python, project writes, or dynamic plugins run.
"""
import argparse
import json
import math
import os
from pathlib import Path
import threading

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

GATEWAY = "http://127.0.0.1:8317/v1"
EVENT_LOCK = threading.Lock()


def save(output, result):
    temporary = output.with_suffix(".partial")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(output)


def emit(event_type, **fields):
    # CrewAI may execute native tool calls concurrently. Keep each event and
    # its newline in one critical section so the parent can decode every call.
    with EVENT_LOCK:
        print("MODELDESK_EVENT " + json.dumps({"type": event_type, **fields}, ensure_ascii=False), flush=True)


def arithmetic(a: float, b: float, operation: str = "add") -> float:
    """Calculate two finite numbers. Operation: add, subtract, multiply, divide.

    Args:
        a: First finite number.
        b: Second finite number.
        operation: add, subtract, multiply, or divide.
    """
    a, b = float(a), float(b)
    if not math.isfinite(a) or not math.isfinite(b) or abs(a) > 1e100 or abs(b) > 1e100:
        raise ValueError("请输入范围内的有限数值")
    operations = {"add": lambda: a + b, "subtract": lambda: a - b,
                  "multiply": lambda: a * b, "divide": lambda: a / b}
    if operation not in operations:
        raise ValueError("不支持此运算")
    result = operations[operation]()
    if not math.isfinite(result):
        raise ValueError("运算结果超出有限数值范围")
    emit("tool_result", tool="arithmetic", input={"a": a, "b": b, "operation": operation}, output=result)
    return result


def text_stats(text: str) -> str:
    """Count characters, words and lines of the given text without network access.

    Args:
        text: The text to analyse, at most 20000 characters.
    """
    if len(text) > 20000:
        raise ValueError("统计文本不得超过 20000 字符")
    result = {"characters": len(text), "words": len(text.split()), "lines": len(text.splitlines())}
    emit("tool_result", tool="text_stats", output=result)
    return json.dumps(result, ensure_ascii=False)


def camel(data, output):
    from camel.models import ModelFactory
    from camel.societies import RolePlaying
    from camel.toolkits import FunctionTool, MathToolkit
    from camel.types import ModelPlatformType

    configuration = data["workflow"]
    agents = configuration["agents"]

    def options(agent):
        model = ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                                    model_type=agent["model"], url=GATEWAY, api_key="local-oauth",
                                    model_config_dict={"max_tokens": 2048}, max_retries=0, timeout=180)
        tools = []
        if "math" in agent.get("tools", []):
            # Reuse the installed Apache-licensed toolkit instead of an evaluator.
            tools.extend(MathToolkit().get_tools())
        if "text_stats" in agent.get("tools", []):
            tools.append(FunctionTool(text_stats))
        return {"model": model, "tools": tools, "max_iteration": configuration["max_iter"]}

    task = data["prompt"] + "\n\n角色分工：\n" + "\n".join(
        f"{agent['role']}：目标 {agent['goal']}；背景 {agent['backstory']}" for agent in agents)
    society = RolePlaying(assistant_role_name=agents[0]["role"], user_role_name=agents[1]["role"],
                          task_prompt=task, with_task_specify=False,
                          assistant_agent_kwargs=options(agents[0]), user_agent_kwargs=options(agents[1]),
                          output_language="Chinese")
    message = society.init_chat()
    result = {"models": data["models"], "agents": agents, "messages": [], "partial": True, "tool_events": []}
    for round_index in range(1, data["rounds"] + 1):
        print(f"CAMEL 第 {round_index}/{data['rounds']} 轮：自定义角色讨论", flush=True)
        assistant, user = society.step(message)
        for agent, response in [(agents[1], user), (agents[0], assistant)]:
            if response.msgs:
                entry = {"role": agent["role"], "model": agent["model"], "round": round_index,
                         "content": response.msgs[0].content}
                result["messages"].append(entry)
                emit("framework_message", **entry)
                save(output, result)
            for call in response.info.get("tool_calls", []) if isinstance(response.info, dict) else []:
                event = {"model": agent["model"], "tool": getattr(call, "tool_name", type(call).__name__),
                         "output": str(getattr(call, "result", ""))[:2000]}
                result["tool_events"].append(event)
                emit("tool_result", **event)
        if assistant.terminated or user.terminated or not assistant.msgs:
            break
        message = assistant.msgs[0]
    if not result["messages"]:
        raise RuntimeError("CAMEL 未返回角色对话")
    result["partial"] = False
    save(output, result)


def crewai(data, output):
    from crewai import Agent, Crew, LLM, Process, Task
    from crewai.tools import tool

    configuration = data["workflow"]
    arithmetic_tool = tool("arithmetic")(arithmetic)
    statistics_tool = tool("text_stats")(text_stats)
    toolbox = {"math": arithmetic_tool, "text_stats": statistics_tool}

    def model(name):
        return LLM(model="openai/" + name, base_url=GATEWAY, api_key="local-oauth", max_tokens=2048, timeout=180)

    agents = {entry["id"]: Agent(role=entry["role"], goal=entry["goal"], backstory=entry["backstory"],
                                llm=model(entry["model"]), tools=[toolbox[name] for name in entry.get("tools", [])],
                                allow_delegation=configuration["process"] == "hierarchical",
                                max_iter=configuration["max_iter"])
              for entry in configuration["agents"]}
    result = {"models": data["models"], "agents": configuration["agents"], "process": configuration["process"],
              "tasks": [], "answer": "", "partial": True}
    tasks = {}
    for entry in configuration["tasks"]:
        def completed(task_output, spec=entry):
            item = {"id": spec["id"], "name": spec["id"], "agent": spec["agent"], "description": spec["description"],
                    "context": spec["context"], "output": task_output.raw}
            result["tasks"].append(item)
            result["answer"] = task_output.raw
            emit("framework_task", **item)
            save(output, result)

        description = "用户目标：\n" + data["prompt"] + "\n\n本步骤：\n" + entry["description"]
        tasks[entry["id"]] = Task(name=entry["id"], description=description, expected_output=entry["expected_output"],
                                 agent=agents[entry["agent"]], context=[tasks[name] for name in entry["context"]],
                                 callback=completed)
    options = {}
    if configuration["process"] == "hierarchical":
        options["manager_llm"] = model(configuration["manager_model"])
    crew = Crew(agents=list(agents.values()), tasks=list(tasks.values()),
                process=Process(configuration["process"]), memory=False, verbose=False, **options)
    print(f"CrewAI 自定义团队开始：{len(agents)} 个角色、{len(tasks)} 个任务", flush=True)
    answer = crew.kickoff()
    if not answer.raw:
        raise RuntimeError("CrewAI 未返回协作结果")
    result["answer"] = answer.raw
    result["partial"] = False
    save(output, result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    {"camel": camel, "crewai": crewai}[data["mode"]](data, args.output)


if __name__ == "__main__":
    main()
