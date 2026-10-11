"""Execute user-configured agents/tasks with the installed upstream frameworks.

The parent passes an immutable JSON input file, fixed script and output path.
No shell commands, arbitrary Python, project writes, or dynamic plugins run.
"""
import argparse
from functools import wraps
import hashlib
import io
import json
import math
import os
from pathlib import Path
import threading
import sys
import urllib.error
import urllib.request
import re
import uuid

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

GATEWAY = "http://127.0.0.1:8317/v1"
EVENT_LOCK = threading.Lock()
SAVE_LOCK = threading.RLock()
RUNTIME_TOOLS = "http://127.0.0.1:8001/api/platform/runtime/tools/"


def save(output, result):
    with SAVE_LOCK:
        temporary = output.with_suffix(".partial")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        temporary.chmod(0o600)
        temporary.replace(output)


def emit(event_type, **fields):
    # CrewAI may execute native tool calls concurrently. Keep each event and
    # its newline in one critical section so the parent can decode every call.
    with EVENT_LOCK:
        record = "\nMODELDESK_EVENT " + json.dumps({"type": event_type, **fields}, ensure_ascii=False, allow_nan=False) + "\n"
        # Native Flow's Rich console can print partial lines concurrently.
        # Frame both boundaries and write the UTF-8 record in one syscall.
        try:
            descriptor = sys.stdout.fileno()
        except (AttributeError, io.UnsupportedOperation):
            sys.stdout.write(record)
            sys.stdout.flush()
        else:
            pending = record.encode("utf-8")
            while pending:
                written = os.write(descriptor, pending)
                pending = pending[written:]


def runtime_functions(data):
    """Create typed native framework tools with a fixed, job-bound HTTP bridge."""
    runtime = data.get("runtime") or {}

    def invoke(name, arguments):
        if not runtime.get("token"):
            raise ValueError("此工具需要有效的任务运行权限")
        payload = json.dumps({"token": runtime["token"], "arguments": arguments}, allow_nan=False).encode()
        request = urllib.request.Request(RUNTIME_TOOLS + name, data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                body = response.read(200001)
                if len(body) > 200000:
                    raise ValueError("工具结果超出运行时读取限制")
                value = json.loads(body)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"工具请求失败 HTTP {error.code}，请检查任务资源权限") from None
        # The runtime API records the authoritative execution event. Avoid a
        # second observation event here, and never emit the private token.
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def web_fetch(url: str) -> str:
        """Read a public HTTP webpage through the platform's validated fetcher.

        Args:
            url: Public HTTP or HTTPS URL to fetch.
        """
        return invoke("web_fetch", {"url": url})

    def web_search(query: str, max_results: int = 5) -> str:
        """Search public web pages and return cited search results.

        Args:
            query: Search terms.
            max_results: Number of results, from 1 to 10.
        """
        return invoke("web_search", {"query": query, "max_results": max_results})

    def knowledge_search(query: str, limit: int = 5) -> str:
        """Search only the knowledge documents explicitly attached to this job.

        Args:
            query: Text to find in the attached knowledge.
            limit: Maximum results, from 1 to 20.
        """
        return invoke("knowledge_search", {"query": query, "limit": limit})

    def memory_read(query: str = "", limit: int = 5) -> str:
        """Read saved platform memory within this job's selected memory scope.

        Args:
            query: Optional text search; empty means recent memories.
            limit: Maximum records, from 1 to 20.
        """
        return invoke("memory_read", {"query": query, "limit": limit})

    def memory_save(content: str, name: str = "团队记忆") -> str:
        """Save useful textual context in this job's explicitly selected scope.

        Args:
            content: Text to remember; never save credentials.
            name: Short descriptive record name.
        """
        return invoke("memory_save", {"content": content, "name": name})

    def project_read(path: str, start_line: int = 1, end_line: int = 200) -> str:
        """Read quoted source lines from this job's selected immutable snapshot.

        Args:
            path: Relative file path present in the selected snapshot.
            start_line: First requested source line, one based.
            end_line: Last requested source line, one based.
        """
        return invoke("project_read", {"path": path, "start_line": start_line, "end_line": end_line})

    def mcp_call(server_id: str, tool: str, arguments: dict) -> str:
        """Call an approved tool on one of this job's selected MCP servers.

        Args:
            server_id: ID of a registered, explicitly selected MCP server.
            tool: Approved tool name from that server's catalog.
            arguments: Object of the tool's validated input arguments.
        """
        return invoke("mcp_call", {"server_id": server_id, "tool": tool, "arguments": arguments})

    def statistics(values: list[float]) -> str:
        """Calculate count, mean, median, stdev, min and max of finite numbers.

        Args:
            values: From 1 to 10000 finite numeric values.
        """
        return invoke("statistics", {"values": values})

    def markdown_to_text(text: str) -> str:
        """Convert Markdown into plain readable text using the local platform.

        Args:
            text: Markdown source text to convert.
        """
        return invoke("markdown_to_text", {"text": text})

    return {function.__name__: function for function in
            (text_stats, web_fetch, web_search, knowledge_search, memory_read, memory_save, project_read, mcp_call,
             statistics, markdown_to_text)}


