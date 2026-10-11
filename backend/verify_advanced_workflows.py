"""Opt-in real framework acceptance on synthetic prompts, using existing OAuth.

Run manually; never imported by unittest discovery and never reads user projects.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

from backend.workflow_config import validate_workflow

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT.parent / "llm-tools"
MODELS = ["gpt-6.1-sol", "gemini-3-flash"]


def agent(identifier, role, model=MODELS[0], tools=None):
    return {"id": identifier, "role": role, "goal": "完成明确的测试目标，并报告真实结果", "model": model,
            "backstory": "基于证据，按要求输出，不执行任何代码或访问用户文件。", "tools": tools or []}


def cases():
    team = [agent("author", "计算方案作者"), agent("verifier", "检查员", MODELS[1])]
    yield "camel", {"agents": team, "task_specify": True, "task_planning": True, "critic": True, "critic_model": MODELS[0]}, "协作说明 6×7 为什么等于 42，输出不超过三句话。"
    yield "camel_workforce", {"agents": [agent("calculator", "算术专家", tools=["math"]), team[1]], "manager_model": MODELS[0]}, "将这个小任务最多分解为两个子任务：算术专家调用 math_multiply 计算 6×7；检查员核对结果。最终报告 42，不需要任何文件或网络。"
    yield "crewai", {"agents": [team[0]], "planning": True, "planning_model": MODELS[0], "memory": True,
                     "memory_scope": "acceptance/native-memory", "tasks": [{"id": "answer", "agent": "author", "description": "说明 6×7=42，记住验收标记 ADVANCED_FIXTURE_42。", "expected_output": "包含数字42与验收标记的一句话", "context": []}]}, "本次只有通用算术示例，绝不读取用户项目。"
    yield "crewai_flow", {"agents": team, "tasks": [
        {"id": "seed", "agent": "author", "description": "只输出 YES 42", "expected_output": "YES 42", "context": []},
        {"id": "approved", "agent": "verifier", "description": "确认上步数字为42，输出 FLOW_PASS_42", "expected_output": "FLOW_PASS_42", "context": ["seed"], "when": {"task": "seed", "operator": "contains", "value": "YES"}},
        {"id": "rejected", "agent": "author", "description": "只应在上步不含YES时执行", "expected_output": "NO", "context": ["seed"], "when": {"task": "seed", "operator": "not_contains", "value": "YES"}},
    ]}, "按路由条件执行任务，条件为假必须跳过，不做额外任务。"


def run_case(item):
    mode, value, prompt = item
    workflow = validate_workflow(value, mode, MODELS).model_dump(mode="json")
    directory = ROOT / "data" / "advanced-validation"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path, output, logfile = [directory / (mode + extension) for extension in (".input.json", ".output.json", ".log")]
    path.write_text(json.dumps({"mode": mode, "workflow": workflow, "models": MODELS, "rounds": 1, "prompt": prompt}, ensure_ascii=False))
    path.chmod(0o600)
    python = TOOLS / (".venv-camel" if mode.startswith("camel") else ".venv-crewai") / "bin" / "python"
    started = time.monotonic()
    with logfile.open("w") as log:
        process = subprocess.run([str(python), "-u", str(ROOT / "backend/workers/workflow.py"), "--input", str(path), "--output", str(output)],
                                 stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, timeout=900)
    logfile.chmod(0o600)
    result = json.loads(output.read_text()) if output.exists() else {}
    events = []
    for line in logfile.read_text().splitlines():
        if line.startswith("MODELDESK_EVENT "):
            events.append(json.loads(line.removeprefix("MODELDESK_EVENT ")))
    passed = process.returncode == 0 and not result.get("partial", True)
    if mode == "camel":
        passed = passed and bool(result.get("specified_task")) and bool(result.get("planned_task")) and bool(result.get("critic", {}).get("selections"))
    elif mode == "camel_workforce":
        passed = passed and result.get("task_state") == "DONE" and bool(result.get("workforce_events")) and "42" in result.get("answer", "")
    elif mode == "crewai":
        passed = passed and bool(result.get("planning")) and result.get("memory", {}).get("records", 0) >= 1
    else:
        passed = passed and [task["id"] for task in result.get("tasks", []) if task.get("status") == "skipped"] == ["rejected"] and "FLOW_PASS_42" in result.get("answer", "")
    summary = {"mode": mode, "passed": bool(passed), "returncode": process.returncode,
               "seconds": round(time.monotonic() - started, 2), "events": len(events), "result": result}
    print(mode, "PASS" if passed else "FAIL", "events", len(events), flush=True)
    return summary


def main():
    os.umask(0o077)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_case, cases()))
    report = ROOT / "data" / "advanced-validation.json"
    report.write_text(json.dumps({"synthetic_prompts_only": True, "models": MODELS, "cases": results}, ensure_ascii=False, indent=2, allow_nan=False))
    report.chmod(0o600)
    raise SystemExit(0 if all(item["passed"] for item in results) else 1)


if __name__ == "__main__":
    main()
