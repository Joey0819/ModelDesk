"""Live platform, native SDK and model-to-tool integration acceptance."""
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

from verify_workbench import run, read, request, MODELS

BASE = "http://127.0.0.1:8001/api/platform"


def api(path, data=None):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json"}), timeout=90) as response:
        return json.load(response)


def execute(body):
    job = read("/jobs", body)
    with request(f"/jobs/{job['id']}/events") as response:
        for line in response:
            if line.startswith(b"data:"):
                event = json.loads(line[5:])
                if event["type"] in {"tool_result", "error", "complete"}:
                    print(body["mode"], event["type"], event.get("tool", ""), flush=True)
    saved = read(f"/jobs/{job['id']}")
    assert saved["status"] == "complete", saved.get("error")
    assert "token" not in {key for key in saved if key.startswith("_")} and not any(key.startswith("_") for key in saved)
    return saved


def main():
    report = {"validated_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    overview = api("/overview")
    assert overview["version"] == "2.0.0" and len(api("/tools")) == 11
    document = api("/knowledge", {"name": "平台工具验收说明", "content": "平台任务代号：研审92。\n空输入需要显式处理。\n22 × 17 = 374。", "tags": ["验收"]})
    found = api("/knowledge/search", {"query": "任务代号研审", "knowledge_ids": [document["id"]]})
    assert found and found[0]["id"] == document["id"] and found[0]["sha256"]
    memory = api("/memories", {"name": "平台记忆验收", "content": "本记忆空间代号杉松96。", "scope": "acceptance"})
    restored = api("/memories/search", {"query": "记忆空间代号", "scope": "acceptance"})
    assert any(item["id"] == memory["id"] for item in restored)
    report["checks"].update(knowledge=True, memory=True)
    mcp = next(item for item in api("/mcp") if item["url"] == "http://127.0.0.1:8371/mcp")
    discovered = api(f"/mcp/{mcp['id']}/discover", {})
    assert len(discovered["tools"]) == 2
    mcp_result = api(f"/mcp/{mcp['id']}/call", {"tool": "descriptive_statistics", "arguments": {"values": [1, 2, 3]}})
    value = mcp_result.get("structuredContent") or json.loads(next(item["text"] for item in mcp_result["content"] if item["type"] == "text"))
    assert value["mean"] == 2
    report["checks"]["mcp"] = True
    tool_job = execute({"mode": "chat", "models": [MODELS[0]], "prompt": f"请实际调用 knowledge_search 查询所选文档中的平台任务代号，再调用 math 计算22×17，最后调用 mcp_call，server_id={mcp['id']}，tool=descriptive_statistics，arguments={{\"values\":[1,2,3]}}。简短报告代号、乘积和均值，不得用心算代替工具。",
        "knowledge_ids": [document["id"]], "tools": ["knowledge_search", "math", "mcp_call"], "mcp_server_ids": [mcp["id"]], "remember": True, "memory_scope": "acceptance"})
    used = {event["tool"] for event in tool_job["events"] if event["type"] == "tool_result" and not event.get("error")}
    assert {"knowledge_search", "math", "mcp_call"} <= used
    answer = tool_job["result"]["responses"][0]["text"]
    assert "374" in answer and "研审92" in answer
    assert tool_job["context_references"][0]["id"] == document["id"]
    report["checks"]["model_tool_bridge"] = {"job_id": tool_job["id"], "tools": sorted(used)}
    sdk_jobs = []
    for template in api("/sdk/templates"):
        saved = execute({"mode": "sdk_script", "models": [MODELS[0]], "prompt": "回复验收代号原生SDK73，最多20字。", "sdk": template["sdk"], "source": template["source"], "approved": True})
        assert "原生SDK73" in json.dumps(saved["result"], ensure_ascii=False) and saved["result"]["native_script"]
        sdk_jobs.append({"sdk": template["sdk"], "id": saved["id"]})
    report["checks"]["sdk"] = sdk_jobs
    # Round-trip the actual export without changing existing resources/history.
    backup = api("/data/export?include_jobs=true")
    imported = api("/data/import", {"data": backup, "conflict": "skip"})
    assert imported["imported"] == 0 and imported["skipped"] and not imported["credentials_imported"]
    report["checks"]["migration"] = True
    report["checks"]["tools"] = True
    report["checks"]["prompts"] = bool(api("/prompts"))
    output = Path(__file__).resolve().parents[1] / "data/super-platform-validation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS super platform integration", output)


if __name__ == "__main__":
    main()
