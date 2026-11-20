"""Todo Kanban board API with atomic persistence and concurrency safety."""
import copy
import hashlib
import json
import logging
import re
import threading
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .attachments import secret_text
from .execution_store import save_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/todo", tags=["todo"])

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "todo"
BOARD_FILE = DATA_DIR / "board.json"
SCHEMA_VERSION = 2
MAX_ACTIVE_TASKS = 2000
VALID_EXTERNAL_PLATFORMS = {"workbench", "group", "feishu", "fleet", "muse"}
BoardStatus = Literal["todo", "doing", "blocked", "review", "done"]

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
    # Previous versions used the same field for both the column and synced state.
    for task in data["tasks"]:
        synced = bool(task.get("external_source"))
        task.setdefault("status_mode", "source" if synced else "manual")
        if synced:
            task.setdefault("source_status", task.get("status"))
        task.setdefault("room_id", None)
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


def _uuid_value(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError("项目或群聊 ID 格式无效") from exc


def _lane(tasks: list[dict], status: str, excluding: str | None = None) -> list[dict]:
    return sorted(
        (task for task in tasks if task["id"] != excluding and task.get("status") == status and not task.get("archived")),
        key=lambda task: task.get("position") or 0,
    )


def _lane_end(tasks: list[dict], status: str, excluding: str | None = None) -> int:
    lane = _lane(tasks, status, excluding)
    return (lane[-1].get("position") or 0) + 1 if lane else 0


def _apply_board_status(tasks: list[dict], task: dict, status: str) -> None:
    if task.get("status") != status:
        task.update(status=status, position=_lane_end(tasks, status, task["id"]))


# --- Pydantic models ---


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: uuid.UUID | None = None
    title: str
    description: str = ""
    status: BoardStatus = "todo"
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    assignee: str = ""
    due_date: Optional[str] = None
    tags: list[str] = []
    project_id: Optional[str] = None
    room_id: Optional[str] = None
    position: int = 0
    # external_source is intentionally absent: the sync key is written only by
    # todo_sync. Accepting it over HTTP let any caller pre-claim (platform,
    # task_id) and hijack, split or orphan the mirrored card.

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

    @field_validator("project_id", "room_id")
    @classmethod
    def validate_project_id(cls, v: Optional[str]) -> Optional[str]:
        return _uuid_value(v)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[BoardStatus] = None
    board_status: Optional[BoardStatus] = None
    status_mode: Optional[Literal["source"]] = None
    follow_source: bool = False
    priority: Optional[Literal["low", "medium", "high", "urgent"]] = None
    assignee: Optional[str] = None
    due_date: Optional[str] = None
    tags: Optional[list[str]] = None
    project_id: Optional[str] = None
    room_id: Optional[str] = None
    position: Optional[int] = None
    archived: Optional[bool] = None
    # See TaskCreate: the sync key must not be rewritable over HTTP, otherwise a
    # PUT can detach a live card and make the next sync create a duplicate.

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

    @field_validator("project_id", "room_id")
    @classmethod
    def validate_project_id(cls, v: Optional[str]) -> Optional[str]:
        return _uuid_value(v)

    @model_validator(mode="after")
    def validate_placement(self):
        if self.board_status is not None and (self.follow_source or self.status_mode == "source"):
            raise ValueError("不能同时更改看板列和恢复跟随来源")
        return self


class ReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str]
    status: BoardStatus
    position_start: int = 0


class MoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: BoardStatus
    anchor_id: str | None = None
    placement: Literal["before", "after"] = "after"
    from_status: BoardStatus | None = None


# --- Internal upsert / archive for todo_sync (not HTTP) ---


def upsert_external_task(
    external_source: dict,
    title: str,
    status: str,
    description: str = "",
    source_label: str = "",
    project_id: Optional[str] = None,
    result_ref: dict | None = None,
    *,
    room_id: str | None = None,
    preserve_board_state: bool = False,
) -> dict:
    """Mirror source state while preserving any manual board placement.

    Startup recovery can also refresh an archived card without resurfacing it.
    """
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data["tasks"])
        identity = _external_identity(external_source)
        matches = [task for task in tasks if _external_identity(task.get("external_source")) == identity]
        task = next((task for task in matches if not task.get("archived")), matches[0] if matches else None)
        if (task is None or (task.get("archived") and not preserve_board_state)) and _active_count(tasks) >= MAX_ACTIVE_TASKS:
            logger.warning(
                "todo_board: active task limit reached (%d), skipping sync for %s/%s",
                MAX_ACTIVE_TASKS, *identity,
            )
            return {}
        task, changed = _mirror_external_task(
            tasks, task, external_source, title, status, description, source_label,
            project_id, result_ref, room_id=room_id, preserve_board_state=preserve_board_state,
        )
        if changed:
            _save_board({**data, "tasks": tasks})
        return task


