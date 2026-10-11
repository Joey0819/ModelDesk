"""Real gateway/framework checks for configurable collaboration, not mocks."""
from datetime import datetime, timezone
import json
from pathlib import Path

from verify_workbench import read, request, MODELS


def execute(body):
    job = read("/jobs", body)
    events = []
    with request(f"/jobs/{job['id']}/events") as response:
        for line in response:
            if line.startswith(b"data:"):
                event = json.loads(line[5:])
                events.append(event)
                if event["type"] in {"framework_message", "framework_task", "tool_result", "complete", "error"}:
                    print(body["mode"], event["type"], event.get("tool", ""), flush=True)
    saved = read(f"/jobs/{job['id']}")
    assert saved["status"] == "complete", saved.get("error", saved["status"])
    return saved, events


def agent(identifier, role, model, tools):
    return {"id": identifier, "role": role, "goal": "必须通过工具核实数字，简短报告实际工具结果。",
            "backstory": "你是工具验收员，只报告实际结果。", "model": model, "tools": tools}


def verify_hierarchical():
    workflow = {"agents": [agent("analyst", "质量分析员", MODELS[1], [])],
                "tasks": [{"id": "quality", "description": "输出代号研审81，并用一句话说明代码空输入检查。",
                           "expected_output": "包含研审81的一句中文结论。", "agent": "analyst", "context": []}],
                "process": "hierarchical", "manager_model": MODELS[0], "max_iter": 3}
    saved, _ = execute({"mode": "crewai", "models": MODELS, "rounds": 1,
                        "prompt": "管理者分配一个简短的质量检查说明任务，不需要工具。", "workflow": workflow})
    assert saved["result"]["process"] == "hierarchical"
    assert "研审81" in saved["result"]["answer"]
    assert len(saved["result"]["tasks"]) == 1
    return {"job_id": saved["id"], "process": "hierarchical", "status": saved["status"], "manager_model": MODELS[0]}


def main():
    report = {"validated_at": datetime.now(timezone.utc).isoformat(), "modes": {}}
    for mode in ["camel", "crewai"]:
        workflow = {"agents": [agent("calculator", "计算核验员", MODELS[0], ["math", "text_stats"]),
                                agent("checker", "独立复核员", MODELS[1], ["text_stats"])],
                    "tasks": [], "process": "sequential", "manager_model": None, "max_iter": 3}
        prompt = '请计算 19 × 23 并通过数学工具核实，再调用 text_stats 统计文本 "alpha beta" 的字符数与单词数。回答必须包含工具结果，最多100字。不要只靠心算。'
        if mode == "crewai":
            workflow["tasks"] = [
                {"id": "calculate", "description": prompt, "expected_output": "实际数学与文本统计工具的结果。", "agent": "calculator", "context": []},
                {"id": "check", "description": "读取此前结果，用 text_stats 独立复核 alpha beta；给出数字结论。", "expected_output": "437、10字符、2单词，以及实际工具核查说明。", "agent": "checker", "context": ["calculate"]},
            ]
        saved, events = execute({"mode": mode, "prompt": prompt, "models": MODELS, "rounds": 2, "workflow": workflow})
        result = saved["result"]
        assert result["agents"][0]["role"] == "计算核验员"
        tools = [event for event in events if event["type"] == "tool_result"]
        assert any(event.get("tool") == "text_stats" for event in tools), "No actual statistics tool call"
        assert any(event.get("tool") in {"math_multiply", "arithmetic"} for event in tools), "No actual mathematics tool call"
        assert "437" in json.dumps(result, ensure_ascii=False)
        if mode == "crewai":
            assert [task["id"] for task in result["tasks"]] == ["calculate", "check"]
            assert result["tasks"][1]["context"] == ["calculate"]
        report["modes"][mode] = {"job_id": saved["id"], "status": saved["status"], "roles_verified": True,
                                  "tool_calls": len(tools), "task_context_verified": mode == "crewai"}
        print("PASS", mode, flush=True)

    saved, events = execute({"mode": "group", "models": MODELS, "rounds": 2,
        "prompt": "共同讨论验收代号松杉73。先提一个代码审查建议，下一位明确回应前一位；每次不超过60字。",
        "system_prompt": "中文简短讨论，保留任务代号。", "roles": {MODELS[0]: "代码审查员", MODELS[1]: "质量复核员"},
        "relay_prompt": "指出前一位建议中的一个可操作改进。", "context_turns": 8})
    assert len(saved["result"]["messages"]) == 4 and not saved["result"]["partial_failure"]
    followup, _ = execute({"mode": "group", "models": MODELS, "rounds": 1,
        "conversation_id": saved["conversation_id"], "prompt": "刚才的验收代号是什么？只输出代号。"})
    assert all("松杉73" in item["content"] for item in followup["result"]["messages"])
    report["modes"]["group"] = {"job_id": saved["id"], "followup_id": followup["id"],
                                "messages": 4, "status": saved["status"], "shared_topic_context_verified": True}
    report["hierarchical"] = verify_hierarchical()
    output = Path(__file__).resolve().parents[1] / "data/collaboration-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: live collaboration; saved", output)


if __name__ == "__main__":
    main()
