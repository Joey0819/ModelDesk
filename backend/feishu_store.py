"""Private configuration and cross-process durable Feishu ingress/outbound receipts."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(pattern=r"^oc_[A-Za-z0-9_-]{1,180}$")
    name: str = Field(default="", max_length=100)
    project_id: uuid.UUID | None = None
    room_id: uuid.UUID
    allowed_sender_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_native: bool = False

    @field_validator("allowed_sender_ids")
    @classmethod
    def senders(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,180}", item) for item in values):
            raise ValueError("成员 Open ID 无效或重复")
        return values


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    app_id: str = Field(default="", max_length=180, pattern=r"^(cli_[A-Za-z0-9_-]+)?$")
    app_secret: SecretStr | None = None
    clear_secret: bool = False
    bindings: list[Binding] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_config(self):
        if len({item.chat_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一个飞书聊天只能绑定一个项目群")
        if self.enabled and self.clear_secret:
            raise ValueError("请先停用连接，再清除凭据")
        return self


class FeishuStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / "data/feishu")
        self.lock = threading.RLock()

    def prepare(self):
        if self.directory.is_symlink():
            raise HTTPException(409, "飞书配置路径无效")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def config(self):
        with self.lock:
            self.prepare()
            path = self.directory / "config.json"
            if path.is_symlink():
                raise HTTPException(409, "飞书配置路径无效")
            if not path.exists():
                return {"enabled": False, "app_id": "", "app_secret": "", "bindings": [], "revision": "", "updated_at": None}
            try:
                path.chmod(0o600)
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or not {"enabled", "app_id", "app_secret", "bindings", "revision", "updated_at"} <= value.keys():
                    raise ValueError()
                ConfigInput.model_validate({key: value[key] for key in ConfigInput.model_fields if key in value})
                if not isinstance(value.get("revision"), str):
                    raise ValueError()
                return value
            except (ValueError, TypeError, OSError):
                raise HTTPException(409, "飞书配置无法读取，请检查本机设置") from None

    def public_config(self):
        config = self.config()
        return {key: copy.deepcopy(config.get(key)) for key in ["enabled", "app_id", "bindings", "updated_at"]} | {"has_app_secret": bool(config.get("app_secret"))}

    def save_config(self, request):
        with self.lock:
            previous = self.config()
            secret = request.app_secret.get_secret_value() if request.app_secret else ""
            if secret and (len(secret) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in secret)):
                raise HTTPException(422, "App Secret 格式无效")
            if previous["app_id"] and previous["app_id"] != request.app_id and not secret and not request.clear_secret:
                raise HTTPException(422, "更换 App ID 时请重新填写 Secret，或先清除旧凭据")
            secret = "" if request.clear_secret else secret or previous.get("app_secret", "")
            if request.enabled and (not request.app_id or not secret):
                raise HTTPException(422, "请先填写飞书企业自建应用的 App ID 和 App Secret")
            from .groups import store as rooms
            for binding in request.bindings:
                rooms.get(str(binding.room_id), str(binding.project_id) if binding.project_id else None)
            data = request.model_dump(mode="json", exclude={"app_secret", "clear_secret"})
            if all(previous.get(key) == value for key, value in {**data, "app_secret": secret}.items()):
                return self.public_config()
            data.update(app_secret=secret, revision=str(uuid.uuid4()), updated_at=now())
            descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=self.directory)
            try:
                with os.fdopen(descriptor, "w") as output:
                    json.dump(data, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.directory / "config.json")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.public_config()

    @contextmanager
    def db(self):
        self.prepare()
        path = self.directory / "receipts.sqlite3"
        if path.is_symlink():
            raise HTTPException(409, "飞书记录路径无效")
        db = sqlite3.connect(path, timeout=1)
        path.chmod(0o600)
        db.row_factory = sqlite3.Row
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, app_id TEXT NOT NULL, message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outgoing (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observed (id TEXT PRIMARY KEY, app_id TEXT NOT NULL, data TEXT NOT NULL);
        """)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, event):
        """Called by the authenticated SDK worker; commit before acknowledging it."""
        config = self.config()
        if not config["enabled"] or event.get("app_id") != config["app_id"] or event.get("sender_type") != "user":
            return None
        chat_id, sender, message_id = event.get("chat_id", ""), event.get("sender_id", ""), event.get("message_id", "")
        if (not re.fullmatch(r"oc_[A-Za-z0-9_-]{1,180}", chat_id) or not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,180}", sender)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id)):
            return None
        try:
            created = datetime.fromtimestamp(int(event["create_time"]) / 1000, timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age < -60 or age > 600:
                return None
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return None
        stamp = now()
        observed = {"chat_id": chat_id, "chat_type": event.get("chat_type", ""), "last_sender_id": sender, "last_seen_at": stamp}
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO observed VALUES (?, ?, ?)", (config["app_id"] + ":" + chat_id, config["app_id"], json.dumps(observed)))
        binding = next((item for item in config["bindings"] if item["chat_id"] == chat_id), None)
        if not binding or (binding["allowed_sender_ids"] and sender not in binding["allowed_sender_ids"]):
            return None
        text = event.get("text")
        if event.get("message_type") != "text" or not isinstance(text, str):
            return None
        text = re.sub(r"<at\b[^>]*>.*?</at>", "", text).strip()
        if not (text == "/modeldesk" or text.startswith("/modeldesk ")):
            return None
        command = text[len("/modeldesk"):].strip()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu:" + config["app_id"] + ":" + message_id))
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM events WHERE id=?", (identifier,)).fetchone():
                return identifier
            pending = db.execute("SELECT count(*) FROM events WHERE state IN ('received','dispatching','monitoring')").fetchone()[0]
            since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            rate = db.execute("SELECT count(*) FROM events WHERE app_id=? AND chat_id=? AND created_at>?", (config["app_id"], chat_id, since)).fetchone()[0]
            reason = "" if len(command) <= 12000 and pending < 32 and rate < 10 else "消息过长、请求过快或后台队列已满，请稍后在本机检查"
            record = {"id": identifier, "app_id": config["app_id"], "message_id": message_id, "chat_id": chat_id,
                      "sender_id": sender, "command": command[:12000], "binding": binding, "config_revision": config["revision"],
                      "job_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu-job:" + identifier)),
                      "state": "rejected" if reason else "received", "reason": reason, "created_at": stamp, "updated_at": stamp,
                      "notifications": []}
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (identifier, config["app_id"], message_id, chat_id,
                       record["state"], stamp, stamp, json.dumps(record, ensure_ascii=False)))
        return identifier

    def events(self, states=None):
        with self.db() as db:
            query = "SELECT data FROM events"
            args = ()
            if states:
                query += " WHERE state IN (" + ",".join("?" for _ in states) + ")"
                args = tuple(states)
            query += " ORDER BY created_at ASC"
            result = []
            for row in db.execute(query, args):
                try:
                    result.append(json.loads(row[0]))
                except (ValueError, TypeError):
                    continue
            return result

    def update(self, record, **changes):
        record.update(**changes, updated_at=now())
        with self.db() as db:
            db.execute("UPDATE events SET state=?, updated_at=?, data=? WHERE id=?", (record["state"], record["updated_at"], json.dumps(record, ensure_ascii=False), record["id"]))

    def reserve_outgoing(self, identifier, event_id, data):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT state,data FROM outgoing WHERE id=?", (identifier,)).fetchone()
            if existing:
                return False, {**json.loads(existing["data"]), "state": existing["state"]}
            value = {**data, "created_at": now(), "state": "dispatching"}
            db.execute("INSERT INTO outgoing VALUES (?, ?, ?, ?)", (identifier, event_id, "dispatching", json.dumps(value)))
            return True, value

    def finish_outgoing(self, identifier, *, state, **data):
        with self.db() as db:
            row = db.execute("SELECT data FROM outgoing WHERE id=?", (identifier,)).fetchone()
            value = {**json.loads(row[0]), **data, "state": state, "updated_at": now()}
            db.execute("UPDATE outgoing SET state=?,data=? WHERE id=?", (state, json.dumps(value), identifier))

    def observed(self, app_id):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM observed WHERE app_id=? ORDER BY rowid DESC LIMIT 50", (app_id,))]

    def current(self, record):
        config = self.config()
        return (config["enabled"] and config["app_id"] == record["app_id"] and config["revision"] == record["config_revision"]
                and record["binding"] in config["bindings"])