def _external_identity(source: dict | None) -> tuple[str | None, str | None]:
    return (source.get("platform"), source.get("task_id")) if source else (None, None)


def _mirror_external_task(
    tasks: list[dict], task: dict | None, external_source: dict, title: str, status: str,
    description: str = "", source_label: str = "", project_id: str | None = None,
    result_ref: dict | None = None, *, room_id: str | None = None,
    preserve_board_state: bool = False,
) -> tuple[dict, bool]:
    from .result_refs import card_result_ref

    platform, task_id = _external_identity(external_source)
    # HTTP callers cannot supply this identity.
    if platform not in VALID_EXTERNAL_PLATFORMS:
        raise ValueError(f"unsupported external platform: {platform}")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("external_source.task_id is required")
    if status not in {"todo", "doing", "blocked", "review", "done"}:
        raise ValueError(f"unsupported board status: {status}")
    previous = task.copy() if task is not None else None
    now = _now_iso()
    if task is None:
        task = {
            "id": str(uuid.uuid4()),
            "status": status,
            "status_mode": "source",
            "priority": "medium",
            "assignee": "",
            "due_date": None,
            "tags": [source_label] if source_label else [],
            "source_label": source_label,
            "position": _lane_end(tasks, status),
            "created_at": now,
            "archived": False,
            "external_source": {"platform": platform, "task_id": task_id},
        }
        tasks.append(task)
    if not (preserve_board_state and task.get("archived")):
        task["archived"] = False
        if task["status_mode"] == "source":
            _apply_board_status(tasks, task, status)
    task["source_status"] = status
    if previous is None or not preserve_board_state:
        # The board can be seen outside the source app, so screen titles before mirroring.
        safe_title = "（来源标题含疑似凭据，已隐藏）" if secret_text(title) else title[:200]
        task.update(title=safe_title, description=description[:4000])
    if source_label:
        task["source_label"] = source_label
    _apply_source_scope(task, project_id, room_id)
    candidate = task.get("result_ref") if result_ref is None else result_ref
    if candidate is not None:
        task["result_ref"] = card_result_ref(task, candidate)
    changed = task != previous
    if changed:
        task["updated_at"] = now
    return task, changed


def backfill_external_tasks(cards: Iterable[dict]) -> int:
    """Recover validated source snapshots, in priority order, using one board write."""
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data["tasks"])
        index = {}
        for task in tasks:
            key = _external_identity(task.get("external_source"))
            if key[0] is not None and (key not in index or index[key].get("archived")):
                index[key] = task
        seen = set()
        active_count = _active_count(tasks)
        changed_count = 0
        candidates = []
        for snapshot in cards:
            key = _external_identity(snapshot.get("external_source"))
            if key in seen:
                continue
            seen.add(key)
            task = index.get(key)
            if snapshot["status"] == "archived":
                if task is not None and not task.get("archived"):
                    task.update(archived=True, updated_at=_now_iso())
                    changed_count += 1
                    active_count -= 1
                continue
            candidates.append((snapshot, task))
        for snapshot, task in candidates:
            if task is None and active_count >= MAX_ACTIVE_TASKS:
                continue
            _, changed = _mirror_external_task(tasks, task, **snapshot, preserve_board_state=True)
            if changed:
                changed_count += 1
            if task is None:
                active_count += 1
        if changed_count:
            _save_board({**data, "tasks": tasks})
        return changed_count


def _apply_source_scope(task: dict, project_id: str | None, room_id: str | None) -> None:
    task.update(project_id=project_id, room_id=room_id)


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
        _save_board({**data, "tasks": tasks})
        return target


def repair_workbench_delivery_cards(jobs: dict) -> None:
    """Correct older cards from the source records recovered at startup."""
    from .result_refs import card_result_ref, workbench_result_ref
    from .todo_sync import STATUS_MAP, workbench_card_state

    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data["tasks"])
        for task in tasks:
            source = task.get("external_source") or {}
            if source.get("platform") != "workbench":
                continue
            job = jobs.get(source.get("task_id"))
            if job is None:
                continue
            source_ref = workbench_result_ref(job)
            _apply_source_scope(task, source_ref["project_id"], source_ref["room_id"])
            task["result_ref"] = card_result_ref(task, source_ref)
            status, detail = workbench_card_state(job)
            lane = STATUS_MAP.get(status)
            if lane == "archived":
                if not task.get("archived"):
                    task.update(archived=True, updated_at=_now_iso())
            elif lane and lane != task.get("source_status"):
                task.update(source_status=lane, updated_at=_now_iso())
                if job.get("status") == "complete" and lane in {"review", "blocked"}:
                    task["description"] = detail
                if task["status_mode"] == "source" and not task.get("archived"):
                    _apply_board_status(tasks, task, lane)
        if tasks != data["tasks"]:
            _save_board({**data, "tasks": tasks})


