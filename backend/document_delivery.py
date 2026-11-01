"""Durable, scoped document outputs created by the platform after collaboration.

The renderer receives an observed artifact, not model-authored paths or binary
files. A manifest binds each download to its owner, content and actual bytes.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import uuid
from urllib.parse import urlencode

from fastapi import HTTPException

from .delivery_media import (DeliveryMediaStore, DIRECTORY_FLAGS, _atomic_write,
                             _identifier, _identity, _read_file)

FORMATS = ("docx", "pdf", "html")
LAYOUTS = ("report", "article", "compact")
MAX_BYTES = 80_000_000


def document_spec(job, plan):
    """Explicit controls win; the planner reads the entire task's user ledger."""
    selected = job.get("group_document_formats")
    preferences = (job.get("_task_context") or {}).get("document_preferences") or {}
    planned = plan.get("document_formats") or []
    if selected is None:
        formats = planned if planned or plan.get("reset_document_formats") else preferences.get("formats", [])
        formats = list(dict.fromkeys(formats))
    else:
        formats = selected
    layout = job.get("group_document_layout", "auto")
    if layout == "auto":
        layout = plan.get("document_layout") or preferences.get("layout") or "report"
    if len(formats) > 3 or any(item not in FORMATS for item in formats) or layout not in LAYOUTS:
        raise ValueError("无效的交付文件格式或版式")
    return {"formats": formats, "layout": layout, "source": "user" if selected is not None else "task_plan"}


class DocumentStore:
    def __init__(self, directory=None):
        from .workbench import PROJECT
        self.files = DeliveryMediaStore(directory or PROJECT / "data/delivery-documents")

    def save(self, job, artifact_id, rendered):
        job_id, project_id = _identity(job)
        raw = rendered["content"]
        format, layout = rendered["format"], rendered["layout"]
        if not isinstance(raw, bytes):
            raise HTTPException(409, "文档内容必须为实际文件字节")
        checksum = hashlib.sha256(raw).hexdigest()
        if (not isinstance(raw, bytes) or len(raw) > MAX_BYTES or not raw
                or format not in FORMATS or layout not in LAYOUTS or rendered["sha256"] != checksum):
            raise HTTPException(409, "文档内容或格式校验失败")
        identifier = str(uuid.uuid4())
        filename = f"ModelDesk-{job_id[:8]}-{layout}.{format}"
        url = f"/api/workbench/jobs/{job_id}/documents/{identifier}"
        if project_id:
            url += "?" + urlencode({"project_id": project_id})
        entry = {"id": identifier, "job_id": job_id, "project_id": project_id,
                 "artifact_id": artifact_id, "format": format, "layout": layout,
                 "filename": filename, "media_type": rendered["media_type"],
                 "sha256": checksum, "size": len(raw), "url": url,
                 "content_sha256": rendered["source_sha256"], "status": "ready",
                 "partial": rendered["partial"], "warnings": rendered["warnings"],
                 "pages": rendered.get("pages")}
        parent = self.files._job(job_id, create=True)
        try:
            os.mkdir(identifier, mode=0o700, dir_fd=parent)
            fd = os.open(identifier, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                _atomic_write(fd, "content", raw)
                _atomic_write(fd, "manifest.json", json.dumps(entry, ensure_ascii=False).encode())
            finally:
                os.close(fd)
        finally:
            os.close(parent)
        return entry

    def read(self, job, identifier):
        job_id, project_id = _identity(job)
        identifier = _identifier(identifier)
        try:
            parent = self.files._job(job_id)
            try:
                fd = os.open(identifier, DIRECTORY_FLAGS, dir_fd=parent)
            finally:
                os.close(parent)
            try:
                entry = json.loads(_read_file(fd, "manifest.json", 65536))
                if not isinstance(entry, dict):
                    raise ValueError("invalid document manifest")
                if (entry.get("id"), entry.get("job_id"), entry.get("project_id")) != (identifier, job_id, project_id):
                    raise ValueError("document ownership changed")
                from .document_exports import FORMATS as TYPES
                format, layout = entry.get("format"), entry.get("layout")
                if (format not in TYPES or layout not in LAYOUTS or entry.get("media_type") != TYPES[format]
                        or entry.get("filename") != f"ModelDesk-{job_id[:8]}-{layout}.{format}"
                        or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("content_sha256")))):
                    raise ValueError("invalid document metadata")
                raw = _read_file(fd, "content", MAX_BYTES)
                if len(raw) != entry.get("size") or hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                    raise ValueError("document bytes changed")
                return entry, raw
            finally:
                os.close(fd)
        except FileNotFoundError:
            raise HTTPException(404, "未找到属于当前任务的文档") from None
        except (ValueError, OSError, TypeError, KeyError):
            raise HTTPException(409, "文档归属或文件完整性校验失败") from None


