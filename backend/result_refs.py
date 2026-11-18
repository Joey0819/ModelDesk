"""Identifiers for reopening saved results through their existing scoped APIs."""
from fastapi import HTTPException

from .harness.store import checked_id


def workbench_result_ref(job):
    return {"kind": "workbench", "id": job.get("id"), "project_id": job.get("project_id"),
            "room_id": job.get("group_room_id"), "source_run_id": None}


def group_task_result_ref(task):
    return {"kind": "group_agent", "id": task.get("id"), "project_id": task.get("project_id"),
            "room_id": task.get("room_id"), "source_run_id": task.get("source_run_id") or task.get("parent_job_id")}


def card_result_ref(card, ref):
    """Keep source metadata off cards whose editable project differs from the run."""
    if not ref or not ref.get("id"):
        return None
    source = card.get("external_source") or {}
    kind = {"workbench": "workbench", "group": "group_agent"}.get(source.get("platform"))
    if not kind or (kind, source.get("task_id"), card.get("project_id")) != (
            ref.get("kind"), ref.get("id"), ref.get("project_id")):
        return None
    if kind == "group_agent" and not (ref.get("project_id") and ref.get("room_id")):
        return None
    try:
        return {"kind": kind, **{field: checked_id(ref[field]) if ref.get(field) else None
                                for field in ("id", "project_id", "room_id", "source_run_id")}}
    except (HTTPException, TypeError, ValueError):
        return None
