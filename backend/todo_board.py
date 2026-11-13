"""Todo Kanban board API with atomic persistence and concurrency safety."""
import copy
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .attachments import secret_text
from .execution_store import save_json

router = APIRouter(prefix="/api/todo", tags=["todo"])

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "todo"
BOARD_FILE = DATA_DIR / "board.json"
SCHEMA_VERSION = 1
MAX_ACTIVE_TASKS = 2000

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
