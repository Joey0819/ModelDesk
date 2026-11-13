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
    "cancelled": "archived",
}

VALID_PLATFORMS = {"workbench", "group", "feishu"}


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
