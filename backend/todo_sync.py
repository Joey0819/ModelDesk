"""Single-way mirror sync from task systems (workbench, group) into the todo kanban board.

This module is the ONLY entry point for syncing external tasks to the board.
It provides idempotent upsert and archive functions that never raise exceptions
to callers - failures are logged but do not affect the source task systems.
"""

import logging
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# Status mapping: workbench/group status -> kanban status
STATUS_MAP = {
    "queued": "todo",
    "running": "doing",
    "error": "blocked",
    "interrupted": "blocked",
    "complete": "done",
    "blocked": "blocked",
    "review": "review",
    "cancelled": "archived",
}

VALID_PLATFORMS = {"workbench", "group", "feishu", "fleet"}


def workbench_card_state(job: dict) -> tuple[str, str]:
    """Use persisted automatic delivery outcomes after the runner finishes."""
    status = job.get("status", "queued")
    detail = f"模式：{job.get('mode', 'unknown')}\n任务编号：{job['id']}"
    if status != "complete" or job.get("mode") != "group" or job.get("group_delivery_mode") != "auto":
        return status, detail

    result = job.get("result")
    delivery = result.get("delivery") if isinstance(result, dict) else None
    partial_detail = f"仅有部分交付，请查看现有成果和缺项。\n{detail}"
    if not isinstance(delivery, dict):
        if isinstance(result, dict) and result.get("partial_failure") is True:
            return "review", partial_detail
        return status, detail
    validation = delivery.get("validation")
    review = validation.get("status") if isinstance(validation, dict) else None
    if delivery.get("status") == "blocked":
        return "blocked", f"自动交付受阻，请查看运行记录。\n{detail}"
    if review == "needs_revision":
        return "review", f"自动交付未通过验收，请查看审查意见。\n{detail}"
    if review == "unavailable":
        return "review", f"自动交付尚未完成验收，请查看运行记录。\n{detail}"
    if delivery.get("status") == "partial":
        return "review", partial_detail
    return status, detail


def _validate_external_source(platform: str, task_id: str) -> dict:
    """Validate and return normalized external_source dict."""
    if not platform or not isinstance(platform, str):
        raise ValueError("platform must be a non-empty string")
    if not task_id or not isinstance(task_id, str):
        raise ValueError("task_id must be a non-empty string")
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(VALID_PLATFORMS)}, got {platform!r}")
    if len(platform) > 50:
        raise ValueError("platform exceeds 50 characters")
    if len(task_id) > 200:
        raise ValueError("task_id exceeds 200 characters")
    return {"platform": platform, "task_id": task_id}


def sync_task(
    platform: str,
    task_id: str,
    *,
    title: str,
    status: str,
    detail: str = "",
    source_label: str = "",
    project_id: Optional[str] = None,
    result_ref: dict | None = None,
) -> None:
    """Idempotent upsert: create or update a kanban card from an external task.

    Args:
        platform: Source system identifier (workbench/group/feishu).
        task_id: Unique task ID within the source system.
        title: Card title (truncated to 200 chars).
        status: Source system status; mapped via STATUS_MAP.
        detail: Optional description/detail text.
        source_label: Display label for the source (e.g. "飞书", "工作台").
        project_id: Optional project UUID to scope the card.

    This function NEVER raises. All errors are caught and logged.
    """
    try:
        # Validate inputs
        ext_src = _validate_external_source(platform, task_id)

        # Map status
        kanban_status = STATUS_MAP.get(status)
        if kanban_status is None:
            logger.warning(
                "todo_sync: unknown status %r for %s/%s, defaulting to 'todo'",
                status, platform, task_id,
            )
            kanban_status = "todo"

        # Handle archived (soft delete) for cancelled
        should_archive = kanban_status == "archived"
        if should_archive:
            kanban_status = "done"  # status field value before archiving

        # Truncate title
        title = (title or "").strip()[:200]
        if not title:
            title = f"[{source_label or platform}] {task_id}"

        # Truncate detail
        detail = (detail or "")[:4000]

        # Import here to avoid circular imports at module level
        from .todo_board import upsert_external_task, archive_external_task

        if should_archive:
            archive_external_task(ext_src)
        else:
            upsert_external_task(
                external_source=ext_src,
                title=title,
                status=kanban_status,
                description=detail,
                source_label=source_label or platform,
                project_id=project_id,
                result_ref=result_ref,
            )
    except Exception:
        logger.exception(
            "todo_sync: failed to sync task %s/%s", platform, task_id,
        )


def archive_task(platform: str, task_id: str) -> None:
    """Archive (soft-delete) a synced card by its external source key.

    This function NEVER raises. All errors are caught and logged.
    """
    try:
        ext_src = _validate_external_source(platform, task_id)
        from .todo_board import archive_external_task
        archive_external_task(ext_src)
    except Exception:
        logger.exception(
            "todo_sync: failed to archive task %s/%s", platform, task_id,
        )


def reverse_sync_task(platform: str, task_id: str, target_status: str, is_archived: bool = False) -> dict:
    """Reverse sync: action from Kanban board back to originating task system.

    Never raises exceptions. Logs errors and returns diagnostic status.
    """
    if not platform or not task_id:
        return {"action": "none", "reason": "invalid_source"}
    try:
        if is_archived or target_status == "archived":
            if platform == "fleet":
                from .fleet import store as fleet_store
                fleet_store.cancel_job(task_id)
                return {"action": "cancelled", "platform": platform, "task_id": task_id}
            elif platform == "workbench":
                from .workbench import manager as wb_manager
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(wb_manager.cancel(task_id))
                except RuntimeError:
                    try:
                        asyncio.run(wb_manager.cancel(task_id))
                    except Exception:
                        pass
                return {"action": "cancelled", "platform": platform, "task_id": task_id}
    except Exception as exc:
        logger.warning("reverse_sync_task failed for %s/%s to status %s: %s", platform, task_id, target_status, exc)
        return {"action": "error", "error": str(exc)}
    return {"action": "none"}
