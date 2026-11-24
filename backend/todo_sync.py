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
    "dispatching": "doing",
    "running": "doing",
    "error": "blocked",
    "interrupted": "blocked",
    "waiting_user": "blocked",
    "uncertain": "blocked",
    "stopped_monitoring": "blocked",
    "complete": "done",
    "blocked": "blocked",
    "review": "review",
    "cancelled": "archived",
}

VALID_PLATFORMS = {"workbench", "group", "muse", "feishu", "fleet"}


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


def _board_card(platform, task_id, *, title, status, detail="", source_label="", project_id=None,
                room_id=None, result_ref=None, source_active=None):
    external_source = _validate_external_source(platform, task_id)
    label = source_label or platform
    title = title.strip() if isinstance(title, str) else ""
    return {
        "external_source": external_source,
        "title": title[:200] or f"[{label}] {task_id}",
        "status": STATUS_MAP.get(status, "todo"),
        "description": (detail or "")[:4000],
        "source_label": label,
        "project_id": project_id,
        "room_id": room_id,
        "result_ref": result_ref,
        "source_active": source_active,
    }


def sync_task(
    platform: str,
    task_id: str,
    *,
    title: str,
    status: str,
    detail: str = "",
    source_label: str = "",
    project_id: Optional[str] = None,
    room_id: str | None = None,
    result_ref: dict | None = None,
    source_active: bool | None = None,
) -> bool:
    """Mirror a saved task and report whether it reached the board."""
    try:
        from .todo_board import upsert_external_task, archive_external_task

        card = _board_card(platform, task_id, title=title, status=status, detail=detail, source_label=source_label,
                           project_id=project_id, room_id=room_id, result_ref=result_ref, source_active=source_active)
        if status not in STATUS_MAP:
            logger.warning("todo_sync: unknown status %r for %s/%s, defaulting to 'todo'", status, platform, task_id)
        if card["status"] == "archived":
            archive_external_task(card["external_source"])
            return True
        return bool(upsert_external_task(**card))
    except Exception:
        logger.exception("todo_sync: failed to sync task %s/%s", platform, task_id)
        return False


def workbench_source_active(job):
    if job.get("status") in {"queued", "running"}:
        return True
    if job.get("status") == "cancelled" or job.get("mode") != "group":
        return False
    autonomy = job.get("autonomy") or {}
    if autonomy.get("enabled") and autonomy.get("state") in {"running", "retry_wait"}:
        return True
    result = job.get("result")
    delivery = (result.get("delivery") or {}) if isinstance(result, dict) else {}
    return any(
        ((step.get("task_observation") or {}).get("remote_task") or {}).get("status") in {"working", "input_required"}
        for step in delivery.get("steps", [])
    )


def workbench_card(job, status, detail):
    from .result_refs import workbench_result_ref

    ref = workbench_result_ref(job)
    external_source = job.get("external_source")
    if isinstance(external_source, dict) and external_source.get("platform") == "feishu":
        label = "飞书"
    else:
        label = "群聊任务" if ref["room_id"] else "工作台"
    return {
        "title": job.get("prompt") or f"Job {job['id']}",
        "status": status,
        "detail": detail,
        "source_label": label,
        "project_id": ref["project_id"],
        "room_id": ref["room_id"],
        "result_ref": ref,
        "source_active": workbench_source_active(job),
    }


def group_card(task):
    from .result_refs import group_task_result_ref

    ref = group_task_result_ref(task)
    return {
        "title": task.get("title") or task.get("prompt") or f"Task {task['id']}",
        "status": task.get("status", "queued"),
        "detail": f"Kind: {task.get('kind', 'unknown')}\nEngine: {task.get('engine', 'unknown')}\nID: {task['id']}",
        "source_label": "协作任务",
        "project_id": ref["project_id"],
        "room_id": ref["room_id"],
        "result_ref": ref,
    }


def muse_card(task):
    """Mirror a saved Muse run without exposing its response or native metadata."""
    return {
        "title": task.get("prompt") or "Muse 任务",
        "status": task["status"],
        "detail": f"Muse 原生任务编号：{task['id']}",
        "source_label": "Muse",
        "project_id": task.get("project_id"),
        "room_id": task.get("room_id"),
        "source_active": task.get("monitoring"),
    }


def backfill_room_tasks(platform, records):
    """Recover older scoped work through existing records with a verified location."""
    if not records:
        return
    from .groups import store as rooms
    from .projects import store as projects
    from .todo_board import backfill_external_tasks

    registered = projects.read_registry()
    scopes = {(room["project_id"], room["id"]) for room in rooms.catalog(registered)}
    cards = []
    for record in sorted(records.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True):
        status = record.get("status")
        room_id = record.get("group_room_id" if platform == "workbench" else "room_id")
        project_id = record.get("project_id")
        if not isinstance(status, str) or status not in STATUS_MAP:
            continue
        if any(value is not None and not isinstance(value, str) for value in (room_id, project_id)):
            continue
        if (project_id, room_id) not in scopes and not (platform == "muse" and room_id is None and project_id in registered):
            continue
        if platform == "workbench":
            if record.get("mode") != "group" or not room_id or record.get("conversation_id") != room_id:
                continue
            details = workbench_card(record, *workbench_card_state(record))
        elif platform == "group":
            details = group_card(record)
        else:
            details = muse_card(record)
        cards.append(_board_card(platform, record["id"], **details))
    backfill_external_tasks(cards)


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