store = DocumentStore()


async def prepare_delivery_documents(job, delivery):
    """Package the final version without replaying any model or native task."""
    from .document_exports import render_document
    from .workbench import public_error
    spec = delivery.get("task_spec", {}).get("documents", {"formats": [], "layout": "report"})
    delivery["documents"] = []
    delivery["document_errors"] = []
    snapshot = copy.deepcopy(job)
    snapshot["status"] = "complete"
    snapshot["result"] = {"delivery": copy.deepcopy(delivery)}
    for format in spec["formats"]:
        try:
            rendered = await render_document(snapshot, "group:delivery", format, spec["layout"])
            entry = await asyncio.to_thread(store.save, job, "group:delivery", rendered)
            await asyncio.to_thread(store.read, job, entry["id"])
            delivery["documents"].append(entry)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            delivery["document_errors"].append({"format": format, "message": public_error(error)})
    # A failed requested format makes the overall delivery partial. Re-render
    # successful siblings with that final status before exposing their URLs;
    # otherwise the downloaded Word file could disagree with the task page.
    if delivery["document_errors"] and snapshot["result"]["delivery"].get("status") == "complete":
        final_source = snapshot["result"]["delivery"]
        final_source["status"] = "partial"
        final_source.setdefault("limitations", []).extend(
            item["format"].upper() + "：" + item["message"] for item in delivery["document_errors"])
        completed = delivery["documents"]
        delivery["documents"] = []
        for previous in completed:
            try:
                rendered = await render_document(snapshot, "group:delivery", previous["format"], spec["layout"])
                entry = await asyncio.to_thread(store.save, job, "group:delivery", rendered)
                await asyncio.to_thread(store.read, job, entry["id"])
                delivery["documents"].append(entry)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delivery["document_errors"].append({"format": previous["format"], "message": public_error(error)})
    return document_checks(job, delivery)


def document_checks(job, delivery):
    checks = []
    content_sha = hashlib.sha256(delivery.get("content", "").encode()).hexdigest()
    for format in delivery.get("task_spec", {}).get("documents", {}).get("formats", []):
        candidates = [entry for entry in delivery.get("documents", []) if entry.get("format") == format]
        verified = False
        for entry in candidates:
            try:
                actual, _ = store.read(job, entry["id"])
                verified = (actual["sha256"] == entry["sha256"] and actual["content_sha256"] == content_sha
                            and actual["format"] == format and actual["artifact_id"] == "group:delivery"
                            and actual["layout"] == delivery["task_spec"]["documents"].get("layout", "report"))
            except HTTPException:
                continue
            if verified:
                break
        error = next((item["message"] for item in delivery.get("document_errors", []) if item["format"] == format), "文件未生成或与当前正文不一致")
        checks.append({"requirement_id": "document-" + format, "met": verified,
                       "reason": (f"已生成并回读校验 {format.upper()} 文件" if verified else f"{format.upper()}：{error}")})
    return checks