def camel_options(data, agent):
    from camel.models import ModelFactory
    from camel.toolkits import FunctionTool, MathToolkit
    from camel.types import ModelPlatformType

    configuration = data["workflow"]
    model = ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                                model_type=agent["model"], url=GATEWAY, api_key="local-oauth",
                                model_config_dict={"max_tokens": 3072}, max_retries=0, timeout=180)
    tools = []
    if "math" in agent.get("tools", []):
        for native in MathToolkit().get_tools():
            def checked(*args, _function=native.func, **kwargs):
                result = _function(*args, **kwargs)
                if not math.isfinite(float(result)):
                    raise ValueError("运算结果超出有限数值范围")
                emit("tool_result", tool=_function.__name__, input={"args": list(args), "kwargs": kwargs}, output=result)
                return result
            tools.append(FunctionTool(wraps(native.func)(checked)))
    bridge = runtime_functions(data)
    tools.extend(FunctionTool(bridge[name]) for name in agent.get("tools", []) if name != "math")
    return {"model": model, "tools": tools, "max_iteration": configuration["max_iter"],
            "retry_attempts": 0, "tool_execution_timeout": 40, "step_timeout": 240}


def lexical_embeddings(texts):
    """Deterministic local lexical hash vectors; no remote embedding API."""
    vectors = []
    for text in texts:
        vector = [0.0] * 256
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:2], "big") % len(vector)] += 1.0 if digest[2] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        vectors.append([value / norm for value in vector] if norm else vector)
    return vectors


