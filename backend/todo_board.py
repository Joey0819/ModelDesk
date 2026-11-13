"""Todo Kanban board API with atomic persistence and concurrency safety."""
import copy
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .attachments import secret_text
from .execution_store import save_json

router = APIRouter(prefix="/api/todo", tags=["todo"])

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "todo"
BOARD_FILE = DATA_DIR / "board.json"
SCHEMA_VERSION = 2
MAX_ACTIVE_TASKS = 2000
VALID_EXTERNAL_PLATFORMS = {"workbench", "group", "feishu"}

_lock = threading.RLock()
_cache: dict | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_board() -> dict:
    """Load board from disk. Raises HTTPException 500 on corruption."""
    global _cache
    if _cache is not None:
        return _cache
    if not BOARD_FILE.exists():
        data = {"schema_version": SCHEMA_VERSION, "tasks": []}
        _cache = data
        return data
    try:
        raw = BOARD_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"看板数据文件损坏，请检查 {BOARD_FILE}。原始错误：{exc}",
        )
    if not isinstance(data, dict) or "tasks" not in data:
        raise HTTPException(status_code=500, detail="看板数据格式无效，缺少 tasks 字段")
    # Migrate v1 -> v2 if needed
    data = _migrate_v1_to_v2(data)
    _cache = data
    return data


def _save_board(data: dict) -> None:
    """Atomically persist board to disk. Only update memory after successful write."""
    global _cache
    try:
        save_json(BOARD_FILE, data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"看板数据写入失败：{exc}") from exc
    _cache = data


def _active_count(tasks: list[dict]) -> int:
    return sum(1 for t in tasks if not t.get("archived", False))


def _migrate_v1_to_v2(data: dict) -> dict:
    """Migrate schema v1 board data to v2 by adding external_source=None to existing tasks."""
    if data.get("schema_version", 1) < 2:
        for task in data.get("tasks", []):
            if "external_source" not in task:
                task["external_source"] = None
        data["schema_version"] = SCHEMA_VERSION
    return data


