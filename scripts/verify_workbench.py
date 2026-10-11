"""Live acceptance checks against the installed, loopback-only ModelDesk API."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

BASE = "http://127.0.0.1:8001/api/workbench"
REPORT = Path(__file__).resolve().parents[1] / "data/workbench-validation.json"
MODELS = ["gpt-6.1-sol", "gemini-3-flash"]


def request(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    return urllib.request.urlopen(urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": "application/json"}
    ), timeout=360)


def read(path, data=None):
    with request(path, data) as response:
        return json.load(response)


def run(mode, **overrides):
    body = {"mode": mode, "prompt": "请记住本次任务代号松杉42，并用一句中文说明多模型协作的一个局限。回答尽量简短。", "models": MODELS, "rounds": 1}
    body.update(overrides)
    job = read("/jobs", body)
    events = []
    with request("/jobs/" + job["id"] + "/events") as response:
        for line in response:
            if line.startswith(b"data:"):
                event = json.loads(line[5:])
                events.append(event)
                if event["type"] in {"stage", "complete", "error", "cancelled"}:
                    print(mode, event["type"], event.get("stage", ""), flush=True)
    saved = read("/jobs/" + job["id"])
    assert saved["status"] == "complete", saved.get("error") or saved["status"]
    assert any(e["type"] == "complete" for e in events)
    assert not any(e["type"] == "error" for e in events)
    result = saved["result"]
    if mode == "chat":
        assert len(result["responses"]) == len(body["models"])
        assert all(r.get("text") and not r.get("error") for r in result["responses"])
    elif mode == "council":
        assert len(result["stage1"]) == len(result["stage2"]) == 2
        assert all(len(r["parsed_ranking"]) == 2 for r in result["stage2"])
        assert result["stage3"]["response"] and not result["metadata"]["partial_failure"]
    elif mode == "camel":
        assert len(result["messages"]) >= 2
        assert all(r["content"] for r in result["messages"])
    elif mode == "crewai":
        assert len(result["tasks"]) == 2 and result["answer"]
    replay = []
    after = max(0, events[-1]["seq"] - 1)
    with request(f"/jobs/{job['id']}/events?after={after}") as response:
        for line in response:
            if line.startswith(b"data:"):
                replay.append(json.loads(line[5:]))
    assert len(replay) == 1 and replay[0]["type"] == "complete"
    return {"job_id": job["id"], "conversation_id": saved["conversation_id"], "status": saved["status"], "event_count": len(events), "models": body["models"], "result_verified": True, "sse_replay_verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", action="append", choices=["chat", "council", "camel", "crewai"])
    args = parser.parse_args()
    modes = args.mode or ["chat", "council", "camel", "crewai"]
    status = read("/status")
    assert status["gateway"]["online"]
    assert all(status["tools"][mode]["available"] for mode in modes)
    assert {"codex", "antigravity"} <= {p["id"] for p in status["providers"] if p["authenticated"]}
    cancelled = read("/jobs", {"mode": "camel", "prompt": "讨论多个方案。", "models": MODELS, "rounds": 6})
    cancel_result = read("/jobs/" + cancelled["id"] + "/cancel", {})
    assert cancel_result["status"] == "cancelled"
    print("PASS: cancelled owned CAMEL job", flush=True)
    report = {"validated_at": datetime.now(timezone.utc).isoformat(), "cancellation_verified": True, "modes": {}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run, mode): mode for mode in modes}
        for future in as_completed(futures):
            mode = futures[future]
            report["modes"][mode] = future.result()
            print("PASS:", mode, flush=True)
    if "chat" in report["modes"]:
        previous = report["modes"]["chat"]
        followup = run("chat", models=[MODELS[0]], prompt="我刚给出的任务代号是什么？只回复代号。", conversation_id=previous["conversation_id"])
        reply = read("/jobs/" + followup["job_id"])["result"]["responses"][0]["text"]
        assert "松杉42" in reply, reply
        report["chat_context_verified"] = True
    history = read("/jobs")
    assert {item["job_id"] for item in report["modes"].values()} <= {job["id"] for job in history}
    REPORT.parent.mkdir(exist_ok=True, parents=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: live ModelDesk acceptance; saved", REPORT)


if __name__ == "__main__":
    main()