def native_memory(data, model):
    from crewai import Memory
    scope = data["workflow"].get("memory_scope", "default")
    # Neither the user nor the model can supply a storage path.
    path = Path(__file__).resolve().parents[2] / "data" / "platform" / "native-memory" / hashlib.sha256(scope.encode()).hexdigest()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    memory = Memory(llm=model, embedder=lexical_embeddings, storage=str(path), root_scope="/" + scope,
                    exploration_budget=0, query_analysis_threshold=10000)
    emit("framework_message", role="原生记忆", content="CrewAI Memory 已启用；本地词法哈希嵌入与 LanceDB 持久化，分析模型通过已登录网关调用。", scope=scope)
    return memory


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
    from camel.societies import RolePlaying

    configuration = data["workflow"]
    agents = configuration["agents"]

    task = data["prompt"] + "\n\n角色分工：\n" + "\n".join(
        f"{agent['role']}：目标 {agent['goal']}；背景 {agent['backstory']}" for agent in agents)
    auxiliary = {**agents[0], "model": configuration.get("critic_model") or agents[0]["model"], "tools": []}
    auxiliary_options = camel_options(data, auxiliary)
    auxiliary_model = auxiliary_options["model"]
    society = RolePlaying(assistant_role_name=agents[0]["role"], user_role_name=agents[1]["role"],
                          task_prompt=task, with_task_specify=configuration.get("task_specify", False),
                          with_task_planner=configuration.get("task_planning", False),
                          with_critic_in_the_loop=configuration.get("critic", False),
                          critic_criteria=configuration.get("critic_criteria"),
                          task_specify_agent_kwargs={"model": auxiliary_model, "word_limit": 250},
                          task_planner_agent_kwargs={"model": auxiliary_model},
                          critic_kwargs={"model": auxiliary_model, "retry_attempts": 1},
                          assistant_agent_kwargs=camel_options(data, agents[0]), user_agent_kwargs=camel_options(data, agents[1]),
                          output_language="Chinese")
    message = society.init_chat()
    result = {"models": data["models"], "agents": agents, "messages": [], "partial": True, "tool_events": []}
    for field, role in [("specified_task_prompt", "任务细化"), ("planned_task_prompt", "任务规划")]:
        content = getattr(society, field, None)
        if content:
            result[field.replace("_prompt", "")] = str(content)
            emit("framework_message", role=role, model=auxiliary["model"], content=str(content))
    result["critic"] = {"enabled": configuration.get("critic", False), "model": auxiliary["model"],
                        "criteria": configuration.get("critic_criteria"), "selections": []}
    if society.critic is not None:
        native_reduce = society.critic.reduce_step
        def recorded_reduce(messages):
            response = native_reduce(messages)
            selection = response.msg.content
            result["critic"]["selections"].append(selection)
            emit("framework_message", role="Critic 原生选择", model=auxiliary["model"], content=selection)
            save(output, result)
            return response
        society.critic.reduce_step = recorded_reduce
    save(output, result)
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
        if assistant.terminated or user.terminated or not assistant.msgs:
            break
        message = assistant.msgs[0]
    if not result["messages"]:
        raise RuntimeError("CAMEL 未返回角色对话")
    result["partial"] = False
    save(output, result)


def camel_workforce(data, output):
    from camel.agents import ChatAgent
    from camel.societies.workforce import Workforce
    from camel.societies.workforce.workforce_callback import WorkforceCallback
    from camel.tasks import Task

    configuration = data["workflow"]
    specifications = configuration["agents"]
    result = {"models": data["models"], "agents": specifications, "process": "workforce",
              "messages": [], "tasks": [], "answer": "", "workforce_events": [], "worker_roles": {}, "partial": True}

    def record(event):
        entry = event.model_dump(mode="json")
        result["workforce_events"].append(entry)
        if entry.get("event_type") == "task_completed":
            source = result["worker_roles"].get(entry.get("worker_id"), {})
            result["tasks"].append({"id": entry["task_id"], "name": entry["task_id"],
                                    "agent": source.get("agent") or entry.get("worker_id"), "model": source.get("model"),
                                    "worker_id": entry.get("worker_id"), "status": "complete",
                                    "output": entry.get("result_summary") or ""})
        emit("framework_task", name=entry.get("task_id", "workforce"), status=entry["event_type"], native_event=entry)
        save(output, result)

    class Callback(WorkforceCallback):
        def log_message(self, event):
            emit("framework_message", role="Workforce", content=event.message)
        log_task_created = staticmethod(record)
        log_task_decomposed = staticmethod(record)
        log_task_assigned = staticmethod(record)
        log_task_started = staticmethod(record)
        log_task_updated = staticmethod(record)
        log_task_completed = staticmethod(record)
        log_task_failed = staticmethod(record)
        log_worker_created = staticmethod(record)
        log_worker_deleted = staticmethod(record)
        log_all_tasks_completed = staticmethod(record)

    manager_spec = {**specifications[0], "model": configuration.get("manager_model") or specifications[0]["model"], "tools": []}
    coordinator = ChatAgent(system_message="协调配置的团队，依据任务与证据分配工作。", **camel_options(data, manager_spec))
    planner = ChatAgent(system_message="将任务分解为最多四个必要子任务，再基于实际工作结果综合答案。", **camel_options(data, manager_spec))
    worker_template = ChatAgent(system_message="只使用提供的证据完成分配的任务。", **camel_options(data, manager_spec))
    workforce = Workforce(description="ModelDesk 用户配置团队", coordinator_agent=coordinator, task_agent=planner,
                          new_worker_agent=worker_template, default_model=coordinator.model_backend,
                          callbacks=[Callback()], task_timeout_seconds=600,
                          graceful_shutdown_timeout=5, share_memory=False,
                          failure_handling_config={"max_retries": 1, "enabled_strategies": ["retry"], "halt_on_max_retries": True})
    for spec in specifications:
        worker = ChatAgent(system_message=f"角色：{spec['role']}\n目标：{spec['goal']}\n背景：{spec['backstory']}",
                           **camel_options(data, spec))
        workforce.add_single_agent_worker(description=f"{spec['id']}: {spec['role']}；{spec['goal']}", worker=worker, pool_max_size=1)
        result["worker_roles"][workforce._children[-1].node_id] = {"agent": spec["id"], "role": spec["role"], "model": spec["model"]}
    for step in configuration.get("tasks", []):
        workforce.pipeline_add(content=f"用户目标：{data['prompt']}\n步骤 {step['id']}，优先角色 {step['agent']}：{step['description']}\n期望输出：{step['expected_output']}",
                               task_id=step["id"], dependencies=step["context"], auto_depend=False)
    if configuration.get("tasks"):
        workforce.pipeline_build()
    save(output, result)
    task = workforce.process_task(Task(content=data["prompt"], id=str(uuid.uuid4())))
    if not task.result:
        raise RuntimeError("CAMEL Workforce 未返回任务结果")
    result["answer"] = task.result
    result["task_state"] = str(task.state.value)
    if task.state.value != "DONE":
        save(output, result)
        raise RuntimeError("CAMEL Workforce 的原生任务状态未完成；已保留部分输出")
    result["partial"] = False
    save(output, result)


