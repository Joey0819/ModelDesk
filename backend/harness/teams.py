"""Reusable project-scoped teams; active runs keep their own immutable snapshot."""
import copy
import json
import threading
import uuid
from datetime import datetime

from fastapi import HTTPException

from ..workbench import now
from .models import RunRequest, TeamRequest
from .store import RunStore, checked_id


class TeamStore:
    def __init__(self, directory):
        self.atomic = RunStore(directory)
        self.lock = threading.RLock()

    def get(self, identifier, project_id=None, *, allow_archived=False):
        identifier = checked_id(identifier)
        project_id = checked_id(project_id) if project_id else None
        path = self.atomic.directory / (identifier + ".json")
        try:
            if path.is_symlink():
                raise ValueError("Invalid team file")
            value = json.loads(path.read_text())
            body = TeamRequest.model_validate({key: value[key] for key in TeamRequest.model_fields if key in value}).model_dump(mode="json")
            if value["id"] != identifier or value.get("schema_version") != 1 or not isinstance(value.get("archived"), bool):
                raise ValueError("Invalid team identity")
            for field in ["created_at", "updated_at"]:
                if datetime.fromisoformat(value[field]).tzinfo is None:
                    raise ValueError("Invalid team timestamp")
        except FileNotFoundError as error:
            raise HTTPException(404, "团队不存在") from error
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise HTTPException(409, "团队记录无法读取，原文件已保留") from error
        if body["project_id"] != project_id:
            raise HTTPException(403, "团队不属于当前项目")
        if value.get("archived") and not allow_archived:
            raise HTTPException(404, "团队已归档，请选择其他团队")
        return {**body, **{field: value[field] for field in ["id", "schema_version", "created_at", "updated_at", "archived"]}}

    def list(self, project_id=None, archived=False):
        items = []
        for path in self.atomic.directory.glob("*.json"):
            try:
                item = self.get(path.stem, project_id, allow_archived=True)
                if bool(item.get("archived")) == archived:
                    items.append(item)
            except HTTPException:
                continue
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def put(self, request, identifier=None):
        body = request.model_dump(mode="json")
        if body["project_id"]:
            from ..projects import store as projects
            projects.get(body["project_id"])
        with self.lock:
            previous = self.get(identifier, body["project_id"]) if identifier else None
            item = {**body, "id": previous["id"] if previous else str(uuid.uuid4()), "schema_version": 1,
                    "created_at": previous["created_at"] if previous else now(), "updated_at": now(), "archived": False}
            self.atomic.save(item)
            return copy.deepcopy(item)

    def archive(self, identifier, project_id=None):
        with self.lock:
            item = self.get(identifier, project_id, allow_archived=True)
            item.update(archived=True, updated_at=now())
            self.atomic.save(item)
            return copy.deepcopy(item)

    def resolve(self, request):
        body = request.model_dump(mode="json")
        if request.team_id:
            snapshot = self.get(str(request.team_id), body["project_id"])
            if any(member.get("kind", "model") != "model" for member in snapshot["members"]):
                raise HTTPException(422, "此协作流程仅支持模型成员；包含可执行 Agent 的团队请使用项目群")
            body["models"] = [member["model"] for member in snapshot["members"]]
            if len(body["models"]) != len(set(body["models"])):
                raise HTTPException(422, "此协作组有多位成员使用同一模型，请在项目群聊中使用；协作流程目前需要不同模型")
        elif request.members:
            snapshot = {"id": None, "name": "临时协作团队", "description": "本次明确配置的成员", "project_id": body["project_id"],
                        "members": body["members"], "created_at": now(), "updated_at": now(), "archived": False}
        else:
            snapshot = None
        for step in request.steps:
            if step.models and not set(step.models) <= set(body["models"]):
                raise HTTPException(422, "步骤模型必须来自所选团队成员")
        return RunRequest.model_validate(body), copy.deepcopy(snapshot)