# --- Pydantic models ---


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    status: Literal["todo", "doing", "blocked", "review", "done"] = "todo"
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    assignee: str = ""
    due_date: Optional[str] = None
    tags: list[str] = []
    project_id: Optional[str] = None
    position: int = 0
    external_source: Optional[dict] = None

    @field_validator("external_source")
    @classmethod
    def validate_external_source(cls, v: Optional[dict]) -> Optional[dict]:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("external_source must be a dict")
        platform = v.get("platform")
        task_id = v.get("task_id")
        if not platform or not isinstance(platform, str):
            raise ValueError("external_source.platform is required and must be a string")
        if not task_id or not isinstance(task_id, str):
            raise ValueError("external_source.task_id is required and must be a string")
        if platform not in VALID_EXTERNAL_PLATFORMS:
            raise ValueError(f"external_source.platform must be one of {sorted(VALID_EXTERNAL_PLATFORMS)}")
        if len(platform) > 50:
            raise ValueError("external_source.platform exceeds 50 characters")
        if len(task_id) > 200:
            raise ValueError("external_source.task_id exceeds 200 characters")
        # Only allow exactly these two keys
        extra_keys = set(v.keys()) - {"platform", "task_id"}
        if extra_keys:
            raise ValueError(f"external_source contains unexpected keys: {extra_keys}")
        return {"platform": platform, "task_id": task_id}

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > 200:
            raise ValueError("标题不能超过 200 个字符")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
            raise ValueError("标题包含非法控制字符")
        if secret_text(v):
            raise ValueError("标题包含疑似凭据信息")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("描述不能超过 4000 个字符")
        return v

    @field_validator("assignee")
    @classmethod
    def validate_assignee(cls, v: str) -> str:
        if len(v) > 100:
            raise ValueError("负责人名称不能超过 100 个字符")
        return v

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("截止日期格式必须为 YYYY-MM-DD")
        # Validate it's a real date
        try:
            parts = v.split("-")
            datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            raise ValueError("截止日期不是有效日期")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        if len(v) > 20:
            raise ValueError("标签数量不能超过 20 个")
        for tag in v:
            if len(tag) > 100:
                raise ValueError("单个标签不能超过 100 个字符")
        return v

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("项目 ID 格式无效")
        return v


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["todo", "doing", "blocked", "review", "done"]] = None
    priority: Optional[Literal["low", "medium", "high", "urgent"]] = None
    assignee: Optional[str] = None
    due_date: Optional[str] = None
    tags: Optional[list[str]] = None
    project_id: Optional[str] = None
    position: Optional[int] = None
    archived: Optional[bool] = None
    external_source: Optional[dict] = None

    @field_validator("external_source")
    @classmethod
    def validate_external_source(cls, v: Optional[dict]) -> Optional[dict]:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("external_source must be a dict")
        platform = v.get("platform")
        task_id = v.get("task_id")
        if not platform or not isinstance(platform, str):
            raise ValueError("external_source.platform is required and must be a string")
        if not task_id or not isinstance(task_id, str):
            raise ValueError("external_source.task_id is required and must be a string")
        if platform not in VALID_EXTERNAL_PLATFORMS:
            raise ValueError(f"external_source.platform must be one of {sorted(VALID_EXTERNAL_PLATFORMS)}")
        if len(platform) > 50:
            raise ValueError("external_source.platform exceeds 50 characters")
        if len(task_id) > 200:
            raise ValueError("external_source.task_id exceeds 200 characters")
        extra_keys = set(v.keys()) - {"platform", "task_id"}
        if extra_keys:
            raise ValueError(f"external_source contains unexpected keys: {extra_keys}")
        return {"platform": platform, "task_id": task_id}

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > 200:
            raise ValueError("标题不能超过 200 个字符")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
            raise ValueError("标题包含非法控制字符")
        if secret_text(v):
            raise ValueError("标题包含疑似凭据信息")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if len(v) > 4000:
            raise ValueError("描述不能超过 4000 个字符")
        return v

    @field_validator("assignee")
    @classmethod
    def validate_assignee(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if len(v) > 100:
            raise ValueError("负责人名称不能超过 100 个字符")
        return v

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v == "":
            return None
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("截止日期格式必须为 YYYY-MM-DD")
        try:
            parts = v.split("-")
            datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            raise ValueError("截止日期不是有效日期")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        if len(v) > 20:
            raise ValueError("标签数量不能超过 20 个")
        for tag in v:
            if len(tag) > 100:
                raise ValueError("单个标签不能超过 100 个字符")
        return v

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("项目 ID 格式无效")
        return v


class ReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str]
    status: Literal["todo", "doing", "blocked", "review", "done"]
    position_start: int = 0


# --- Internal upsert / archive for todo_sync (not HTTP) ---


def upsert_external_task(
    external_source: dict,
    title: str,
    status: str,
    description: str = "",
    source_label: str = "",
    project_id: Optional[str] = None,
) -> dict:
    """Idempotent upsert of a synced card keyed by (platform, task_id).

    If an active (non-archived) card with the same external_source exists,
    update its title/status/description. Otherwise create a new card.
    Returns the created or updated task dict.

    This function is called from todo_sync while holding no lock;
    it acquires _lock internally.
    """
    platform = external_source["platform"]
    task_id = external_source["task_id"]
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        # Look for existing active card with same external_source
        existing = None
        for t in tasks:
            es = t.get("external_source")
            if (
                es
                and es.get("platform") == platform
                and es.get("task_id") == task_id
                and not t.get("archived", False)
            ):
                existing = t
                break
        now_ts = _now_iso()
        if existing is not None:
            # Update in place
            existing["title"] = title[:200]
            existing["status"] = status
            existing["description"] = description[:4000]
            # The display label can change (e.g. a workbench job that turns out
            # to be Feishu-dispatched), and the UI must follow the newest one.
            if source_label:
                existing["source_label"] = source_label
            existing["updated_at"] = now_ts
            new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
            _save_board(new_data)
            return existing
        else:
            # Check if there's an archived card we should un-archive
            archived_match = None
            for t in tasks:
                es = t.get("external_source")
                if (
                    es
                    and es.get("platform") == platform
                    and es.get("task_id") == task_id
                    and t.get("archived", False)
                ):
                    archived_match = t
                    break
            if archived_match is not None:
                # Un-archive and update
                archived_match["archived"] = False
                archived_match["title"] = title[:200]
                archived_match["status"] = status
                archived_match["description"] = description[:4000]
                if source_label:
                    archived_match["source_label"] = source_label
                archived_match["updated_at"] = now_ts
                new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
                _save_board(new_data)
                return archived_match
            else:
                # Create new card
                if _active_count(tasks) >= MAX_ACTIVE_TASKS:
                    logger.warning(
                        "todo_board: active task limit reached (%d), skipping sync for %s/%s",
                        MAX_ACTIVE_TASKS, platform, task_id,
                    )
                    return {}
                task = {
                    "id": str(uuid.uuid4()),
                    "title": title[:200],
                    "description": description[:4000],
                    "status": status,
                    "priority": "medium",
                    "assignee": "",
                    "due_date": None,
                    "tags": [source_label] if source_label else [],
                    "source_label": source_label,
                    "project_id": project_id,
                    "position": 0,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                    "archived": False,
                    "external_source": {"platform": platform, "task_id": task_id},
                }
                tasks.append(task)
                new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
                _save_board(new_data)
                return task