def repair_group_task_result_refs(records: dict) -> None:
    from .result_refs import card_result_ref, group_task_result_ref

    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data["tasks"])
        for task in tasks:
            source = task.get("external_source") or {}
            item = records.get(source.get("task_id")) if source.get("platform") == "group" else None
            if item:
                ref = group_task_result_ref(item)
                _apply_source_scope(task, ref["project_id"], ref["room_id"])
                task["result_ref"] = card_result_ref(task, ref)
        if tasks != data["tasks"]:
            _save_board({**data, "tasks": tasks})


# --- Endpoints ---


@router.get("")
def list_tasks(archived: bool = False, project_id: str | None = None, room_id: str | None = None):
    try:
        project = _uuid_value(None if project_id == "none" else project_id)
        room = _uuid_value(room_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _lock:
        data = _load_board()
        return [task for task in data["tasks"]
                if (archived or not task.get("archived"))
                and (project_id is None or task.get("project_id") == project)
                and (room is None or task.get("room_id") == room)]


def has_room_cards(project_id: str | None, room_id: str) -> bool:
    """Check all cards, including archives, before deleting a room."""
    with _lock:
        return any(task.get("project_id") == project_id and task.get("room_id") == room_id
                   for task in _load_board()["tasks"])


def _room_store():
    from .groups import store
    return store


def _validate_room_scope(store, room_id: str, project_id: str | None) -> None:
    try:
        store.get(room_id, project_id)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            raise HTTPException(422, "群聊不存在或不属于所选项目") from exc
        raise


def _card_creation_event(task, request):
    """Only an intentional creation through a trusted board can trigger group work."""
    if request is None or not task["room_id"] or not task["tags"]:
        return None
    from .remote_access import require_local
    try:
        require_local(request)
    except HTTPException:
        return None
    remote = request.headers.get("x-modeldesk-remote")
    if remote not in (None, "phone"):
        return None
    event = {key: task[key] for key in ("id", "project_id", "room_id", "title", "created_at")}
    event["tags"] = [tag for tag in task["tags"] if tag.strip() and not secret_text(tag)]
    event["description"] = "待办描述含疑似凭据，自动化仅使用标题。" if secret_text(task["description"]) else task["description"]
    event["local"] = remote is None
    return event if event["tags"] else None


def _card_status_event(task, request, changed_at):
    """Record a trusted manual column entry for board_status routines."""
    if request is None or not task.get("room_id") or task.get("external_source") or task.get("archived"):
        return None
    from .remote_access import require_local
    try:
        require_local(request)
    except HTTPException:
        return None
    remote = request.headers.get("x-modeldesk-remote")
    if remote not in (None, "phone"):
        return None
    return {
        "event_id": str(uuid.uuid4()),
        "id": task["id"],
        "project_id": task.get("project_id"),
        "room_id": task["room_id"],
        "title": task["title"],
        "status": task["status"],
        "changed_at": changed_at,
        "tags": [tag for tag in (task.get("tags") or []) if tag.strip() and not secret_text(tag)],
        "description": "待办描述含疑似凭据，自动化仅使用标题。" if secret_text(task.get("description") or "") else (task.get("description") or ""),
        "local": remote is None,
    }


def _record_status_event(data, task, request, changed_at):
    event = _card_status_event(task, request, changed_at)
    if not event:
        return data
    events = dict(data.get("manual_status_events", {}))
    events[task["id"]] = event
    if len(events) > 200:
        ordered = sorted(events.items(), key=lambda pair: pair[1].get("changed_at", ""))
        events = dict(ordered[-200:])
    return {**data, "manual_status_events": events}


def routine_card_events() -> list[dict]:
    """Return the original create-time inputs. Ordinary edits and mirrored jobs emit nothing."""
    with _lock:
        data = _load_board()
        records = data.get("manual_creations", {})
        return [copy.deepcopy(records[task["id"]]["event"]) for task in data["tasks"]
                if not task.get("external_source") and not task.get("archived")
                and records.get(task["id"], {}).get("event")]


def routine_status_events() -> list[dict]:
    """Return trusted manual column-entry snapshots for board_status routines."""
    with _lock:
        data = _load_board()
        events = data.get("manual_status_events", {})
        return [copy.deepcopy(events[task["id"]]) for task in data["tasks"]
                if not task.get("external_source") and not task.get("archived")
                and events.get(task["id"])]


def _retire_card_event(data, task_id):
    updated = data
    records = updated.get("manual_creations", {})
    record = records.get(task_id, {})
    if "event" in record:
        records = records.copy()
        records[task_id] = {key: value for key, value in record.items() if key != "event"}
        updated = {**updated, "manual_creations": records}
    status_events = updated.get("manual_status_events", {})
    if task_id in status_events:
        status_events = {key: value for key, value in status_events.items() if key != task_id}
        updated = {**updated, "manual_status_events": status_events}
    return updated


@router.post("")
def create_task(body: TaskCreate, request: Request = None):
    rooms = _room_store() if body.room_id else None
    # Room deletion takes these locks in the same order.
    with (rooms.lock if rooms is not None else nullcontext()), _lock:
        if body.room_id:
            _validate_room_scope(rooms, body.room_id, body.project_id)
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        record = {}
        if body.client_request_id:
            from .dispatch_receipts import scoped_task_id
            identifier = scoped_task_id("todo", body.project_id, body.room_id, body.client_request_id)
            original = body.model_dump(mode="json", exclude={"client_request_id"})
            if "position" not in body.model_fields_set:
                original.pop("position")
            encoded = json.dumps(original, sort_keys=True, ensure_ascii=False).encode()
            record["request_sha256"] = hashlib.sha256(encoded).hexdigest()
            previous = next((card for card in tasks if card["id"] == identifier), None)
            if previous:
                saved = data.get("manual_creations", {}).get(identifier, {})
                if (saved.get("request_sha256") != record["request_sha256"]
                        or (previous.get("project_id"), previous.get("room_id")) != (body.project_id, body.room_id)):
                    raise HTTPException(409, "此创建回执对应的内容或任务归属已变更，请查看原卡片")
                return previous
        else:
            identifier = str(uuid.uuid4())
        if _active_count(tasks) >= MAX_ACTIVE_TASKS:
            raise HTTPException(
                status_code=422,
                detail=f"活跃任务数已达上限（{MAX_ACTIVE_TASKS}），请归档或删除部分任务后再创建",
            )
        now = _now_iso()
        task = {
            "id": identifier,
            "title": body.title,
            "description": body.description,
            "status": body.status,
            "status_mode": "manual",
            "priority": body.priority,
            "assignee": body.assignee,
            "due_date": body.due_date,
            "tags": body.tags,
            "project_id": body.project_id,
            "room_id": body.room_id,
            "position": body.position if "position" in body.model_fields_set else _lane_end(tasks, body.status),
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "external_source": None,
        }
        tasks.append(task)
        new_data = {**data, "tasks": tasks}
        event = _card_creation_event(task, request)
        if event:
            record["event"] = event
        if record:
            new_data["manual_creations"] = {**data.get("manual_creations", {}), identifier: record}
        new_data = _record_status_event(new_data, task, request, now)
        _save_board(new_data)
        return task


@router.put("/{task_id}")
def update_task(task_id: str, body: TaskUpdate, request: Request):
    rooms = _room_store() if body.room_id else None
    with (rooms.lock if rooms is not None else nullcontext()), _lock:
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
        ext = target.get("external_source")
        status = updates.pop("status", None)
        board_status = updates.pop("board_status", None)
        status_mode = updates.pop("status_mode", None)
        follow_source = updates.pop("follow_source", False) or status_mode == "source"
        if ext:
            # Older editors echo status without expressing a move. An explicit
            # board_status changes the column only; synced scope is read only.
            status = None
            for field in ("project_id", "room_id"):
                if field in updates and updates.pop(field) != target.get(field):
                    raise HTTPException(409, "同步任务的项目和群聊由来源管理")
        else:
            if follow_source:
                raise HTTPException(422, "只有同步任务可以恢复跟随来源")
            project = updates.get("project_id", target.get("project_id"))
            project_changed = project != target.get("project_id")
            room = updates.get("room_id", None if project_changed else target.get("room_id"))
            if room and (project_changed or room != target.get("room_id")):
                _validate_room_scope(rooms, room, project)
            if project_changed:
                updates["room_id"] = room
        if not updates and not (status or board_status or follow_source):
            return target
        old = target.copy()
        old_archived = target.get("archived")
        if not old_archived and updates.get("archived"):
            _require_source_archive_access(ext, request)
        if follow_source:
            _apply_board_status(tasks, target, target["source_status"])
            target["status_mode"] = "source"
        elif board_status or status:
            requested = board_status or status
            if requested != target["status"]:
                _apply_board_status(tasks, target, requested)
                target["status_mode"] = "manual"
        target.update(updates)
        if target == old:
            return target
        target["updated_at"] = _now_iso()
        new_data = {**data, "tasks": tasks}
        if target.get("archived") or (old.get("project_id"), old.get("room_id")) != (target.get("project_id"), target.get("room_id")):
            new_data = _retire_card_event(new_data, task_id)
        if not ext and not target.get("archived") and target.get("status") != old.get("status"):
            new_data = _record_status_event(new_data, target, request, target["updated_at"])
        _save_board(new_data)

    if isinstance(ext, dict) and not old_archived and target.get("archived"):
        _archive_source(ext, target.get("source_status", target.get("status")))
    return target


@router.delete("/{task_id}")
def delete_task(task_id: str, request: Request):
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
        ext = target.get("external_source")
        _require_source_archive_access(ext, request)
        target["archived"] = True
        target["updated_at"] = _now_iso()
        new_data = _retire_card_event({**data, "tasks": tasks}, task_id)
        _save_board(new_data)

    if isinstance(ext, dict):
        _archive_source(ext, target.get("source_status", target.get("status")))
    return target


def _require_source_archive_access(external_source: dict | None, request: Request) -> None:
    if not external_source or external_source.get("platform") != "fleet":
        return
    from .fleet import fleet_admin_guard
    from .platform_support import machine_label

    if request.headers.get("x-modeldesk-remote"):
        raise HTTPException(403, f"节点任务取消只能在{machine_label()}的 ModelDesk 中操作")
    fleet_admin_guard(request)


def _archive_source(external_source: dict, status: str):
    # Fleet calls forward sync while holding its store lock, so reverse sync
    # must run outside the board lock.
    from .todo_sync import reverse_sync_task
    reverse_sync_task(external_source.get("platform"), external_source.get("task_id"), target_status=status, is_archived=True)


@router.post("/move")
def move_task(body: MoveRequest, request: Request = None):
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data["tasks"])
        target = next((task for task in tasks if task["id"] == body.id and not task.get("archived")), None)
        if target is None:
            raise HTTPException(404, "任务不存在或已归档")
        if body.from_status is not None and target["status"] != body.from_status:
            raise HTTPException(409, "任务的看板列已变更，请刷新后重试")
        old_status = target["status"]
        old_lane = _lane(tasks, body.status)
        lane = [card for card in old_lane if card["id"] != target["id"]]
        insert_at = len(lane)
        if body.anchor_id is not None:
            anchor = next((index for index, card in enumerate(lane) if card["id"] == body.anchor_id), None)
            if anchor is None:
                raise HTTPException(409, "目标位置已变更，请刷新后重试")
            insert_at = anchor + (body.placement == "after")
        lane.insert(insert_at, target)
        if lane == old_lane:
            return target
        if target["status"] != body.status:
            target.update(status=body.status, status_mode="manual")
        # Include hidden cards when assigning ranks, but change only the moved
        # card's lane and mode. Hidden cards retain their relative order.
        for index, card in enumerate(lane):
            card["position"] = index
        target["updated_at"] = _now_iso()
        new_data = {**data, "tasks": tasks}
        if not target.get("external_source") and target["status"] != old_status:
            new_data = _record_status_event(new_data, target, request, target["updated_at"])
        _save_board(new_data)
        return target


@router.post("/reorder")
def reorder_tasks(body: ReorderRequest):
    with _lock:
        data = _load_board()
        tasks = copy.deepcopy(data.get("tasks", []))
        # Build index
        task_map = {t["id"]: t for t in tasks}
        # A column payload may include a synced card whose source already
        # moved it. Reject the stale reorder before changing any positions.
        for tid in body.ids:
            task = task_map.get(tid)
            if task is None:
                raise HTTPException(status_code=422, detail=f"任务 {tid} 不存在")
            if task.get("external_source") and task.get("status") != body.status:
                raise HTTPException(status_code=409, detail="任务的看板列已变更，请刷新后重试。")
        # The board is one global board: cards from different project scopes
        # legitimately share a status column, and the UI drags the whole column
        # at once. Rejecting the mix made every drag in a mixed column fail, so
        # positions are assigned across scopes in the order the user saw.
        now = _now_iso()
        for idx, tid in enumerate(body.ids):
            task_map[tid]["status"] = body.status
            task_map[tid]["position"] = body.position_start + idx
            task_map[tid]["updated_at"] = now
        _save_board({**data, "tasks": tasks})
        return {"ok": True, "reordered": len(body.ids)}
