"""Real, bounded project/vision acceptance using only self-owned synthetic files.

This calls the existing local OAuth gateway through actual workbench jobs. The
visual answer is absent from every model prompt. Reports and inputs stay in the
ignored data directory; no credentials or runtime capabilities are recorded.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
import uuid
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data/project-input-acceptance"
MODELS = ["gpt-6.1-sol", "gemini-3.8-flash-high"]
MODES = ["chat", "council", "camel", "crewai", "group", "camel_workforce", "crewai_flow", "review", "sdk_script"]
PROMPT = ("这是自建合成材料验收。实际查看本轮附图，列出从左到右的三个形状和颜色，"
          "并准确抄写图下方的五位字母数字。再从项目共用的文稿中抄写材料代号。"
          "简短用中文回答；无法读取时明确说明，不能猜测。")
TERMINAL = {"complete", "error", "cancelled", "interrupted"}
PRIVATE = {"_attachments", "_runtime", "_workflow_input", "_sdk_input", "token"}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def check_public(value):
    if isinstance(value, dict):
        assert not (PRIVATE & set(value)), "public response includes private keys"
        for item in value.values():
            check_public(item)
    elif isinstance(value, list):
        for item in value:
            check_public(item)
    elif isinstance(value, str):
        assert "data:image/" not in value and ";base64," not in value, "image payload leaked"
        assert "attachment-inputs/" not in value, "private input-copy path leaked"


def fixtures():
    from PIL import Image, ImageDraw, ImageFont
    from docx import Document
    folder = DATA / "inputs"
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (720, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 80, 190, 220), fill="red")
    draw.ellipse((280, 80, 420, 220), fill="blue")
    draw.polygon([(580, 60), (510, 220), (650, 220)], fill="green")
    draw.text((280, 300), "T6R37", fill="black", font=ImageFont.load_default(size=36))
    image.save(folder / "visual.png")
    (folder / "brief.md").write_text("这是合成验收文稿，材料代号为文稿2468。\n", encoding="utf-8")
    (folder / "source.py").write_text("def average(values):\n    # Synthetic review fixture: empty input is not guarded.\n    total = sum(values)\n    return total / len(values)\n", encoding="utf-8")
    (folder / "reference").mkdir(exist_ok=True)
    (folder / "reference/notes.txt").write_text("合成文件夹标记 FOLDER-N4T88\n", encoding="utf-8")
    document = Document()
    document.add_paragraph("Synthetic document marker: DOCX-C9P52")
    document.save(folder / "document.docx")
    return folder


def upload(client, project_id, path, shared=False):
    with path.open("rb") as stream:
        return request(client, "POST", f"/api/workbench/projects/{project_id}/assets",
                       files={"file": (path.name, stream)}, data={"shared": str(shared).lower()})


def resources(client, folder):
    tag = uuid.uuid4().hex[:8]
    project = request(client, "POST", "/api/workbench/projects", json={
        "name": "项目图片验收 " + tag, "description": "仅自建合成素材，无用户文件", "instructions": "明确区分读取到的内容和推断。"})
    assert project["managed"] and not (Path(project["path"]) / ".git").exists()
    other = request(client, "POST", "/api/workbench/projects", json={"name": "隔离项目 " + tag})
    image = upload(client, project["id"], folder / "visual.png")
    brief = upload(client, project["id"], folder / "brief.md", shared=True)
    docx = upload(client, project["id"], folder / "document.docx")
    assert "DOCX-C9P52" in docx["text_preview"]
    assert image.get("preview_url") and image["kind"] == "image"
    linked = request(client, "POST", f"/api/workbench/projects/{project['id']}/folders", json={"path": str(folder), "name": "自建素材文件夹"})
    inventory = request(client, "GET", f"/api/workbench/projects/{project['id']}/folders/{linked['id']}/files")
    imported = request(client, "POST", f"/api/workbench/projects/{project['id']}/folders/{linked['id']}/import",
                       json={"files": ["reference/notes.txt"], "shared": False})
    assert len(imported["assets"]) == 1 and "FOLDER-N4T88" in imported["assets"][0]["text_preview"]
    existing = request(client, "GET", "/api/workbench/projects")
    review = next((item for item in existing if item["path"] == str(folder.resolve())), None)
    if not review:
        review = request(client, "POST", "/api/workbench/projects", json={"name": "图片代码评审 " + tag, "path": str(folder)})
    review_image = upload(client, review["id"], folder / "visual.png")
    upload(client, review["id"], folder / "brief.md", shared=True)
    snapshot = request(client, "POST", f"/api/workbench/projects/{review['id']}/snapshots", json={"scope": "selected", "files": ["source.py"]})
    assert [item["path"] for item in snapshot["files"]] == ["source.py"]
    denied = client.post("/api/workbench/jobs", json={"mode": "chat", "prompt": "隔离检查", "models": [MODELS[0]],
                        "project_id": other["id"], "attachment_ids": [image["id"]]})
    assert denied.status_code in {403, 404, 422}, "cross-project attachment accepted"
    return {"project_id": project["id"], "other_project_id": other["id"], "image_id": image["id"], "brief_id": brief["id"],
            "docx_id": docx["id"], "folder_id": linked["id"], "folder_coverage": inventory["coverage"],
            "review_project_id": review["id"], "review_image_id": review_image["id"], "snapshot_id": snapshot["id"],
            "cross_project_denied": denied.status_code}


def workflow(mode):
    agents = [{"id": "reader", "role": "图片与文稿核查员", "goal": "实际读取图片与文稿后简短回答", "model": MODELS[0], "tools": []}]
    if mode == "camel":
        agents.append({"id": "verifier", "role": "材料复核员", "goal": "核对真实图片与文稿，不能猜测", "model": MODELS[1], "tools": []})
    result = {"agents": agents, "tasks": [], "max_iter": 3, "process": "sequential"}
    if mode != "camel":
        result["tasks"] = [{"id": "inspect", "agent": "reader", "description": PROMPT,
                            "expected_output": "三个形状与颜色、图中五位文字、文稿的材料代号", "context": []}]
    if mode == "camel_workforce":
        result["manager_model"] = MODELS[0]
    return result


def poll(client, job, timeout):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        job = request(client, "GET", "/api/workbench/jobs/" + job["id"])
        check_public(job)
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.5)
    client.post("/api/workbench/jobs/" + job["id"] + "/cancel")
    raise AssertionError("acceptance job exceeded bounded timeout")


def run_case(base, mode, resource, timeout):
    from backend.conversations import deliverables
    start = time.monotonic()
    payload = {"mode": mode, "prompt": PROMPT, "models": MODELS, "rounds": 1, "conversation_id": str(uuid.uuid4()),
               "project_id": resource["project_id"], "attachment_ids": [resource["image_id"]]}
    if mode in {"camel", "crewai", "camel_workforce", "crewai_flow"}:
        payload["workflow"] = workflow(mode)
    if mode == "review":
        payload.update(project_id=resource["review_project_id"], snapshot_id=resource["snapshot_id"],
                       attachment_ids=[resource["review_image_id"]], engine="chat", models=[MODELS[0]],
                       prompt=PROMPT + " 同时评审source.py：指出真实缺陷、文件行号与证据；图中文字可写入总结。")
    elif mode == "sdk_script":
        payload.update(sdk="camel", approved=True, models=[MODELS[0]], source=(
            'from camel.agents import ChatAgent\n'
            'agent = ChatAgent("依据实际图片与文稿回答，不能猜测。", model=CAMEL_MODEL(MODELS[0]))\n'
            'reply = agent.step(PROMPT + "\\n" + CONTEXT)\n'
            'RESULT = {"answer": reply.msgs[0].content, "sdk": "camel"}\n'))
    with httpx.Client(base_url=base, timeout=45) as client:
        path = "/api/workbench/jobs"
        if mode == "review":
            path = f"/api/workbench/projects/{payload['project_id']}/reviews"
            payload = {key: value for key, value in payload.items() if key not in {"mode", "project_id"}}
        job = request(client, "POST", path, json=payload)
        print(mode, "started", job["id"], flush=True)
        job = poll(client, job, timeout)
        report = {"mode": mode, "job_id": job["id"], "conversation_id": payload["conversation_id"],
                  "seconds": round(time.monotonic() - start, 2), "status": job["status"],
                  "result": job.get("result"), "error": job.get("error"), "context_references": job.get("context_references", [])}
        if mode == "review":
            observed = job.get("result", {}).get("raw", {}).get("responses", [])
            raw = "\n".join(item.get("text", "") for item in observed)
        else:
            outputs, _ = deliverables(job)
            raw = "\n".join(item["content"] for item in outputs)
        report["observed_delivery"] = raw
        report["visual_code_observed"] = "T6R37" in raw
        report["shared_text_observed"] = "文稿2468" in raw
        report["visual_shapes_observed"] = all(color in raw and any(shape in raw for shape in shapes) for color, shapes in [
            ("红", ["正方", "方块", "方形"]), ("蓝", ["圆"]), ("绿", ["三角"])])
        if mode == "review":
            findings = job.get("result", {}).get("findings", [])
            report["code_reference_observed"] = any(item.get("file") == "source.py" and item.get("line_start") == 4 and "len(values)" in item.get("evidence", "") for item in findings)
        export = client.get("/api/workbench/jobs/" + job["id"] + "/export", params={"format": "json"})
        export.raise_for_status()
        check_public(export.json())
        report["public_export_clean"] = True
        report["passed"] = job["status"] == "complete" and report["visual_code_observed"] and report["shared_text_observed"] and report["visual_shapes_observed"] and report.get("code_reference_observed", True)
        print(mode, "PASS" if report["passed"] else "FAIL", job.get("error") or "", flush=True)
        return report


def history_checks(base, resource, chat, timeout):
    from backend.conversations import deliverables
    with httpx.Client(base_url=base, timeout=45) as client:
        payload = {"mode": "chat", "prompt": "重新实际查看上一轮的图片，写出下方五位字母数字与从左到右的形状和颜色。", "models": [MODELS[0]],
                   "conversation_id": chat["conversation_id"], "project_id": resource["project_id"], "attachment_ids": []}
        job = poll(client, request(client, "POST", "/api/workbench/jobs", json=payload), timeout)
        outputs, _ = deliverables(job)
        assert job["status"] == "complete" and "T6R37" in "\n".join(item["content"] for item in outputs)
        references = job.get("context_references", [])
        assert any(item.get("source_job_id") == chat["job_id"] for item in references), "history image provenance missing"
        payload.update(project_id=resource["other_project_id"], prompt="如果本会话没有图片或历史，只回答没有图片历史。")
        isolated = poll(client, request(client, "POST", "/api/workbench/jobs", json=payload), timeout)
        assert isolated["status"] == "complete" and "T6R37" not in json.dumps(isolated["result"], ensure_ascii=False)
        assert not isolated.get("attachments")
        assert not any(item.get("kind") in {"attachment", "history_attachment", "image", "text"} for item in isolated.get("context_references", []))
        return {"passed": True, "history_job_id": job["id"], "isolated_job_id": isolated["id"], "context_references": references}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    os.umask(0o077)
    folder = fixtures()
    with httpx.Client(base_url=args.base, timeout=45) as client:
        resource = resources(client, folder)
    report = {"synthetic_only": True, "not_user_project_execution": True, "models": MODELS, "resources": resource, "cases": []}
    destination = DATA / "platform-validation.json"
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, args.base, mode, resource, args.timeout): mode for mode in args.modes}
        for future in as_completed(futures):
            try:
                report["cases"].append(future.result())
            except Exception as error:
                report["cases"].append({"mode": futures[future], "passed": False, "error": str(error)[:1000]})
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    chat = next((case for case in report["cases"] if case["mode"] == "chat" and case["passed"]), None)
    if chat:
        try:
            report["history"] = history_checks(args.base, resource, chat, args.timeout)
        except Exception as error:
            report["history"] = {"passed": False, "error": str(error)[:1000]}
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    passed = all(case["passed"] for case in report["cases"]) and report.get("history", {"passed": True})["passed"]
    print("Project inputs acceptance", "PASS" if passed else "FAIL", "report", destination, flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