def archive_external_task(external_source: dict) -> Optional[dict]:
    """Archive (soft-delete) a synced card by its external_source key.

    Returns the archived task dict, or None if not found.
    """
    platform = external_source["platform"]
    task_id = external_source["task_id"]
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        target = None
        for t in tasks:
            es = t.get("external_source")
            if (
                es
                and es.get("platform") == platform
                and es.get("task_id") == task_id
                and not t.get("archived", False)
            ):
                target = t
                break
        if target is None:
            return None
        target["archived"] = True
        target["updated_at"] = _now_iso()
        new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
        _save_board(new_data)
        return target


# --- Endpoints ---


@router.get("")
def list_tasks(archived: bool = Query(False)):
    with _lock:
        data = _load_board()
        tasks = data.get("tasks", [])
        if not archived:
            tasks = [t for t in tasks if not t.get("archived", False)]
        return tasks


@router.post("")
def create_task(body: TaskCreate):
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        if _active_count(tasks) >= MAX_ACTIVE_TASKS:
            raise HTTPException(
                status_code=422,
                detail=f"活跃任务数已达上限（{MAX_ACTIVE_TASKS}），请归档或删除部分任务后再创建",
            )
        now = _now_iso()
        task = {
            "id": str(uuid.uuid4()),
            "title": body.title,
            "description": body.description,
            "status": body.status,
            "priority": body.priority,
            "assignee": body.assignee,
            "due_date": body.due_date,
            "tags": body.tags,
            "project_id": body.project_id,
            "position": body.position,
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "external_source": body.external_source,
        }
        tasks.append(task)
        new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
        _save_board(new_data)
        return task


@router.put("/{task_id}")
def update_task(task_id: str, body: TaskUpdate):
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        target = None
        for t in tasks:
            if t["id"] == task_id:
                target = t
                break
        if target is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return target
        for key, value in updates.items():
            target[key] = value
        target["updated_at"] = _now_iso()
        new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
        _save_board(new_data)
        return target


@router.delete("/{task_id}")
def delete_task(task_id: str):
    """Soft delete: set archived=True."""
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        target = None
        for t in tasks:
            if t["id"] == task_id:
                target = t
                break
        if target is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        target["archived"] = True
        target["updated_at"] = _now_iso()
        new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
        _save_board(new_data)
        return target


@router.post("/reorder")
def reorder_tasks(body: ReorderRequest):
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        # Build index
        task_map = {t["id"]: t for t in tasks}
        # Verify all IDs exist
        for tid in body.ids:
            if tid not in task_map:
                raise HTTPException(status_code=422, detail=f"任务 {tid} 不存在")
        # Verify same project scope
        project_ids = set()
        for tid in body.ids:
            pid = task_map[tid].get("project_id")
            project_ids.add(pid)
        if len(project_ids) > 1:
            raise HTTPException(status_code=422, detail="不能跨项目重排任务")
        now = _now_iso()
        for idx, tid in enumerate(body.ids):
            task_map[tid]["status"] = body.status
            task_map[tid]["position"] = body.position_start + idx
            task_map[tid]["updated_at"] = now
        new_data = {"schema_version": SCHEMA_VERSION, "tasks": tasks}
        _save_board(new_data)
        return {"ok": True, "reordered": len(body.ids)}
