"""One authoritative local checkpoint store; no implicit replay on restart."""
import copy
import json
import math
import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from .models import RunRequest, StepConfig, TeamRequest


class RunStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, run):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / (str(UUID(run["id"])) + ".json")
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(run, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def load(self):
        if not self.directory.exists():
            return []
        runs = []
        for path in self.directory.glob("*.json"):
            try:
                value = json.loads(path.read_text())
                if str(UUID(value["id"])) != path.stem or value.get("schema_version") != 1:
                    continue
                if not isinstance(value.get("steps"), list) or not isinstance(value.get("request"), dict):
                    continue
                value["request"] = RunRequest.model_validate(value["request"]).model_dump(mode="json")
                for field in ["title", "goal", "created_at", "updated_at"]:
                    if not isinstance(value.get(field), str):
                        raise ValueError("Missing run metadata")
                for field in ["created_at", "updated_at"]:
                    if datetime.fromisoformat(value[field]).tzinfo is None:
                        raise ValueError("Invalid run timestamp")
                if value.get("project_id") != value["request"]["project_id"]:
                    raise ValueError("Run project does not match request")
                snapshot = value.get("team_snapshot")
                if snapshot is not None:
                    team = TeamRequest.model_validate({key: snapshot[key] for key in TeamRequest.model_fields if key in snapshot}).model_dump(mode="json")
                    if team["project_id"] != value["project_id"] or [member["model"] for member in team["members"]] != value["request"]["models"]:
                        raise ValueError("Team snapshot does not match run")
                    if snapshot.get("id") != value["request"].get("team_id"):
                        raise ValueError("Team identity does not match run")
                    if value["request"].get("members") and team["members"] != value["request"]["members"]:
                        raise ValueError("Temporary members do not match run")
                elif value["request"].get("team_id") or value["request"].get("members"):
                    raise ValueError("Missing immutable team snapshot")
                if value.get("status") not in {"queued", "running", "waiting_review", "complete", "error", "cancelled", "interrupted"}:
                    continue
                if not 1 <= len(value["steps"]) <= 6 or not isinstance(value.get("events"), list) or not isinstance(value.get("resource_revisions"), list):
                    continue
                if not isinstance(value.get("elapsed_seconds"), (int, float)) or not math.isfinite(value["elapsed_seconds"]) or value["elapsed_seconds"] < 0:
                    continue
                for record in value["resource_revisions"]:
                    if record["kind"] not in {"knowledge", "memories", "mcp"} or not isinstance(record["digest"], str):
                        raise ValueError("Invalid resource revision")
                    UUID(record["id"])
                if [step["id"] for step in value["steps"]] != [step["id"] for step in value["request"]["steps"]]:
                    raise ValueError("Run plan does not match request")
                for step in value["steps"]:
                    config = StepConfig.model_validate(step["config"])
                    if not isinstance(step.get("title"), str) or step["id"] != config.id or step["mode"] != config.mode or step["status"] not in {"pending", "running", "complete", "error", "cancelled", "interrupted"}:
                        raise ValueError("Invalid step state")
                    if not isinstance(step.get("attempts"), list) or len(step["attempts"]) > 3:
                        raise ValueError("Invalid attempts")
                    for attempt in step["attempts"]:
                        UUID(attempt["job_id"])
                    if step["status"] == "complete" and (not isinstance(step.get("output"), dict) or not isinstance(step["output"].get("text"), str)):
                        raise ValueError("Missing completed output")
                runs.append(value)
            except (ValueError, TypeError, KeyError, OSError):
                continue
        return runs


def public_run(run):
    value = copy.deepcopy(run)
    value.pop("resource_revisions", None)
    return value


def checked_id(identifier):
    try:
        return str(UUID(identifier))
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "协作任务标识无效") from error
