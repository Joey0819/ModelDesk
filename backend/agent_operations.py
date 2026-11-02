"""Common operation receipts for supported Agent adapters.

Capabilities describe what the adapter can actually do. A saved result can be
collected without remote observation support; the two are deliberately distinct.
"""
import hashlib
import uuid

ADAPTERS = {
    "codex": {"outputs": ["text", "code", "video_with_lemo_skill"], "observe": "local_process", "cancel": "local_process"},
    "claude": {"outputs": ["text", "code"], "observe": "local_process", "cancel": "local_process"},
    "muse": {"outputs": ["text", "image", "video"], "observe": "acknowledged_native_thread", "cancel": "local_observation_only"},
    "mcp": {"outputs": ["text", "structured"], "observe": "saved_receipt_only", "cancel": "local_wait_only"},
}


def operation_receipt(job, member, prompt, result, *, observe_only=False, revision_of=None):
    kind = member.get("kind")
    capabilities = ADAPTERS.get(kind, {"outputs": [], "observe": "unsupported", "cancel": "unsupported"})
    key = f"{kind}:{member['id']}:{revision_of or 'original'}"
    # Identity does not change when a server response arrives after a restart.
    operation_id = str(uuid.uuid5(uuid.UUID(job["id"]), key))
    return {"schema_version": 1, "id": operation_id, "parent_job_id": job["id"],
            "project_id": job.get("project_id"), "room_id": job.get("group_room_id"), "member_id": member["id"],
            "adapter": kind, "capabilities": capabilities.copy(), "external_task_id": result.get("task_id"),
            "state": result.get("status", "unknown"), "mode": "collect_only" if observe_only else "execute_or_reattach",
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "output_sha256": result.get("output_sha256"), "dispatch_count": result.get("dispatch_count"),
            "unknown_result_retryable": False}
