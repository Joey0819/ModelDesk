"""Identify direct Agent submissions across HTTP retries and service restarts."""

import asyncio
import copy
import hashlib
import json
import re
import uuid

from fastapi import HTTPException


def unverified_dispatch(message):
    return HTTPException(409, {"code": "dispatch_receipt_unverified", "message": message})


def scoped_task_id(kind, project_id, room_id, client_id):
    scope = json.dumps([kind, project_id, room_id, str(client_id)], separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:direct:" + scope))


def direct_task_receipt(kind, request):
    """Return a scoped task ID and a fingerprint of the caller's original intent."""
    if request.client_request_id is None:
        raise ValueError("A direct task receipt requires a client request ID")
    body = request.model_dump(mode="json", exclude={"client_request_id"})
    task_id = scoped_task_id(kind, body.get("project_id"), body.get("room_id"), request.client_request_id)
    content = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return task_id, hashlib.sha256(content).hexdigest()


def has_dispatch_receipt(kind, record):
    client_id = record.get("client_request_id")
    digest = record.get("_dispatch_request_sha256")
    return bool(client_id and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                and record["id"] == scoped_task_id(kind, record.get("project_id"), record.get("room_id"), client_id))


def replay_direct_task(kind, records, task_id, digest):
    saved = records.get(task_id)
    if saved is None:
        return None
    if not has_dispatch_receipt(kind, saved):
        raise unverified_dispatch("已找到原任务，但派发回执不完整。请先查看原任务记录。")
    if saved.get("_dispatch_request_sha256") != digest:
        raise HTTPException(409, "这次派发的回执已关联其他内容，请核对原任务。")
    return copy.deepcopy(saved)


def start_agent_worker(record, records, workers, execute, checkpoint):
    """Publish a worker only after its receipt is durable, including with eager scheduling."""
    admitted = asyncio.Event()

    async def run():
        await admitted.wait()
        await execute(record)

    runner = run()
    try:
        worker = asyncio.create_task(runner)
    except BaseException:
        runner.close()
        raise
    try:
        checkpoint()
    except BaseException:
        worker.cancel()
        raise
    records[record["id"]] = record
    workers[record["id"]] = worker
    admitted.set()
    return copy.deepcopy(record)