def crew_components(data, output):
    from crewai import Agent, Crew, LLM, Process, Task
    from crewai.tools import tool

    configuration = data["workflow"]
    toolbox = {name: tool(name)(function) for name, function in runtime_functions(data).items()}
    toolbox["math"] = tool("arithmetic")(arithmetic)

    def model(name):
        return LLM(model="openai/" + name, base_url=GATEWAY, api_key="local-oauth", max_tokens=3072, timeout=180)

    agents = {entry["id"]: Agent(role=entry["role"], goal=entry["goal"], backstory=entry["backstory"],
                                llm=model(entry["model"]), tools=[toolbox[name] for name in entry.get("tools", [])],
                                allow_delegation=configuration["process"] == "hierarchical",
                                max_iter=configuration["max_iter"])
              for entry in configuration["agents"]}
    is_flow = data["mode"] == "crewai_flow"
    result = {"models": data["models"], "agents": configuration["agents"], "process": "flow" if is_flow else configuration["process"],
              "tasks": [], "answer": "", "partial": True, "routes": [], "planning": []}
    tasks = {}
    for entry in configuration["tasks"]:
        def completed(task_output, spec=entry):
            item = {"id": spec["id"], "name": spec["id"], "agent": spec["agent"], "description": spec["description"],
                    "context": spec["context"], "output": task_output.raw, "status": "complete"}
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
    planning_model = model(configuration.get("planning_model") or configuration["agents"][0]["model"])
    memory = native_memory(data, planning_model) if configuration.get("memory") else None
    options.update(planning=configuration.get("planning", False), planning_llm=planning_model,
                   memory=memory or False, tracing=False)
    result["memory"] = {"enabled": bool(memory), "scope": configuration.get("memory_scope", "default"),
                        "embedding": "local-lexical-hash" if memory else None}

    def execute(ids):
        chosen = [tasks[name] for name in ids]
        original = {task.name: task.description for task in chosen}
        crew = Crew(agents=list(agents.values()), tasks=chosen,
                    process=Process(configuration["process"]), verbose=False, **options)
        answer = crew.kickoff()
        if not answer.raw:
            raise RuntimeError("CrewAI 未返回协作结果")
        result["answer"] = answer.raw
        if configuration.get("planning"):
            for task in chosen:
                plan = task.description[len(original[task.name]):] if task.description.startswith(original[task.name]) else task.description
                result["planning"].append({"task_id": task.name, "plan": plan})
                emit("framework_message", role="任务规划", model=configuration.get("planning_model") or configuration["agents"][0]["model"],
                     task_id=task.name, content=plan)
        save(output, result)
        return answer.raw

    return result, tasks, execute, memory


