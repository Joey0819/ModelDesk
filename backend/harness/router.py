"""Thin HTTP surface for durable collaboration runs."""
import copy
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import workbench
from .manager import manager
from .models import RunRequest, ResumeRequest, TeamRequest, TEMPLATES
from .store import checked_id, public_run

router = APIRouter(prefix="/api/harness", tags=["harness"])


@router.get("/templates")
async def templates():
    return copy.deepcopy(TEMPLATES)


@router.get("/runs")
async def list_runs(project_id: str | None = None):
    project = checked_id(project_id) if project_id else None
    runs = [public_run(run) for run in manager.runs.values() if run.get("project_id") == project]
    runs.sort(key=lambda run: run["updated_at"], reverse=True)
    return [{key: value for key, value in run.items() if key not in {"events", "attachment_manifest"}} for run in runs[:100]]


@router.post("/runs", status_code=202)
async def create_run(request: RunRequest):
    try:
        resolved = manager.teams.resolve(request)
        request = resolved[0]
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        catalog = await workbench.model_catalog()
    except Exception as error:
        raise HTTPException(503, workbench.public_error(error)) from error
    if not set(request.models) <= set(catalog):
        missing = [model for model in request.models if model not in catalog]
        raise HTTPException(422, "这些成员模型尚未连接，不能启动团队：" + "、".join(missing))
    for step in request.steps:
        sdk = step.engine if step.mode == "review" else "camel" if step.mode.startswith("camel") else "crewai" if step.mode.startswith("crewai") else None
        if sdk in {"camel", "crewai"} and not (workbench.TOOLS / (".venv-" + sdk) / "bin/python").exists():
            raise HTTPException(503, f"{sdk} 独立运行环境尚未安装")
    try:
        return await manager.create(request, resolved_team=resolved)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/teams")
async def list_teams(project_id: str | None = None, archived: bool = False):
    project = checked_id(project_id) if project_id else None
    return manager.teams.list(project, archived)


@router.post("/teams", status_code=201)
async def create_team(request: TeamRequest):
    return manager.teams.put(request)


@router.get("/teams/{identifier}")
async def get_team(identifier: str, project_id: str | None = None):
    return manager.teams.get(identifier, project_id)


@router.put("/teams/{identifier}")
async def update_team(identifier: str, request: TeamRequest):
    return manager.teams.put(request, identifier)


@router.delete("/teams/{identifier}")
async def archive_team(identifier: str, project_id: str | None = None):
    return manager.teams.archive(identifier, project_id)


@router.get("/runs/{identifier}")
async def get_run(identifier: str):
    return public_run(manager.get(identifier))


@router.post("/runs/{identifier}/cancel")
async def cancel_run(identifier: str):
    return await manager.cancel(identifier)


@router.post("/runs/{identifier}/resume", status_code=202)
async def resume_run(identifier: str, request: ResumeRequest):
    return await manager.resume(identifier)


@router.post("/runs/{identifier}/approve", status_code=202)
async def approve_run(identifier: str):
    return await manager.approve(identifier)


@router.get("/runs/{identifier}/export")
async def export_run(identifier: str, format: Literal["md", "json"] = "md"):
    run = public_run(manager.get(identifier))
    if format == "json":
        content = json.dumps(run, ensure_ascii=False, indent=2)
    else:
        lines = ["# " + run["title"], "", "任务目标：" + run["goal"], "", "状态：" + run["status"], "", "任务记录：" + run["id"], ""]
        if run.get("team_snapshot"):
            team = run["team_snapshot"]
            lines.extend(["## 本次团队", "", team["name"], ""])
            lines.extend(f"- {member['name']} · {member['model']}：{member['role']}" for member in team["members"])
            lines.append("")
        for step in run["steps"]:
            lines.extend(["## " + step["title"], "", f"引擎：{step['mode']} · 状态：{step['status']}", ""])
            if step.get("output"):
                lines.extend(["来源任务：" + step["output"]["job_id"], "", step["output"]["text"], ""])
            for attempt in step["attempts"]:
                if attempt.get("error"):
                    lines.extend([f"尝试 {attempt['number']}：{attempt['error']}", ""])
        if run.get("error"):
            lines.extend(["运行提示：" + run["error"], ""])
        content = "\n".join(lines)
    return Response(content, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="ModelDesk-collaboration-{run["id"]}.{format}"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
