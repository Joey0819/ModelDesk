"""Real API acceptance for native framework jobs and scoped platform tools.

Only synthetic knowledge/memory and the pre-existing intentional defect fixture
are transmitted. Runtime capability tokens stay in process memory and are never
written into the report or printed.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8001"
PROJECT = "005f41ad-958f-40fa-a8a6-5b627b71be54"
MODELS = ["gpt-6.1-sol", "gemini-3-flash"]
SCOPE = "acceptance/runtime-" + uuid.uuid4().hex[:12]
MARKER = "平台研审88"
FORBIDDEN = {"token", "runtime", "_runtime", "_workflow_input", "_sdk_input"}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def private_keys(value):
    if isinstance(value, dict):
        return [key for key in value if key in FORBIDDEN] + [key for item in value.values() for key in private_keys(item)]
    if isinstance(value, list):
        return [key for item in value for key in private_keys(item)]
    return []


def resources(client):
    snapshots = request(client, "GET", f"/api/workbench/projects/{PROJECT}/snapshots")
    assert snapshots, "验收示例没有已保存快照"
    snapshot = next(item for item in snapshots if item["scope"] == "selected")
    full = request(client, "GET", f"/api/workbench/projects/{PROJECT}/snapshots/{snapshot['id']}")
    assert {item["path"] for item in full["files"]} == {"average.py"}
    document = request(client, "POST", "/api/platform/knowledge", json={
        "name": "验收规范", "category": "合成验收示例", "tags": ["synthetic-acceptance"],
        "content": "这是合成验收规范，不是用户资料。\n代号为平台研审88。\n明确的计算基准：22×17=374。\n引用时注明文档名称、文档ID与行范围。"})
    memory = request(client, "POST", "/api/platform/memories", json={
        "name": "验收复核约定", "scope": SCOPE, "tags": ["synthetic-acceptance"],
        "content": "平台研审88：本次只检查合成示例，复核标记 MEMORY_PLATFORM_88；不得声称已经运行项目代码。"})
    foreign_document = request(client, "POST", "/api/platform/knowledge", json={
        "name": "隔离验收文档", "category": "合成隔离示例", "tags": ["synthetic-acceptance"],
        "content": "平台研审88 FOREIGN_ISOLATION_88；此条不属于选择的知识资源。"})
    foreign_memory = request(client, "POST", "/api/platform/memories", json={
        "name": "隔离验收记忆", "scope": SCOPE + "-foreign", "tags": ["synthetic-acceptance"],
        "content": "平台研审88 FOREIGN_ISOLATION_88；此条不属于当前任务记忆空间。"})
    return {"project_id": PROJECT, "snapshot_id": snapshot["id"], "snapshot_digest": snapshot["digest"],
            "knowledge_id": document["id"], "memory_id": memory["id"], "foreign_document_id": foreign_document["id"],
            "foreign_memory_id": foreign_memory["id"], "memory_scope": SCOPE}


def workflow(mode, resource):
    primary = {"id": "reader", "role": "合成材料核查员", "model": MODELS[0],
               "goal": "真实调用指定工具核对知识、记忆与源码，不凭材料注入的答案假装调用工具。",
               "backstory": "始终保留来源与行号，明确未运行源码。",
               "tools": ["knowledge_search", "memory_read", "project_read", "memory_save", "math"]}
    verifier = {"id": "verifier", "role": "证据复核与记忆记录员", "model": MODELS[1],
                "goal": "核对来源，实际保存简洁的合成结果。", "backstory": "明确未经运行的文本评审边界。",
                "tools": ["memory_save"]}
    reading = ("必须真实调用 knowledge_search(query='平台研审88')、memory_read(query='平台研审88')、"
               "project_read(path='average.py',start_line=4,end_line=6)，再调用数学工具核算22乘17。"
               "禁止只依据已注入材料声称调用过工具。基于返回结果报告代号、374、MEMORY_PLATFORM_88，"
               "说明average.py第6行对空列表存在除零风险且未执行源码。写出知识文档ID、行范围与项目行号。")
    saving = "实际调用 memory_save 保存含 PLATFORM_RAG_374、平台研审88、374、average.py:6 的简要结果；最终回答保留这些标记和来源。"
    team = {"agents": [primary, verifier], "max_iter": 8, "process": "sequential",
            "knowledge_ids": [resource["knowledge_id"]], "memory_scope": SCOPE,
            "project_id": PROJECT, "snapshot_id": resource["snapshot_id"], "tasks": [
                {"id": "read", "agent": "reader", "description": reading,
                 "expected_output": "知识ID与行范围、记忆标记、源码行6风险、374的核算结果", "context": []},
                {"id": "save", "agent": "verifier", "description": saving,
                 "expected_output": "包含PLATFORM_RAG_374的已保存结果及完整来源", "context": ["read"]}]}
    if mode == "camel_workforce":
        team["manager_model"] = MODELS[0]
    else:
        team["tasks"][1]["when"] = {"task": "read", "operator": "contains", "value": "374"}
        team["tasks"].append({"id": "negative", "agent": "verifier", "description": "只有read结果不含374时才输出FAILED",
                              "expected_output": "FAILED", "context": ["read"],
                              "when": {"task": "read", "operator": "not_contains", "value": "374"}})
    return team


def check_runtime(client, token, resource):
    # Supplying alternate context fields must not expand the stored capability.
    denied = client.post("/api/platform/runtime/tools/web_fetch", json={"token": token, "arguments": {"url": "https://example.com"}})
    assert denied.status_code == 403, "未选择工具不应获得调用权限"
    denied_file = client.post("/api/platform/runtime/tools/project_read", json={"token": token, "arguments": {"path": ".env"}})
    assert denied_file.status_code == 404, "快照外或已排除文件不应被读取"
    scoped_knowledge = request(client, "POST", "/api/platform/runtime/tools/knowledge_search", json={
        "token": token, "arguments": {"query": MARKER}, "knowledge_ids": [resource["foreign_document_id"]]})
    assert scoped_knowledge["result"] and all(item["id"] == resource["knowledge_id"] for item in scoped_knowledge["result"])
    scoped_memory = request(client, "POST", "/api/platform/runtime/tools/memory_read", json={
        "token": token, "arguments": {"query": MARKER}, "memory_scope": SCOPE + "-foreign"})
    assert scoped_memory["result"] and all(item["scope"] == SCOPE for item in scoped_memory["result"])
    return {"unselected_tool": denied.status_code, "excluded_file": denied_file.status_code,
            "knowledge_override_ignored": True, "memory_override_ignored": True}


def run_case(mode, resource):
    started = time.monotonic()
    with httpx.Client(base_url=BASE, timeout=30) as client:
        job = request(client, "POST", "/api/workbench/jobs", json={
            "mode": mode, "prompt": "合成平台验收：核对平台研审88、22×17与avg空列表风险，必须执行任务中声明的工具读取和保存步骤。",
            "models": MODELS, "rounds": 1, "workflow": workflow(mode, resource),
            "knowledge_ids": [resource["knowledge_id"]], "memory_ids": [resource["memory_id"]],
            "memory_scope": SCOPE, "project_id": PROJECT, "snapshot_id": resource["snapshot_id"]})
        print(mode, "started", job["id"], flush=True)
        token = None
        security = None
        input_path = ROOT / "data/workbench" / (job["id"] + ".workflow-input")
        while time.monotonic() - started < 900:
            if token is None and input_path.exists():
                payload = json.loads(input_path.read_text())
                token = payload.get("runtime", {}).get("token")
                assert input_path.stat().st_mode & 0o777 == 0o600
                if token:
                    security = check_runtime(client, token, resource)
            job = request(client, "GET", "/api/workbench/jobs/" + job["id"])
            assert not private_keys(job), "公开任务包含私有运行字段"
            if token:
                assert token not in json.dumps(job, ensure_ascii=False), "运行令牌泄漏到公开任务或事件"
            if job["status"] in {"complete", "error", "cancelled", "interrupted"}:
                break
            time.sleep(0.4)
        assert job["status"] == "complete", f"{mode} 未成功完成：{job.get('error')}"
        assert security and token, "未观察到权限边界验收"
        replay = client.get("/api/workbench/jobs/" + job["id"] + "/events")
        assert replay.status_code == 200 and token not in replay.text
        assert not input_path.exists(), "私有工作流输入未清理"
        expired = client.post("/api/platform/runtime/tools/memory_read", json={"token": token, "arguments": {}})
        assert expired.status_code == 403, "已结束任务的授权应撤销"
        security.update(terminal_token_revoked=True, token_not_public=True, private_input_cleaned=True)
        token = None
        result = job["result"]
        answer = result.get("answer", "")
        assert "374" in answer and MARKER in answer and "average.py" in answer
        references = job.get("context_references", [])
        assert any(item.get("kind") == "knowledge" and item.get("id") == resource["knowledge_id"] and item.get("start_line") for item in references)
        assert any(item.get("kind") == "project" and item.get("snapshot_id") == resource["snapshot_id"] for item in references)
        tools = [event["tool"] for event in job["events"] if event.get("type") == "tool_result"]
        assert {"knowledge_search", "memory_read", "project_read", "memory_save"} <= set(tools)
        # Each positive boundary probe emits exactly one knowledge/memory read;
        # require another successful call for each from the framework itself.
        assert tools.count("knowledge_search") >= 2 and tools.count("memory_read") >= 2
        assert any(event.get("type") == "tool_result" and event.get("tool") in {"arithmetic", "math_multiply"}
                   and "374" in str(event.get("output")) for event in job["events"])
        assert resource["knowledge_id"] in json.dumps(result, ensure_ascii=False)
        memories = request(client, "GET", "/api/platform/memories")
        saved = [item for item in memories if item["scope"] == SCOPE and item.get("provenance", {}).get("job_id") == job["id"]]
        assert any("PLATFORM_RAG_374" in item["content"] for item in saved)
        if mode == "crewai_flow":
            assert any(item["id"] == "negative" and item.get("status") == "skipped" for item in result["tasks"])
        summary = {"mode": mode, "job_id": job["id"], "passed": True, "seconds": round(time.monotonic()-started, 2),
                   "tools": tools, "context_references": references, "security": security,
                   "saved_memory_ids": [item["id"] for item in saved], "result": result}
        summary["actual_framework_calls_verified"] = True
        print(mode, "PASS", "tools", sorted(set(tools)), flush=True)
        return summary


def main():
    os.umask(0o077)
    with httpx.Client(base_url=BASE, timeout=30) as client:
        resource = resources(client)
    report = {"synthetic_resources_only": True, "resource": resource, "models": MODELS, "cases": []}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_case, mode, resource): mode for mode in ["camel_workforce", "crewai_flow"]}
        for future in as_completed(futures):
            try:
                report["cases"].append(future.result())
            except Exception as error:
                report["cases"].append({"mode": futures[future], "passed": False, "error": str(error)})
    destination = ROOT / "data/platform-framework-validation.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    destination.chmod(0o600)
    passed = len(report["cases"]) == 2 and all(item["passed"] for item in report["cases"])
    print("Platform framework acceptance", "PASS" if passed else "FAIL", report.get("error", ""), flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
