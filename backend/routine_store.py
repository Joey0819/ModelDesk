"""Scoped routine settings, trigger cursors, and durable dispatch receipts."""
from collections.abc import Iterable
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, time, timedelta
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


TriggerKind = Literal["manual", "daily", "weekly", "board_card"]
DocumentFormat = Literal["docx", "pdf", "html"]
OPEN_STATUSES = ("pending", "submitted")


class RoutineTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind = "manual"
    time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    weekday: Annotated[int, Field(strict=True, ge=0, le=6)] | None = None
    tag: str | None = Field(default=None, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("请选择有效的 IANA 时区") from None
        return value

    @model_validator(mode="after")
    def validate_trigger_fields(self):
        if self.kind in ("daily", "weekly") and (self.time is None or self.timezone is None):
            raise ValueError("定时运行需要时间和时区")
        if self.kind == "weekly" and self.weekday is None:
            raise ValueError("每周运行需要指定星期")
        if self.kind == "board_card" and not (self.tag or "").strip():
            raise ValueError("看板触发需要指定标签")
        return self


class RoutineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=3000)
    process: str = Field(default="", max_length=2500)
    checks: str = Field(default="", max_length=2500)
    trigger: RoutineTrigger = Field(default_factory=RoutineTrigger)
    document_formats: list[DocumentFormat] = Field(default_factory=list, max_length=3)
    allow_native: StrictBool = False

    @field_validator("document_formats")
    @classmethod
    def unique_formats(cls, value):
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_text_budget(self):
        if len(self.goal) + len(self.process) + len(self.checks) > 7000:
            raise ValueError("目标、过程和验收标准合计不能超过 7000 字")
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

    def next_after(self, instant, used_dates):
        day = instant.astimezone(self.zone).date()
        if self.weekday is not None:
            day += timedelta(days=(self.weekday - day.weekday()) % 7)
        while True:
            candidate = self._on(day)
            if candidate is not None and candidate > instant and not used_dates(day):
                return candidate
            day += self.interval

    def latest_due(self, instant, earliest):
        day = instant.astimezone(self.zone).date()
        if self.weekday is not None:
            day -= timedelta(days=(day.weekday() - self.weekday) % 7)
        while day >= earliest.astimezone(self.zone).date():
            candidate = self._on(day)
            if candidate is not None and earliest <= candidate <= instant:
                return day, candidate
            day -= self.interval
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
                        database.executescript(SCHEMA)
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
        return {
            **json.loads(row["spec"]),
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
        if spec["allow_native"] and not allow_native:
            raise RoutineForbidden("需要在本机管理可运行本机工具的计划")

    @staticmethod
    def _find_occurrence(database, routine_id, key):
        return database.execute(
            "SELECT * FROM routine_occurrences WHERE routine_id = ? AND occurrence_key = ?",
            (routine_id, key),
        ).fetchone()

    def _next_run(self, database, routine_id, spec, instant):
        trigger = spec["trigger"]
        if trigger["kind"] not in ("daily", "weekly"):
            return None
        schedule = _WallSchedule(trigger)
        return _iso(schedule.next_after(
            instant,
            lambda day: self._find_occurrence(database, routine_id, f"schedule:{day}") is not None,
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
                if previous["create_spec"] != payload:
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
            return self._routine(self._row(database, scope, routine_id))

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
               *, now=None, allow_native=True):
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
            next_run = self._next_run(database, row["id"], spec, instant) if enabled else None
            database.execute(
                """UPDATE routines SET spec = ?, team_revision = ?, revision = revision + 1, enabled = ?,
                   enabled_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
                (_json(spec), team_revision, enabled, timestamp if enabled else None,
                 next_run, timestamp, row["id"]),
            )
            return self._routine(self._row(database, scope, routine_id))

    def set_enabled(self, project_id, room_id, routine_id, enabled, expected_revision,
                    *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect(write=True) as database:
            row = self._row(database, scope, routine_id)
            self._check_revision(row, expected_revision)
            spec = json.loads(row["spec"])
            self._check_native(spec, allow_native)
            if enabled and spec["trigger"]["kind"] == "manual":
                raise RoutineConflict("仅手动运行的计划无需启用")
            if bool(row["enabled"]) == enabled:
                return self._routine(row)
            next_run = self._next_run(database, row["id"], spec, instant) if enabled else None
            database.execute(
                """UPDATE routines SET enabled = ?, revision = revision + 1,
                   enabled_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?""",
                (enabled, timestamp if enabled else None, next_run, timestamp, row["id"]),
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

    @staticmethod
    def _routine_busy(database, routine_id):
        return database.execute(
            """SELECT 1 FROM routine_occurrences
               WHERE routine_id = ? AND status IN ('pending', 'submitted') LIMIT 1""",
            (routine_id,),
        ).fetchone() is not None

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

    def _record_occurrence(self, database, row, key, kind, scheduled_at, created_at, event=None,
                           skip_reason=None):
        scope = (row["project_id"], row["room_id"])
        if skip_reason is None and kind != "board_card" and self._routine_busy(database, row["id"]):
            skip_reason = "此计划已有正在运行或等待的任务"
            if kind == "manual":
                raise RoutineBusy(skip_reason)
        status = "skipped" if skip_reason else "pending"
        occurrence_id = str(uuid4())
        database.execute(
            """INSERT INTO routine_occurrences (id, routine_id, project_id, room_id, occurrence_key,
               kind, created_at, scheduled_at, updated_at, spec, team_revision, routine_revision,
               event, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (occurrence_id, row["id"], *scope, key, kind, created_at, scheduled_at, created_at,
             row["spec"], row["team_revision"], row["revision"], _json(event) if event else None,
             status, skip_reason),
        )
        return self._occurrence(self._find_occurrence(database, row["id"], key))

    def manual_run(self, project_id, room_id, routine_id, request_id, *, now=None, allow_native=True):
        scope = _scope(project_id, room_id)
        routine_id = _uuid(routine_id)
        key = f"manual:{_uuid(request_id)}"
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
            return self._record_occurrence(database, row, key, "manual", timestamp, timestamp)

    def claim_scheduled(self, *, now=None):
        instant = self._now(now)
        timestamp = _iso(instant)
        with self._connect() as database:
            if database.execute(_DUE_QUERY + " LIMIT 1", (timestamp,)).fetchone() is None:
                return []
        pending = []
        with self._connect(write=True) as database:
            for row in database.execute(_DUE_QUERY, (timestamp,)).fetchall():
                spec = json.loads(row["spec"])
                schedule = _WallSchedule(spec["trigger"])
                due = schedule.latest_due(instant, _utc(row["next_run_at"]))
                if due is not None:
                    day, scheduled_at = due
                    key = f"schedule:{day}"
                    if self._find_occurrence(database, row["id"], key) is None:
                        claim = self._record_occurrence(
                            database, row, key, spec["trigger"]["kind"], _iso(scheduled_at), timestamp
                        )
                        if claim["status"] == "pending":
                            pending.append(claim)
                database.execute(
                    "UPDATE routines SET next_run_at = ? WHERE id = ?",
                    (self._next_run(database, row["id"], spec, instant), row["id"]),
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
                    skip_reason = None
                    if json.loads(row["spec"])["allow_native"] and not event["trusted_local"]:
                        skip_reason = "手机创建的卡片不能触发需要本机执行权限的例行任务"
                    claim = self._record_occurrence(
                        database, row, key, "board_card", event["created_at"], timestamp, event,
                        skip_reason=skip_reason,
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
            database.execute(
                """UPDATE routine_occurrences SET status = ?, updated_at = ?,
                   run_id = COALESCE(run_id, ?), detail = ? WHERE id = ?""",
                (status, timestamp, run_id, detail, occurrence_id),
            )
            return self._occurrence(database.execute(
                "SELECT * FROM routine_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone())
