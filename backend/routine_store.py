"""Scoped routine settings, trigger cursors, durable dispatch receipts, telemetry and self-improvement loops."""
from collections.abc import Iterable
from contextlib import closing, contextmanager
from datetime import UTC, datetime, time, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


TriggerKind = Literal["manual", "daily", "weekly", "interval", "board_card", "board_status"]
BoardStatus = Literal["todo", "doing", "blocked", "review", "done"]
DocumentFormat = Literal["docx", "pdf", "html"]
FeedbackVerdict = Literal["good", "needs_improvement", "wrong_judgment"]
FeedbackSource = Literal["user", "review", "external"]
CaseState = Literal["waiting_details", "resolved"]
ProposalStatus = Literal["open", "applied", "dismissed"]
OPEN_STATUSES = ("pending", "submitted")
TIMED_TRIGGERS = ("daily", "weekly", "interval")


class RoutineTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind = "manual"
    time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    weekday: Annotated[int, Field(strict=True, ge=0, le=6)] | None = None
    every_hours: Annotated[int, Field(strict=True, ge=1, le=720)] | None = None
    tag: str | None = Field(default=None, max_length=100)
    board_status: BoardStatus | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("请选择有效的 IANA 时区") from None
        return value

    @field_validator("tag")
    @classmethod
    def clean_tag(cls, value):
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def validate_trigger_fields(self):
        if self.kind in ("daily", "weekly") and (self.time is None or self.timezone is None):
            raise ValueError("定时运行需要时间和时区")
        if self.kind == "weekly" and self.weekday is None:
            raise ValueError("每周运行需要指定星期")
        if self.kind == "interval" and self.every_hours is None:
            raise ValueError("固定间隔运行需要设置间隔小时数")
        if self.kind == "board_card" and not (self.tag or "").strip():
            raise ValueError("看板触发需要指定标签")
        if self.kind == "board_status" and self.board_status is None:
            raise ValueError("看板状态触发需要指定目标列")
        if self.kind == "board_status" and self.tag == "":
            self.tag = None
        return self


class RoutineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=3000)
    process: str = Field(default="", max_length=2500)
    checks: str = Field(default="", max_length=2500)
    judgment: str = Field(default="", max_length=2000)
    examples: str = Field(default="", max_length=2000)
    correlation_field: str = Field(default="", max_length=80)
    trigger: RoutineTrigger = Field(default_factory=RoutineTrigger)
    document_formats: list[DocumentFormat] = Field(default_factory=list, max_length=3)
    allow_native: StrictBool = False
    auto_reflect: StrictBool = False

    @field_validator("document_formats")
    @classmethod
    def unique_formats(cls, value):
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_text_budget(self):
        total = len(self.goal) + len(self.process) + len(self.checks) + len(self.judgment) + len(self.examples)
        if total > 7000:
            raise ValueError("目标、步骤、判断规则、案例和验收标准合计不能超过 7000 字")
        return self


class RoutineNotFound(LookupError):
    pass


class RoutineConflict(ValueError):
    pass


class RoutineBusy(RoutineConflict):
    pass


class RoutineForbidden(PermissionError):
    pass


def _uuid(value):
    return str(UUID(str(value)))


def _scope(project_id, room_id):
    return (_uuid(project_id) if project_id is not None else "", _uuid(room_id))


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间需要带时区")
    return value.astimezone(UTC)


def _iso(value):
    return value.isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _spec(spec):
    if isinstance(spec, RoutineSpec):
        return spec.model_dump(mode="json")
    return RoutineSpec.model_validate(spec).model_dump(mode="json")


def _normalize_spec_dict(raw):
    """Hydrate pre-2.29 stored specs with safe default fields."""
    data = dict(raw)
    data.setdefault("judgment", "")
    data.setdefault("examples", "")
    data.setdefault("correlation_field", "")
    data.setdefault("auto_reflect", False)
    trigger = dict(data.get("trigger") or {"kind": "manual"})
    trigger.setdefault("every_hours", None)
    trigger.setdefault("board_status", None)
    data["trigger"] = trigger
    return data


