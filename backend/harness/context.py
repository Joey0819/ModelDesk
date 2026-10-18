"""Shared resource revision protocol for staged and automatic collaboration."""
import hashlib
import json

from fastapi import HTTPException
from ..platform_store import store as resources


def revision(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def capture_revisions(body, resource_store=None):
    store = resource_store or resources
    records = []
    with store.lock:
        for kind, field in [("knowledge", "knowledge_ids"), ("memories", "memory_ids"), ("mcp", "mcp_server_ids")]:
            for identifier in body.get(field, []):
                record = store.get(kind, identifier)
                if record.get("project_id") not in {None, body.get("project_id")}:
                    raise HTTPException(403, "所选资料不属于当前项目")
                records.append({"kind": kind, "id": identifier, "digest": revision(record)})
    return records


def validate_revisions(records, project_id, resource_store=None):
    store = resource_store or resources
    with store.lock:
        for record in records:
            current = store.get(record["kind"], record["id"])
            if current.get("project_id") not in {None, project_id}:
                raise HTTPException(403, "协作资料已移至其他项目，请创建新协作任务")
            if revision(current) != record["digest"]:
                raise HTTPException(409, "协作资料已修改，请创建新任务以确认更新后的输入")