def finish_crew(result, memory, output):
    if memory:
        memory.close()
        result["memory"]["records"] = memory.info().record_count
    result["partial"] = False
    save(output, result)


def crewai(data, output):
    result, tasks, execute, memory = crew_components(data, output)
    save(output, result)
    print(f"CrewAI 自定义团队开始：{len(result['agents'])} 个角色、{len(tasks)} 个任务", flush=True)
    try:
        execute(list(tasks))
        finish_crew(result, memory, output)
    except BaseException:
        if memory:
            memory.close()
        save(output, result)
        raise


def branch_decision(step, completed):
    """Only inspect actual prior output; no expression evaluation or code."""
    missing = [name for name in step.get("context", []) if name not in completed]
    if missing:
        return False, "依赖任务未完成：" + ", ".join(missing)
    condition = step.get("when")
    if not condition:
        return True, "无条件步骤"
    if condition["task"] not in completed:
        return False, "条件引用任务未完成"
    text = completed[condition["task"]]
    value = condition["value"]
    matched = {"contains": lambda: value in text, "not_contains": lambda: value not in text,
               "equals": lambda: value == text.strip()}[condition["operator"]]()
    return matched, "条件成立" if matched else "条件不成立"


def configured_flow(specifications, execute, result, output, memory=None):
    """Build a native CrewAI Flow of routers and explicitly joined branches."""
    from crewai.flow.flow import Flow, start, listen, router, or_
    completed = {}
    methods = {}

    def begin(self):
        return "configured-start"
    methods["begin"] = start()(begin)
    previous = "begin"

    for index, specification in enumerate(specifications):
        route_name, run_name, skip_name = f"route_{index}", f"run_{index}", f"skip_{index}"

        def route(self, prior=None, step=specification):
            matched, reason = branch_decision(step, completed)
            decision = {"task": step["id"], "decision": "run" if matched else "skip",
                        "condition": step.get("when"), "reason": reason}
            result["routes"].append(decision)
            emit("framework_task", id=step["id"], name=step["id"], status="routed", **decision)
            save(output, result)
            return ("execute:" if matched else "skip:") + step["id"]

        route.__name__ = route_name
        methods[route_name] = router(previous, emit=["execute:" + specification["id"], "skip:" + specification["id"]])(route)

        def run(self, prior=None, step=specification):
            answer = execute([step["id"]])
            completed[step["id"]] = answer
            return answer

        run.__name__ = run_name
        methods[run_name] = listen("execute:" + specification["id"])(run)

        def skip(self, prior=None, step=specification):
            decision = result["routes"][-1]
            item = {"id": step["id"], "name": step["id"], "agent": step["agent"], "context": step["context"],
                    "description": step["description"], "output": "", "status": "skipped",
                    "condition": step.get("when"), "reason": decision["reason"]}
            result["tasks"].append(item)
            emit("framework_task", **item)
            save(output, result)
            return "skipped:" + step["id"]

        skip.__name__ = skip_name
        methods[skip_name] = listen("skip:" + specification["id"])(skip)
        previous = or_(run_name, skip_name)

    def final(self, prior=None):
        return result["answer"]
    methods["final"] = listen(previous)(final)
    workflow_type = type("ModelDeskConfiguredFlow", (Flow,), methods)
    return workflow_type(tracing=False, memory=memory, max_method_calls=len(specifications) * 3 + 3)


def crewai_flow(data, output):
    result, tasks, execute, memory = crew_components(data, output)
    save(output, result)
    try:
        flow = configured_flow(data["workflow"]["tasks"], execute, result, output, memory=memory)
        flow.kickoff()
        if not result["answer"]:
            raise RuntimeError("Flow 没有产生已完成的任务结果")
        finish_crew(result, memory, output)
    except BaseException:
        if memory:
            memory.close()
        save(output, result)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    data = json.loads(args.input.read_text())
    {"camel": camel, "camel_workforce": camel_workforce, "crewai": crewai, "crewai_flow": crewai_flow}[data["mode"]](data, args.output)


if __name__ == "__main__":
    main()