def clean_correlation_key(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 120 or re.search(r"[\x00-\x1f\x7f]", text):
        raise ValueError("关联编号长度不能超过 120 且不能包含控制字符")
    return text


def extract_correlation_key(spec, title="", description=""):
    """Extract an explicit correlation key from board text when correlation_field is configured."""
    field = (spec.get("correlation_field") or "").strip()
    if not field:
        return None
    text = f"{title}\n{description}"
    pattern = re.compile(rf"{re.escape(field)}\s*[:：#]\s*([^\s,，;；\n]{{1,80}})")
    match = pattern.search(text)
    if match:
        try:
            return clean_correlation_key(match.group(1))
        except ValueError:
            return None
    tag_match = re.search(r"#([A-Za-z0-9][A-Za-z0-9._:/-]{1,60})", text)
    if tag_match:
        try:
            return clean_correlation_key(tag_match.group(1))
        except ValueError:
            return None
    return None


def duration_seconds(start, end):
    if not start or not end:
        return None
    try:
        delta = (_utc(end) - _utc(start)).total_seconds()
    except (ValueError, TypeError):
        return None
    return round(max(0.0, delta), 1)


def routine_readiness(spec):
    """Deterministic readiness checklist aligned with goal -> process -> judgment -> checks -> examples -> trigger."""
    data = _normalize_spec_dict(spec)
    checks = {
        "goal": bool((data.get("goal") or "").strip()),
        "process": bool((data.get("process") or "").strip()),
        "judgment": bool((data.get("judgment") or "").strip()),
        "checks": bool((data.get("checks") or "").strip()),
        "examples": bool((data.get("examples") or "").strip()),
        "trigger": (data.get("trigger") or {}).get("kind", "manual") != "manual",
    }
    score = sum(1 for met in checks.values() if met)
    return {**checks, "score": score, "total": len(checks)}


class _WallSchedule:
    def __init__(self, trigger):
        self.zone = ZoneInfo(trigger["timezone"])
        self.time = time.fromisoformat(trigger["time"])
        self.weekday = trigger["weekday"] if trigger["kind"] == "weekly" else None
        self.interval = timedelta(days=1 if self.weekday is None else 7)

    def _on(self, day):
        wall_time = datetime.combine(day, self.time, tzinfo=self.zone)
        # fold=0 gives the earlier instant on a repeated hour and moves a
        # nonexistent local time forward by the size of a spring transition.
        candidate = wall_time.astimezone(UTC)
        if candidate.astimezone(self.zone).date() == day:
            return candidate
        return None

    def next_after(self, instant, used_keys):
        day = instant.astimezone(self.zone).date()
        if self.weekday is not None:
            day += timedelta(days=(self.weekday - day.weekday()) % 7)
        while True:
            candidate = self._on(day)
            if candidate is not None and candidate > instant and not used_keys(f"schedule:{day}"):
                return candidate
            day += self.interval

    def latest_due(self, instant, earliest):
        day = instant.astimezone(self.zone).date()
        if self.weekday is not None:
            day -= timedelta(days=(day.weekday() - self.weekday) % 7)
        while day >= earliest.astimezone(self.zone).date():
            candidate = self._on(day)
            if candidate is not None and earliest <= candidate <= instant:
                return f"schedule:{day}", candidate
            day -= self.interval
        return None


class _IntervalSchedule:
    def __init__(self, trigger, anchor):
        self.anchor = _utc(anchor)
        self.step = timedelta(hours=int(trigger["every_hours"]))

    def next_after(self, instant, used_keys):
        instant = _utc(instant)
        if instant < self.anchor:
            slot = 1
        else:
            slot = int((instant - self.anchor) // self.step) + 1
        while True:
            candidate = self.anchor + self.step * slot
            if candidate > instant and not used_keys(f"interval:{slot}"):
                return candidate
            slot += 1

    def latest_due(self, instant, earliest):
        instant = _utc(instant)
        earliest = _utc(earliest)
        if instant < earliest or instant < self.anchor + self.step:
            return None
        slot = int((instant - self.anchor) // self.step)
        while slot >= 1:
            candidate = self.anchor + self.step * slot
            if candidate < earliest:
                break
            if candidate <= instant:
                return f"interval:{slot}", candidate
            slot -= 1
        return None


def _schedule_for(spec, enabled_at):
    trigger = spec["trigger"]
    if trigger["kind"] in ("daily", "weekly"):
        return _WallSchedule(trigger)
    if trigger["kind"] == "interval" and enabled_at:
        return _IntervalSchedule(trigger, enabled_at)
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    create_request_id TEXT NOT NULL,
    create_spec TEXT NOT NULL,
    spec TEXT NOT NULL,
    team_revision TEXT NOT NULL,
    revision INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    enabled_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE (project_id, room_id, create_request_id)
);
CREATE INDEX IF NOT EXISTS routines_room ON routines (project_id, room_id, created_at)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS routines_due ON routines (next_run_at)
    WHERE enabled = 1 AND deleted_at IS NULL AND next_run_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS routine_occurrences (
    id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(id),
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    occurrence_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    dispatched_at TEXT,
    finished_at TEXT,
    correlation_key TEXT,
    case_state TEXT,
    spec TEXT NOT NULL,
    team_revision TEXT NOT NULL,
    routine_revision INTEGER NOT NULL,
    event TEXT,
    status TEXT NOT NULL,
    run_id TEXT,
    detail TEXT,
    UNIQUE (routine_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS routine_open ON routine_occurrences (routine_id)
    WHERE status IN ('pending', 'submitted');
CREATE UNIQUE INDEX IF NOT EXISTS routine_submitted ON routine_occurrences (routine_id)
    WHERE status = 'submitted';
CREATE UNIQUE INDEX IF NOT EXISTS routine_manual_request ON routine_occurrences
    (project_id, room_id, occurrence_key) WHERE kind = 'manual';
CREATE INDEX IF NOT EXISTS routine_recent ON routine_occurrences (routine_id, created_at DESC);
CREATE TABLE IF NOT EXISTS routine_revisions (
    id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(id),
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    spec TEXT NOT NULL,
    team_revision TEXT NOT NULL,
    change_note TEXT NOT NULL,
    proposal_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (routine_id, revision)
);
CREATE INDEX IF NOT EXISTS routine_revisions_lookup ON routine_revisions (routine_id, revision DESC);
CREATE TABLE IF NOT EXISTS routine_feedback (
    id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(id),
    occurrence_id TEXT NOT NULL REFERENCES routine_occurrences(id),
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT NOT NULL,
    correlation_key TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS routine_feedback_lookup ON routine_feedback (routine_id, created_at DESC);
CREATE INDEX IF NOT EXISTS routine_feedback_occ ON routine_feedback (occurrence_id, created_at DESC);
CREATE TABLE IF NOT EXISTS routine_proposals (
    id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(id),
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    diagnosis TEXT NOT NULL,
    changes_summary TEXT NOT NULL,
    proposed_spec TEXT NOT NULL,
    evidence_run_ids TEXT NOT NULL,
    applied_revision INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS routine_proposals_lookup ON routine_proposals (routine_id, status, created_at DESC);
"""

_DUE_QUERY = """
SELECT * FROM routines
WHERE enabled = 1 AND deleted_at IS NULL AND next_run_at <= ?
ORDER BY next_run_at, created_at, id
"""


class RoutineStore:
    def __init__(self, db_path, clock=None):
        self.path = Path(db_path)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._setup_lock = Lock()
        self._initialized = False

    @staticmethod
    def _migrate(database):
        columns = {row["name"] for row in database.execute("PRAGMA table_info(routine_occurrences)").fetchall()}
        for name in ("dispatched_at", "finished_at", "correlation_key", "case_state"):
            if name not in columns:
                database.execute(f"ALTER TABLE routine_occurrences ADD COLUMN {name} TEXT")
        database.execute(
            "CREATE INDEX IF NOT EXISTS routine_correlation ON routine_occurrences (routine_id, correlation_key, created_at DESC)"
        )
        # Backfill revision snapshot for pre-2.29 routines if missing.
        existing = database.execute(
            """SELECT id, project_id, room_id, revision, spec, team_revision, updated_at FROM routines AS r
               WHERE deleted_at IS NULL AND NOT EXISTS (
                   SELECT 1 FROM routine_revisions WHERE routine_id = r.id AND revision = r.revision
               )"""
        ).fetchall()
        for row in existing:
            database.execute(
                """INSERT OR IGNORE INTO routine_revisions
                   (id, routine_id, project_id, room_id, revision, spec, team_revision, change_note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), row["id"], row["project_id"], row["room_id"], row["revision"],
                 row["spec"], row["team_revision"], "既有配置版本", row["updated_at"]),
            )

    @contextmanager
    def _connect(self, *, write=False):
        if not self._initialized:
            with self._setup_lock:
                if not self._initialized:
                    self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    try:
                        os.fchmod(descriptor, 0o600)
                    finally:
                        os.close(descriptor)
                    with closing(sqlite3.connect(self.path)) as database:
                        database.row_factory = sqlite3.Row
                        database.executescript(SCHEMA)
                        self._migrate(database)
                        database.commit()
                    self._initialized = True
        database = sqlite3.connect(self.path)
        database.row_factory = sqlite3.Row
        try:
            if write:
                database.execute("BEGIN IMMEDIATE")
            yield database
            if write:
                database.commit()
        finally:
            database.close()

    def _now(self, now):
        return _utc(self.clock() if now is None else now)

    @staticmethod
    def _routine(row):
        spec = _normalize_spec_dict(json.loads(row["spec"]))
        return {
            **spec,
            "id": row["id"],
            "project_id": row["project_id"] or None,
            "room_id": row["room_id"],
            "client_request_id": row["create_request_id"],
            "team_revision": row["team_revision"],
            "revision": row["revision"],
            "enabled": bool(row["enabled"]),
            "next_run_at": row["next_run_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _occurrence(row):
        result = dict(row)
        key = result.pop("occurrence_key")
        result["client_request_id"] = key.removeprefix("manual:") if row["kind"] == "manual" else None
        result["project_id"] = row["project_id"] or None
        result["spec"] = json.loads(row["spec"])
        result["event"] = json.loads(row["event"]) if row["event"] is not None else None
        return result

    @staticmethod
    def _revision_row(row):
        return {
            "id": row["id"],
            "routine_id": row["routine_id"],
            "project_id": row["project_id"] or None,
            "room_id": row["room_id"],
            "revision": row["revision"],
            "spec": _normalize_spec_dict(json.loads(row["spec"])),
            "team_revision": row["team_revision"],
            "change_note": row["change_note"],
            "proposal_id": row["proposal_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _feedback_row(row):
        return {
            "id": row["id"],
            "routine_id": row["routine_id"],
            "occurrence_id": row["occurrence_id"],
            "project_id": row["project_id"] or None,
            "room_id": row["room_id"],
            "verdict": row["verdict"],
            "source": row["source"],
            "note": row["note"],
            "correlation_key": row["correlation_key"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _proposal_row(row):
        return {
            "id": row["id"],
            "routine_id": row["routine_id"],
            "project_id": row["project_id"] or None,
            "room_id": row["room_id"],
            "base_revision": row["base_revision"],
            "status": row["status"],
            "trigger_source": row["trigger_source"],
            "diagnosis": row["diagnosis"],
            "changes_summary": json.loads(row["changes_summary"]),
            "proposed_spec": _normalize_spec_dict(json.loads(row["proposed_spec"])),
            "evidence_run_ids": json.loads(row["evidence_run_ids"]),
            "applied_revision": row["applied_revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row(database, scope, routine_id):
        row = database.execute(
            """SELECT * FROM routines
               WHERE project_id = ? AND room_id = ? AND id = ? AND deleted_at IS NULL""",
            (*scope, _uuid(routine_id)),
        ).fetchone()
        if row is None:
            raise RoutineNotFound("运行计划不存在")
        return row

    @staticmethod
    def _check_revision(row, expected_revision):
        if row["revision"] != expected_revision:
            raise RoutineConflict("运行计划已更新，请刷新后重试")

    @staticmethod
    def _check_native(spec, allow_native):
        if spec.get("allow_native") and not allow_native:
            raise RoutineForbidden("需要在本机管理可运行本机工具的计划")

    @staticmethod
    def _find_occurrence(database, routine_id, key):
        return database.execute(
            "SELECT * FROM routine_occurrences WHERE routine_id = ? AND occurrence_key = ?",
            (routine_id, key),
        ).fetchone()

    @staticmethod
    def _save_revision(database, row, change_note, timestamp, *, proposal_id=None):
        database.execute(
            """INSERT OR REPLACE INTO routine_revisions
               (id, routine_id, project_id, room_id, revision, spec, team_revision, change_note, proposal_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), row["id"], row["project_id"], row["room_id"], row["revision"],
             row["spec"], row["team_revision"], change_note, proposal_id, timestamp),
        )

    def _next_run(self, database, routine_id, spec, instant, enabled_at=None):
        schedule = _schedule_for(spec, enabled_at or instant)
        if schedule is None:
            return None
        return _iso(schedule.next_after(
            instant,
            lambda key: self._find_occurrence(database, routine_id, key) is not None,
        ))

    def create(self, project_id, room_id, spec, team_revision, request_id, *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        request_id = _uuid(request_id)
        spec = _spec(spec)
        self._check_native(spec, allow_native)
        payload = _json(spec)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            previous = database.execute(
                """SELECT * FROM routines
                   WHERE project_id = ? AND room_id = ? AND create_request_id = ?""",
                (*scope, request_id),
            ).fetchone()
            if previous is not None:
                if _json(_spec(_normalize_spec_dict(json.loads(previous["create_spec"])))) != payload:
                    raise RoutineConflict("此请求编号已用于不同的运行计划")
                if previous["deleted_at"] is not None:
                    raise RoutineConflict("此请求创建的运行计划已删除")
                self._check_native(json.loads(previous["spec"]), allow_native)
                return self._routine(previous)
            routine_id = str(uuid4())
            database.execute(
                """INSERT INTO routines (id, project_id, room_id, create_request_id, create_spec,
                   spec, team_revision, revision, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)""",
                (routine_id, *scope, request_id, payload, payload, team_revision, timestamp, timestamp),
            )
            created = self._row(database, scope, routine_id)
            self._save_revision(database, created, "初始创建", timestamp)
            return self._routine(created)

    def list(self, project_id, room_id):
        with self._connect() as database:
            rows = database.execute(
                """SELECT * FROM routines WHERE project_id = ? AND room_id = ? AND deleted_at IS NULL
                   ORDER BY created_at, id""",
                _scope(project_id, room_id),
            )
            return [self._routine(row) for row in rows]

    def get(self, project_id, room_id, routine_id):
        with self._connect() as database:
            return self._routine(self._row(database, _scope(project_id, room_id), routine_id))

    def update(self, project_id, room_id, routine_id, spec, team_revision, expected_revision,
               *, now=None, allow_native=True, change_note="手动更新"):
        scope = _scope(project_id, room_id)
        spec = _spec(spec)
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_revision(row, expected_revision)
            self._check_native(json.loads(row["spec"]), allow_native)
            self._check_native(spec, allow_native)
            enabled = bool(row["enabled"]) and spec["trigger"]["kind"] != "manual"
            enabled_at = timestamp if enabled else None
            next_run = self._next_run(database, row["id"], spec, instant, enabled_at) if enabled else None
            database.execute(
                """UPDATE routines SET spec = ?, team_revision = ?, revision = revision + 1, enabled = ?,
                   enabled_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
                (_json(spec), team_revision, enabled, enabled_at,
                 next_run, timestamp, row["id"]),
            )
            updated = self._row(database, scope, routine_id)
            self._save_revision(database, updated, change_note, timestamp)
            return self._routine(updated)

    def set_enabled(self, project_id, room_id, routine_id, enabled, expected_revision,
                    *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_revision(row, expected_revision)
            spec = _normalize_spec_dict(json.loads(row["spec"]))
            self._check_native(spec, allow_native)
            if enabled and spec["trigger"]["kind"] == "manual":
                raise RoutineConflict("仅手动运行的计划无需启用")
            if bool(row["enabled"]) == enabled:
                return self._routine(row)
            enabled_at = timestamp if enabled else None
            next_run = self._next_run(database, row["id"], spec, instant, enabled_at) if enabled else None
            database.execute(
                """UPDATE routines SET enabled = ?, revision = revision + 1,
                   enabled_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
                (enabled, enabled_at, next_run, timestamp, row["id"]),
            )
            return self._routine(self._row(database, scope, routine_id))

    def delete(self, project_id, room_id, routine_id, expected_revision, *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_revision(row, expected_revision)
            self._check_native(json.loads(row["spec"]), allow_native)
            database.execute(
                """UPDATE routines SET deleted_at = ?, enabled = 0, enabled_at = NULL,
                   next_run_at = NULL, revision = revision + 1, updated_at = ? WHERE id = ?""",
                (timestamp, timestamp, row["id"]),
            )

    def revisions(self, project_id, room_id, routine_id, limit=20):
        scope = _scope(project_id, room_id)
        with self._connect() as database:
            row = self._row(database, scope, routine_id)
            rows = database.execute(
                """SELECT * FROM routine_revisions WHERE routine_id = ?
                   ORDER BY revision DESC LIMIT ?""",
                (row["id"], max(1, min(limit, 50))),
            ).fetchall()
            return [self._revision_row(item) for item in rows]

    @staticmethod
    def _routine_busy(database, routine_id):
        return database.execute(
            """SELECT 1 FROM routine_occurrences
               WHERE routine_id = ? AND status IN ('pending', 'submitted') LIMIT 1""",
            (routine_id,),
        ).fetchone() is not None

    @staticmethod
    def _correlation_busy(database, routine_id, correlation_key):
        if not correlation_key:
            return None
        row = database.execute(
            """SELECT id, status, case_state FROM routine_occurrences
               WHERE routine_id = ? AND correlation_key = ?
               AND (status IN ('pending', 'submitted') OR case_state = 'waiting_details')
               ORDER BY created_at DESC LIMIT 1""",
            (routine_id, correlation_key),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _prior_case_summary(database, routine_id, correlation_key):
        if not correlation_key:
            return None
        prior = database.execute(
            """SELECT id, status, case_state, detail, created_at FROM routine_occurrences
               WHERE routine_id = ? AND correlation_key = ? AND status NOT IN ('pending', 'submitted', 'skipped')
               ORDER BY created_at DESC, rowid DESC LIMIT 2""",
            (routine_id, correlation_key),
        ).fetchall()
        feedback = database.execute(
            """SELECT verdict, note, created_at FROM routine_feedback
               WHERE routine_id = ? AND correlation_key = ?
               ORDER BY created_at DESC LIMIT 2""",
            (routine_id, correlation_key),
        ).fetchall()
        lines = []
        for item in prior:
            tag = "等待补充信息" if item["case_state"] == "waiting_details" else item["status"]
            detail = (item["detail"] or "").strip()[:140]
            lines.append(f"- 历史运行 {item['created_at'][:16]}（{tag}）" + (f"：{detail}" if detail else ""))
        for item in feedback:
            note = (item["note"] or "").strip()[:160]
            lines.append(f"- 历史反馈（{item['verdict']}）：{note}" if note else f"- 历史反馈：{item['verdict']}")
        if not lines:
            return None
        return "\n".join(lines)[:600]

    def has_room(self, project_id, room_id):
        scope = _scope(project_id, room_id)
        with self._connect() as database:
            return database.execute(
                """SELECT 1 FROM routines AS r WHERE project_id = ? AND room_id = ?
                   AND (deleted_at IS NULL OR EXISTS (SELECT 1 FROM routine_occurrences
                       WHERE routine_id = r.id AND status IN ('pending', 'submitted')))
                   LIMIT 1""",
                scope,
            ).fetchone() is not None

    def has_board_routines(self):
        with self._connect() as database:
            return database.execute(
                """SELECT 1 FROM routines WHERE enabled = 1 AND deleted_at IS NULL
                   AND json_extract(spec, '$.trigger.kind') = 'board_card' LIMIT 1"""
            ).fetchone() is not None

    def has_board_status_routines(self):
        with self._connect() as database:
            return database.execute(
                """SELECT 1 FROM routines WHERE enabled = 1 AND deleted_at IS NULL
                   AND json_extract(spec, '$.trigger.kind') = 'board_status' LIMIT 1"""
            ).fetchone() is not None

    def _record_occurrence(self, database, row, key, kind, scheduled_at, created_at, event=None,
                           skip_reason=None, *, correlation_key=None, resume_case=False):
        scope = (row["project_id"], row["room_id"])
        correlation_key = clean_correlation_key(correlation_key)
        event_payload = dict(event) if event else None
        if skip_reason is None and correlation_key:
            active_case = self._correlation_busy(database, row["id"], correlation_key)
            if active_case is not None:
                if active_case["status"] in OPEN_STATUSES:
                    skip_reason = f"关联编号 {correlation_key} 已有正在运行或等待的任务"
                    if kind == "manual":
                        raise RoutineBusy(skip_reason)
                elif active_case["case_state"] == "waiting_details" and not resume_case:
                    skip_reason = f"关联编号 {correlation_key} 正在等待补充信息，避免重复创建；如需续办请显式继续"
                    if kind == "manual":
                        raise RoutineBusy(skip_reason)
        if skip_reason is None and kind not in ("board_card", "board_status") and self._routine_busy(database, row["id"]):
            skip_reason = "此计划已有正在运行或等待的任务"
            if kind == "manual":
                raise RoutineBusy(skip_reason)
        if skip_reason is None and correlation_key:
            prior_context = self._prior_case_summary(database, row["id"], correlation_key)
            if prior_context:
                event_payload = {**(event_payload or {}), "prior_case_context": prior_context}
            if resume_case:
                database.execute(
                    """UPDATE routine_occurrences SET case_state = 'resolved', updated_at = ?
                       WHERE routine_id = ? AND correlation_key = ? AND case_state = 'waiting_details'""",
                    (created_at, row["id"], correlation_key),
                )
        status = "skipped" if skip_reason else "pending"
        finished_at = created_at if status == "skipped" else None
        occurrence_id = str(uuid4())
        database.execute(
            """INSERT INTO routine_occurrences (id, routine_id, project_id, room_id, occurrence_key,
               kind, created_at, scheduled_at, updated_at, finished_at, correlation_key, spec,
               team_revision, routine_revision, event, status, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (occurrence_id, row["id"], *scope, key, kind, created_at, scheduled_at, created_at,
             finished_at, correlation_key, row["spec"], row["team_revision"], row["revision"],
             _json(event_payload) if event_payload else None, status, skip_reason),
        )
        return self._occurrence(self._find_occurrence(database, row["id"], key))

    def manual_run(self, project_id, room_id, routine_id, request_id, *, now=None, allow_native=True,
                   correlation_key=None, input_note=None, resume_case=False):
        scope = _scope(project_id, room_id)
        routine_id = _uuid(routine_id)
        key = f"manual:{_uuid(request_id)}"
        correlation_key = clean_correlation_key(correlation_key)
        note = (input_note or "").strip()[:1000] if input_note else ""
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            previous = database.execute(
                """SELECT * FROM routine_occurrences WHERE project_id = ? AND room_id = ?
                   AND kind = 'manual' AND occurrence_key = ?""",
                (*scope, key),
            ).fetchone()
            if previous is not None:
                if previous["routine_id"] != routine_id:
                    raise RoutineConflict("此请求编号已用于其他运行计划")
                self._check_native(json.loads(previous["spec"]), allow_native)
                return self._occurrence(previous)
            row = self._row(database, scope, routine_id)
            self._check_native(json.loads(row["spec"]), allow_native)
            event = {"input_note": note} if note else None
            return self._record_occurrence(
                database, row, key, "manual", timestamp, timestamp, event=event,
                correlation_key=correlation_key, resume_case=resume_case,
            )

    def claim_scheduled(self, *, now=None):
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect() as database:
            if database.execute(_DUE_QUERY + " LIMIT 1", (timestamp,)).fetchone() is None:
                return []
        pending = []
        with self._connect(write=True) as database:
            for row in database.execute(_DUE_QUERY, (timestamp,)).fetchall():
                spec = _normalize_spec_dict(json.loads(row["spec"]))
                schedule = _schedule_for(spec, row["enabled_at"] or row["next_run_at"])
                if schedule is None:
                    continue
                due = schedule.latest_due(instant, _utc(row["next_run_at"]))
                if due is not None:
                    key, scheduled_at = due
                    if self._find_occurrence(database, row["id"], key) is None:
                        claim = self._record_occurrence(
                            database, row, key, spec["trigger"]["kind"], _iso(scheduled_at), timestamp
                        )
                        if claim["status"] == "pending":
                            pending.append(claim)
                database.execute(
                    "UPDATE routines SET next_run_at = ? WHERE id = ?",
                    (self._next_run(database, row["id"], spec, instant, row["enabled_at"]), row["id"]),
                )
        return pending

    def claim_board_card(self, project_id, room_id, card_id, tags: Iterable[str], created_at,
                         *, title="", description="", trusted_local=False, now=None):
        return self.claim_board_cards([{
            "id": card_id, "project_id": project_id, "room_id": room_id, "tags": tags,
            "created_at": created_at, "title": title, "description": description,
            "local": trusted_local,
        }], now=now)

    @staticmethod
    def _board_candidates(database, scope, key, event):
        return database.execute(
            """SELECT * FROM routines AS r WHERE project_id = ? AND room_id = ?
               AND enabled = 1 AND deleted_at IS NULL AND enabled_at < ?
               AND json_extract(spec, '$.trigger.kind') = 'board_card'
               AND json_extract(spec, '$.trigger.tag') IN (SELECT value FROM json_each(?))
               AND NOT EXISTS (SELECT 1 FROM routine_occurrences
                               WHERE routine_id = r.id AND occurrence_key = ?)
               ORDER BY created_at, id""",
            (*scope, event["created_at"], _json(event["tags"]), key),
        ).fetchall()

    def claim_board_cards(self, events, *, now=None):
        """Match snapshots from the board's trusted create path in creation order."""
        cards = []
        for item in events:
            scope = _scope(item["project_id"], item["room_id"])
            card_id = _uuid(item["id"])
            event = {
                "card_id": card_id, "tags": list(item["tags"]), "created_at": _iso(_utc(item["created_at"])),
                "title": item["title"], "description": item["description"], "trusted_local": item["local"] is True,
            }
            cards.append((scope, f"board:{card_id}", event))
        if not cards:
            return []
        cards.sort(key=lambda card: card[2]["created_at"])
        with self._connect() as database:
            if not any(self._board_candidates(database, *card) for card in cards):
                return []
        pending = []
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            for scope, key, event in cards:
                for row in self._board_candidates(database, scope, key, event):
                    spec = _normalize_spec_dict(json.loads(row["spec"]))
                    skip_reason = None
                    if spec["allow_native"] and not event["trusted_local"]:
                        skip_reason = "手机创建的卡片不能触发需要本机执行权限的例行任务"
                    correlation_key = extract_correlation_key(spec, event["title"], event["description"])
                    claim = self._record_occurrence(
                        database, row, key, "board_card", event["created_at"], timestamp, event,
                        skip_reason=skip_reason, correlation_key=correlation_key,
                    )
                    if claim["status"] == "pending":
                        pending.append(claim)
        return pending

    @staticmethod
    def _board_status_candidates(database, scope, key, event):
        return database.execute(
            """SELECT * FROM routines AS r WHERE project_id = ? AND room_id = ?
               AND enabled = 1 AND deleted_at IS NULL AND enabled_at < ?
               AND json_extract(spec, '$.trigger.kind') = 'board_status'
               AND json_extract(spec, '$.trigger.board_status') = ?
               AND (
                   json_extract(spec, '$.trigger.tag') IS NULL
                   OR json_extract(spec, '$.trigger.tag') = ''
                   OR json_extract(spec, '$.trigger.tag') IN (SELECT value FROM json_each(?))
               )
               AND NOT EXISTS (SELECT 1 FROM routine_occurrences
                               WHERE routine_id = r.id AND occurrence_key = ?)
               ORDER BY created_at, id""",
            (*scope, event["changed_at"], event["status"], _json(event["tags"]), key),
        ).fetchall()

    def claim_board_status_events(self, events, *, now=None):
        """Match trusted manual board column transitions in chronological order."""
        transitions = []
        for item in events:
            scope = _scope(item["project_id"], item["room_id"])
            card_id = _uuid(item["id"])
            event_id = _uuid(item["event_id"])
            event = {
                "event_id": event_id,
                "card_id": card_id,
                "status": item["status"],
                "tags": list(item.get("tags") or []),
                "changed_at": _iso(_utc(item["changed_at"])),
                "title": item["title"],
                "description": item.get("description") or "",
                "trusted_local": item.get("local") is True,
            }
            transitions.append((scope, f"board_status:{event_id}", event))
        if not transitions:
            return []
        transitions.sort(key=lambda entry: entry[2]["changed_at"])
        with self._connect() as database:
            if not any(self._board_status_candidates(database, *entry) for entry in transitions):
                return []
        pending = []
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            for scope, key, event in transitions:
                for row in self._board_status_candidates(database, scope, key, event):
                    spec = _normalize_spec_dict(json.loads(row["spec"]))
                    skip_reason = None
                    if spec["allow_native"] and not event["trusted_local"]:
                        skip_reason = "手机变更的看板状态不能触发需要本机执行权限的例行任务"
                    correlation_key = extract_correlation_key(spec, event["title"], event["description"])
                    claim = self._record_occurrence(
                        database, row, key, "board_status", event["changed_at"], timestamp, event,
                        skip_reason=skip_reason, correlation_key=correlation_key,
                    )
                    if claim["status"] == "pending":
                        pending.append(claim)
        return pending

    def history(self, project_id, room_id, routine_id, limit=10):
        with self._connect() as database:
            row = self._row(database, _scope(project_id, room_id), routine_id)
            rows = database.execute(
                """SELECT * FROM routine_occurrences WHERE routine_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (row["id"], max(0, min(limit, 50))),
            )
            return [self._occurrence(item) for item in rows]

    def active(self):
        with self._connect() as database:
            rows = database.execute(
                """SELECT * FROM routine_occurrences AS r WHERE status IN ('pending', 'submitted')
                   AND (status = 'submitted' OR NOT EXISTS (
                       SELECT 1 FROM routine_occurrences AS other
                       WHERE other.routine_id = r.routine_id AND other.status IN ('pending', 'submitted')
                       AND (other.status = 'submitted' OR other.rowid < r.rowid)))
                   ORDER BY created_at, rowid"""
            )
            return [self._occurrence(row) for row in rows]

    def pending(self):
        return [row for row in self.active() if row["status"] == "pending"]

    def finish(self, occurrence_id, status, *, run_id=None, detail=None, now=None):
        if not isinstance(status, str) or not status or status == "pending":
            raise ValueError("运行结果状态无效")
        if status == "submitted" and not run_id:
            raise ValueError("已提交的运行需要任务编号")
        occurrence_id = _uuid(occurrence_id)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = database.execute(
                "SELECT * FROM routine_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise RoutineNotFound("运行记录不存在")
            if row["run_id"] and run_id and row["run_id"] != run_id:
                raise RoutineConflict("运行记录已绑定其他任务")
            if row["status"] not in OPEN_STATUSES or row["status"] == status:
                return self._occurrence(row)
            if status == "submitted":
                earlier = database.execute(
                    """SELECT 1 FROM routine_occurrences AS other
                       WHERE other.routine_id = ? AND other.status IN ('pending', 'submitted')
                       AND (other.status = 'submitted' OR other.rowid < (
                           SELECT rowid FROM routine_occurrences WHERE id = ?)) LIMIT 1""",
                    (row["routine_id"], row["id"]),
                ).fetchone()
                if earlier is not None:
                    raise RoutineBusy("此计划正在运行更早的任务")
            dispatched_at = timestamp if status == "submitted" else None
            finished_at = timestamp if status not in OPEN_STATUSES else None
            database.execute(
                """UPDATE routine_occurrences SET status = ?, updated_at = ?,
                   dispatched_at = COALESCE(dispatched_at, ?),
                   finished_at = COALESCE(finished_at, ?),
                   run_id = COALESCE(run_id, ?), detail = ? WHERE id = ?""",
                (status, timestamp, dispatched_at, finished_at, run_id, detail, occurrence_id),
            )
            return self._occurrence(database.execute(
                "SELECT * FROM routine_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone())

    def set_case_state(self, project_id, room_id, routine_id, occurrence_id, case_state,
                       *, correlation_key=None, now=None, allow_native=True):
        if case_state not in ("waiting_details", "resolved"):
            raise ValueError("无效的事项状态")
        scope = _scope(project_id, room_id)
        occurrence_id = _uuid(occurrence_id)
        correlation_key = clean_correlation_key(correlation_key)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_native(json.loads(row["spec"]), allow_native)
            occ = database.execute(
                """SELECT * FROM routine_occurrences
                   WHERE id = ? AND routine_id = ? AND project_id = ? AND room_id = ?""",
                (occurrence_id, row["id"], *scope),
            ).fetchone()
            if occ is None:
                raise RoutineNotFound("运行记录不存在")
            final_key = correlation_key or occ["correlation_key"]
            database.execute(
                """UPDATE routine_occurrences SET case_state = ?, correlation_key = ?, updated_at = ?
                   WHERE id = ?""",
                (case_state, final_key, timestamp, occ["id"]),
            )
            return self._occurrence(database.execute(
                "SELECT * FROM routine_occurrences WHERE id = ?", (occ["id"],)
            ).fetchone())

    def add_feedback(self, project_id, room_id, routine_id, occurrence_id, verdict, note,
                     *, source="user", correlation_key=None, case_state=None, now=None, allow_native=True):
        if verdict not in ("good", "needs_improvement", "wrong_judgment"):
            raise ValueError("反馈结论无效")
        if source not in ("user", "review", "external"):
            raise ValueError("反馈来源无效")
        if case_state is not None and case_state not in ("waiting_details", "resolved"):
            raise ValueError("事项状态无效")
        text = (note or "").strip()
        if len(text) > 2000:
            raise ValueError("反馈说明不能超过 2000 字")
        if verdict != "good" and not text:
            raise ValueError("请写明具体问题或希望怎么改进")
        scope = _scope(project_id, room_id)
        occurrence_id = _uuid(occurrence_id)
        correlation_key = clean_correlation_key(correlation_key)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_native(json.loads(row["spec"]), allow_native)
            occ = database.execute(
                """SELECT * FROM routine_occurrences
                   WHERE id = ? AND routine_id = ? AND project_id = ? AND room_id = ?""",
                (occurrence_id, row["id"], *scope),
            ).fetchone()
            if occ is None:
                raise RoutineNotFound("运行记录不存在")
            final_key = correlation_key or occ["correlation_key"]
            feedback_id = str(uuid4())
            database.execute(
                """INSERT INTO routine_feedback
                   (id, routine_id, occurrence_id, project_id, room_id, verdict, source, note, correlation_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (feedback_id, row["id"], occ["id"], *scope, verdict, source, text, final_key, timestamp),
            )
            if case_state is not None or (final_key and not occ["correlation_key"]):
                database.execute(
                    """UPDATE routine_occurrences SET case_state = COALESCE(?, case_state),
                       correlation_key = COALESCE(correlation_key, ?), updated_at = ? WHERE id = ?""",
                    (case_state, final_key, timestamp, occ["id"]),
                )
            saved = database.execute("SELECT * FROM routine_feedback WHERE id = ?", (feedback_id,)).fetchone()
            return self._feedback_row(saved)

    def list_feedback(self, project_id, room_id, routine_id, limit=30):
        scope = _scope(project_id, room_id)
        with self._connect() as database:
            row = self._row(database, scope, routine_id)
            rows = database.execute(
                """SELECT * FROM routine_feedback WHERE routine_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (row["id"], max(1, min(limit, 100))),
            ).fetchall()
            return [self._feedback_row(item) for item in rows]

    def feedback_by_occurrence(self, routine_id, occurrence_ids):
        ids = [str(item) for item in occurrence_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as database:
            rows = database.execute(
                f"""SELECT * FROM routine_feedback WHERE routine_id = ? AND occurrence_id IN ({placeholders})
                    ORDER BY created_at DESC, rowid DESC""",
                (str(routine_id), *ids),
            ).fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["occurrence_id"], []).append(self._feedback_row(row))
        return grouped

    def save_proposal(self, project_id, room_id, routine_id, base_revision, diagnosis, changes_summary,
                      proposed_spec, evidence_run_ids, *, trigger_source="manual", now=None):
        scope = _scope(project_id, room_id)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            current = _normalize_spec_dict(json.loads(row["spec"]))
            # Security invariant: self-improvement proposals can never escalate allow_native.
            merged = {**current, **proposed_spec, "allow_native": bool(current["allow_native"])}
            validated = _spec(merged)
            proposal_id = str(uuid4())
            database.execute(
                """INSERT INTO routine_proposals
                   (id, routine_id, project_id, room_id, base_revision, status, trigger_source,
                    diagnosis, changes_summary, proposed_spec, evidence_run_ids, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)""",
                (proposal_id, row["id"], *scope, int(base_revision), trigger_source,
                 diagnosis.strip()[:3000], _json(list(changes_summary)[:12]),
                 _json(validated), _json([str(item) for item in evidence_run_ids][:20]),
                 timestamp, timestamp),
            )
            saved = database.execute("SELECT * FROM routine_proposals WHERE id = ?", (proposal_id,)).fetchone()
            return self._proposal_row(saved)

    def list_proposals(self, project_id, room_id, routine_id, *, status=None, limit=10):
        scope = _scope(project_id, room_id)
        with self._connect() as database:
            row = self._row(database, scope, routine_id)
            if status is not None:
                rows = database.execute(
                    """SELECT * FROM routine_proposals WHERE routine_id = ? AND status = ?
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (row["id"], status, max(1, min(limit, 50))),
                ).fetchall()
            else:
                rows = database.execute(
                    """SELECT * FROM routine_proposals WHERE routine_id = ?
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (row["id"], max(1, min(limit, 50))),
                ).fetchall()
            return [self._proposal_row(item) for item in rows]

    def has_proposal_for_run(self, routine_id, occurrence_id):
        with self._connect() as database:
            rows = database.execute(
                """SELECT evidence_run_ids FROM routine_proposals
                   WHERE routine_id = ? AND status IN ('open', 'applied')""",
                (str(routine_id),),
            ).fetchall()
            target = str(occurrence_id)
            return any(target in json.loads(row["evidence_run_ids"]) for row in rows)

    def apply_proposal(self, project_id, room_id, routine_id, proposal_id, team_revision, expected_revision,
                       *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        proposal_id = _uuid(proposal_id)
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_revision(row, expected_revision)
            current = _normalize_spec_dict(json.loads(row["spec"]))
            self._check_native(current, allow_native)
            proposal = database.execute(
                """SELECT * FROM routine_proposals
                   WHERE id = ? AND routine_id = ? AND project_id = ? AND room_id = ?""",
                (proposal_id, row["id"], *scope),
            ).fetchone()
            if proposal is None:
                raise RoutineNotFound("改进建议不存在")
            if proposal["status"] != "open":
                raise RoutineConflict("此改进建议已处理，不能重复采纳")
            proposed = _normalize_spec_dict(json.loads(proposal["proposed_spec"]))
            # Never permit a proposal to flip allow_native upward.
            proposed["allow_native"] = bool(current["allow_native"])
            spec = _spec(proposed)
            self._check_native(spec, allow_native)
            enabled = bool(row["enabled"]) and spec["trigger"]["kind"] != "manual"
            enabled_at = row["enabled_at"] if enabled and row["enabled_at"] else (timestamp if enabled else None)
            next_run = self._next_run(database, row["id"], spec, instant, enabled_at) if enabled else None
            database.execute(
                """UPDATE routines SET spec = ?, team_revision = ?, revision = revision + 1, enabled = ?,
                   enabled_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
                (_json(spec), team_revision, enabled, enabled_at, next_run, timestamp, row["id"]),
            )
            updated = self._row(database, scope, routine_id)
            self._save_revision(database, updated, "采纳自改进建议", timestamp, proposal_id=proposal_id)
            database.execute(
                """UPDATE routine_proposals SET status = 'applied', applied_revision = ?, updated_at = ?
                   WHERE id = ?""",
                (updated["revision"], timestamp, proposal_id),
            )
            saved_proposal = database.execute("SELECT * FROM routine_proposals WHERE id = ?", (proposal_id,)).fetchone()
            return self._routine(updated), self._proposal_row(saved_proposal)

    def dismiss_proposal(self, project_id, room_id, routine_id, proposal_id, *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        proposal_id = _uuid(proposal_id)
        timestamp = _iso(self._now(now))
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_native(json.loads(row["spec"]), allow_native)
            proposal = database.execute(
                """SELECT * FROM routine_proposals
                   WHERE id = ? AND routine_id = ? AND project_id = ? AND room_id = ?""",
                (proposal_id, row["id"], *scope),
            ).fetchone()
            if proposal is None:
                raise RoutineNotFound("改进建议不存在")
            if proposal["status"] != "open":
                return self._proposal_row(proposal)
            database.execute(
                "UPDATE routine_proposals SET status = 'dismissed', updated_at = ? WHERE id = ?",
                (timestamp, proposal_id),
            )
            saved = database.execute("SELECT * FROM routine_proposals WHERE id = ?", (proposal_id,)).fetchone()
            return self._proposal_row(saved)

    def telemetry(self, project_id, room_id, routine_id):
        scope = _scope(project_id, room_id)
        with self._connect() as database:
            row = self._row(database, scope, routine_id)
            occurrences = database.execute(
                """SELECT id, status, created_at, dispatched_at, finished_at, correlation_key, case_state, detail, updated_at
                   FROM routine_occurrences WHERE routine_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 100""",
                (row["id"],),
            ).fetchall()
            feedback = database.execute(
                "SELECT verdict, created_at FROM routine_feedback WHERE routine_id = ? ORDER BY created_at DESC LIMIT 100",
                (row["id"],),
            ).fetchall()
            proposals = database.execute(
                "SELECT status, created_at FROM routine_proposals WHERE routine_id = ? ORDER BY created_at DESC LIMIT 20",
                (row["id"],),
            ).fetchall()

        counts = {"total": len(occurrences), "completed": 0, "needs_attention": 0, "skipped": 0, "active": 0, "cancelled": 0}
        queue_times = []
        run_times = []
        waiting_cases = []
        seen_waiting = set()
        latest_signal = None

        for item in occurrences:
            status = item["status"]
            if status == "complete":
                counts["completed"] += 1
            elif status == "needs_attention":
                counts["needs_attention"] += 1
                if latest_signal is None or item["updated_at"] > latest_signal:
                    latest_signal = item["updated_at"]
            elif status == "skipped":
                counts["skipped"] += 1
            elif status == "cancelled":
                counts["cancelled"] += 1
            elif status in OPEN_STATUSES:
                counts["active"] += 1
            q_sec = duration_seconds(item["created_at"], item["dispatched_at"])
            if q_sec is not None:
                queue_times.append(q_sec)
            r_sec = duration_seconds(item["dispatched_at"], item["finished_at"])
            if r_sec is not None:
                run_times.append(r_sec)
            if item["case_state"] == "waiting_details":
                key = item["correlation_key"] or item["id"][:8]
                if key not in seen_waiting:
                    seen_waiting.add(key)
                    waiting_cases.append({"occurrence_id": item["id"], "correlation_key": key, "updated_at": item["updated_at"]})

        feedback_counts = {"good": 0, "needs_improvement": 0, "wrong_judgment": 0}
        for item in feedback:
            verdict = item["verdict"]
            if verdict in feedback_counts:
                feedback_counts[verdict] += 1
            if verdict != "good" and (latest_signal is None or item["created_at"] > latest_signal):
                latest_signal = item["created_at"]

        open_proposals = sum(1 for item in proposals if item["status"] == "open")
        latest_proposal_at = proposals[0]["created_at"] if proposals else None
        executed = counts["completed"] + counts["needs_attention"] + counts["cancelled"]
        success_rate = round(counts["completed"] / executed, 3) if executed else None
        avg_queue = round(sum(queue_times) / len(queue_times), 1) if queue_times else None
        avg_run = round(sum(run_times) / len(run_times), 1) if run_times else None
        needs_reflection = bool(
            latest_signal and (not latest_proposal_at or latest_signal > latest_proposal_at)
            and latest_signal >= row["updated_at"]
        )
        return {
            "total_runs": counts["total"],
            "completed": counts["completed"],
            "needs_attention": counts["needs_attention"],
            "skipped": counts["skipped"],
            "active": counts["active"],
            "cancelled": counts["cancelled"],
            "success_rate": success_rate,
            "avg_queue_seconds": avg_queue,
            "avg_run_seconds": avg_run,
            "feedback_counts": feedback_counts,
            "open_proposals": open_proposals,
            "waiting_cases": waiting_cases[:10],
            "needs_reflection": needs_reflection,
        }
