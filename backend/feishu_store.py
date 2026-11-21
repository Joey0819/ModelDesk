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
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

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
    natural_language_tasks: bool = False
    allow_all_groups: bool = False
    push_group_results: bool = False

    @field_validator("allowed_sender_ids")
    @classmethod
    def senders(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,180}", item) for item in values):
            raise ValueError("成员 Open ID 无效或重复")
        return values


OWNER_PATTERN = re.compile(r"^ou_[A-Za-z0-9_-]{1,180}$")
SETUP_COMMAND = re.compile(r"^[/／]modeldesk\s+(setup|confirm)\b", re.IGNORECASE)


def binding_is_current(snapshot, bindings):
    # The local allow-all grant can widen the live binding without invalidating
    # a pending event that retains the original, narrower authorization.
    return snapshot in bindings or (
        isinstance(snapshot, dict) and not snapshot.get("allow_all_groups")
        and {**snapshot, "allow_all_groups": True} in bindings
    )


def parse_setup_command(command):
    """Parse a gated bootstrap command body into (action, argument, models)."""
    match = re.match(r"^(setup|confirm)\s+(.+)$", command, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    action = match[1].lower()
    argument = match[2].strip()
    if action == "confirm":
        code = re.match(r"^(\d{4})$", argument)
        return ("confirm", code[1], []) if code else ("confirm", "", [])
    name, models = argument, []
    separated = re.split(r"\s+with\s+", argument, maxsplit=1, flags=re.IGNORECASE)
    if len(separated) == 2:
        name, model_text = separated[0].strip(), separated[1]
        models = [item.strip() for item in re.split(r"[,，;；]", model_text) if item.strip()]
        if not models or len(models) > 4 or any(not 1 <= len(item) <= 200 or any(ord(char) < 32 for char in item) for item in models):
            return ("setup", "", [])
    name = name.strip()
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        return ("setup", "", [])
    return ("setup", name, models)


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    app_id: str = Field(default="", max_length=180, pattern=r"^(cli_[A-Za-z0-9_-]+)?$")
    app_secret: SecretStr | None = None
    clear_secret: bool = False
    owner_sender_ids: list[str] = Field(default_factory=list, max_length=10)
    bindings: list[Binding] = Field(default_factory=list, max_length=30)

    @field_validator("owner_sender_ids")
    @classmethod
    def clean_owners(cls, values):
        if len(set(values)) != len(values) or any(not OWNER_PATTERN.fullmatch(item) for item in values):
            raise ValueError("管理员 Open ID 无效或重复")
        return values

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

    def config(self, *, readonly=False):
        with self.lock:
            if readonly:
                if self.directory.is_symlink():
                    raise HTTPException(409, "飞书配置路径无效")
            else:
                self.prepare()
            path = self.directory / "config.json"
            if path.is_symlink():
                raise HTTPException(409, "飞书配置路径无效")
            if not path.exists():
                return {"enabled": False, "app_id": "", "app_secret": "", "bindings": [], "revision": "", "updated_at": None}
            try:
                if not readonly:
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
        return {key: copy.deepcopy(config.get(key)) for key in ["enabled", "app_id", "owner_sender_ids", "bindings", "updated_at"]} | {"has_app_secret": bool(config.get("app_secret"))}

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
            self._write_config(data)
            return self.public_config()

    def _write_config(self, data):
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

    def save_binding(self, config, binding):
        """Append/update one binding without touching credentials or owners.

        Rebuilds ConfigInput from the stored private config so the write path
        keeps every existing validation (chat uniqueness, room references).
        """
        with self.lock:
            current = self.config()
            if current["revision"] != config["revision"] or current["app_id"] != config["app_id"]:
                raise HTTPException(409, "飞书配置在开通期间已变更，请重新执行")
            bindings = [item for item in current["bindings"] if item["chat_id"] != binding["chat_id"]] + [binding]
            request = ConfigInput(enabled=current["enabled"], app_id=current["app_id"],
                                   owner_sender_ids=current.get("owner_sender_ids") or [], bindings=bindings)
            return self.save_config(request)

    def remove_binding(self, config, chat_id):
        """Drop one chat binding; projects, teams and rooms are untouched."""
        with self.lock:
            current = self.config()
            if current["revision"] != config["revision"] or current["app_id"] != config["app_id"]:
                raise HTTPException(409, "飞书配置在解绑期间已变更，请重试")
            bindings = [item for item in current["bindings"] if item["chat_id"] != chat_id]
            if len(bindings) == len(current["bindings"]):
                return False
            request = ConfigInput(enabled=current["enabled"], app_id=current["app_id"],
                                   owner_sender_ids=current.get("owner_sender_ids") or [], bindings=bindings)
            self.save_config(request)
            self.clear_chat_route(current["app_id"], chat_id)
            return True

    def upsert_room_binding(
        self,
        room_id,
        *,
        chat_id,
        name="",
        project_id=None,
        allowed_sender_ids=None,
        allow_native=False,
        natural_language_tasks=False,
        allow_all_groups=False,
        push_group_results=False,
        replace_existing=False,
        enable_connection=False,
    ):
        """Atomically bind or update one Feishu chat for a room without touching credentials or owners."""
        with self.lock:
            current = self.config()
            if not current.get("app_id") or not current.get("app_secret"):
                raise HTTPException(422, "请先在「Agent 与连接 → 飞书远程」填写并保存 App ID 和 App Secret")
            room_id_str = str(room_id)
            project_id_str = str(project_id) if project_id else None
            from .groups import store as rooms
            rooms.get(room_id_str, project_id_str)
            existing = next((item for item in current["bindings"] if item["chat_id"] == chat_id), None)
            if (existing and (existing.get("room_id") != room_id_str or existing.get("project_id") != project_id_str)
                    and not replace_existing):
                raise HTTPException(
                    409,
                    "此飞书会话已绑定到其他项目群。请确认改绑到当前群，或为该会话开启「允许访问与切换全部项目群」。",
                )
            new_binding = {
                "chat_id": chat_id,
                "name": name or "",
                "project_id": project_id_str,
                "room_id": room_id_str,
                "allowed_sender_ids": list(allowed_sender_ids or []),
                "allow_native": bool(allow_native),
                "natural_language_tasks": bool(natural_language_tasks),
                "allow_all_groups": bool(allow_all_groups),
                "push_group_results": bool(push_group_results),
            }
            bindings = [item for item in current["bindings"] if item["chat_id"] != chat_id] + [new_binding]
            enabled = True if enable_connection else current["enabled"]
            try:
                request = ConfigInput(
                    enabled=enabled,
                    app_id=current["app_id"],
                    owner_sender_ids=current.get("owner_sender_ids") or [],
                    bindings=bindings,
                )
            except ValidationError as error:
                detail = error.errors()[0].get("msg", "飞书绑定参数无效") if error.errors() else "飞书绑定参数无效"
                raise HTTPException(422, detail) from None
            result = self.save_config(request)
            self.clear_chat_route(current["app_id"], chat_id)
            return result

    def allow_all_room_groups(self, room_id, chat_id, project_id):
        with self.lock:
            config = self.config()
            binding = next((item for item in config["bindings"] if item["chat_id"] == chat_id), None)
            if not binding or (binding["room_id"], binding.get("project_id")) != (room_id, project_id):
                raise HTTPException(409, "该飞书会话的绑定已变更，请刷新后重试")
            from .groups import store as rooms
            from .projects import store as projects
            rooms.get(room_id, project_id)
            if project_id:
                projects.get(project_id)
            if not binding.get("allow_all_groups"):
                binding["allow_all_groups"] = True
                # Preserve the existing authorization epoch and outbound cutoff.
                # This edit grants new events access without revoking prior events.
                self._write_config(config)

    def remove_room_binding(self, room_id, chat_id, project_id=None):
        """Atomically remove one room's Feishu chat binding without touching credentials or owners."""
        with self.lock:
            current = self.config()
            room_id_str = str(room_id)
            project_id_str = str(project_id) if project_id is not None else None
            target = next(
                (item for item in current["bindings"]
                 if item["chat_id"] == chat_id and item["room_id"] == room_id_str
                 and (project_id_str is None or item.get("project_id") == project_id_str)),
                None,
            )
            if not target:
                raise HTTPException(404, "未找到此项目群对应的飞书会话绑定")
            bindings = [item for item in current["bindings"] if item is not target]
            request = ConfigInput(
                enabled=current["enabled"],
                app_id=current["app_id"],
                owner_sender_ids=current.get("owner_sender_ids") or [],
                bindings=bindings,
            )
            result = self.save_config(request)
            self.clear_chat_route(current["app_id"], chat_id)
            return result

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
            CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, app_id TEXT NOT NULL, chat_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                code TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT);
            CREATE INDEX IF NOT EXISTS plans_chat ON plans(app_id, chat_id);
            CREATE INDEX IF NOT EXISTS plans_rate ON plans(app_id, chat_id, created_at);
            CREATE TABLE IF NOT EXISTS sender_routes (
                app_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                config_revision TEXT NOT NULL,
                project_id TEXT,
                room_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (app_id, chat_id, sender_id)
            );
            CREATE TABLE IF NOT EXISTS sender_room_numbers (
                app_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                number INTEGER NOT NULL,
                shown_revision TEXT,
                PRIMARY KEY (app_id, chat_id, sender_id, room_id),
                UNIQUE (app_id, chat_id, sender_id, number)
            );
        """)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_chat_route(self, app_id, chat_id, config_revision, sender_id):
        with self.db() as db:
            row = db.execute(
                "SELECT project_id, room_id, updated_at FROM sender_routes WHERE app_id=? AND chat_id=? AND config_revision=? AND sender_id=?",
                (app_id, chat_id, config_revision, sender_id),
            ).fetchone()
            if not row:
                return None
            return {"project_id": row["project_id"], "room_id": row["room_id"], "updated_at": row["updated_at"]}

    def set_chat_route(self, app_id, chat_id, config_revision, sender_id, project_id, room_id):
        stamp = now()
        project_str = str(project_id) if project_id else None
        room_str = str(room_id)
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO sender_routes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app_id, chat_id, sender_id, config_revision, project_str, room_str, stamp),
            )
        return {"project_id": project_str, "room_id": room_str, "updated_at": stamp}

    def active_routes(self, app_id, config_revision):
        if not app_id or not config_revision:
            return {}
        with self.db() as db:
            rows = db.execute(
                """SELECT DISTINCT chat_id, project_id, room_id FROM sender_routes
                   WHERE app_id=? AND config_revision=?""",
                (app_id, config_revision),
            ).fetchall()
            routes = {}
            ambiguous = set()
            for row in rows:
                chat_id = row["chat_id"]
                if chat_id in routes:
                    ambiguous.add(chat_id)
                routes[chat_id] = {"project_id": row["project_id"], "room_id": row["room_id"]}
            return {chat_id: route for chat_id, route in routes.items() if chat_id not in ambiguous}

    def clear_chat_route(self, app_id, chat_id):
        if not app_id or not chat_id:
            return
        with self.db() as db:
            db.execute("DELETE FROM sender_routes WHERE app_id=? AND chat_id=?", (app_id, chat_id))

    def reserve_room_numbers(self, record, room_ids):
        scope = (record["app_id"], record["chat_id"], record["sender_id"])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT room_id, number FROM sender_room_numbers WHERE app_id=? AND chat_id=? AND sender_id=?", scope
            ).fetchall()
            numbers = {row["room_id"]: row["number"] for row in existing}
            next_number = max(numbers.values(), default=0) + 1
            for room_id in room_ids:
                if room_id not in numbers:
                    db.execute(
                        "INSERT INTO sender_room_numbers VALUES (?, ?, ?, ?, ?, NULL)", (*scope, room_id, next_number)
                    )
                    numbers[room_id] = next_number
                    next_number += 1
            return {room_id: numbers[room_id] for room_id in room_ids}

    def mark_room_numbers_shown(self, record, room_ids):
        scope = (record["app_id"], record["chat_id"], record["sender_id"])
        with self.db() as db:
            db.executemany(
                """UPDATE sender_room_numbers SET shown_revision=?
                   WHERE app_id=? AND chat_id=? AND sender_id=? AND room_id=?""",
                [(record["config_revision"], *scope, room_id) for room_id in room_ids],
            )

    def shown_room_numbers(self, record):
        with self.db() as db:
            rows = db.execute(
                """SELECT number, room_id FROM sender_room_numbers
                   WHERE app_id=? AND chat_id=? AND sender_id=? AND shown_revision=?""",
                (record["app_id"], record["chat_id"], record["sender_id"], record["config_revision"]),
            ).fetchall()
            return {str(row["number"]): row["room_id"] for row in rows}

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
        observed = {"chat_id": chat_id, "chat_type": event.get("chat_type", ""), "last_sender_id": sender, "last_seen_at": stamp,
                    "last_message_type": str(event.get('message_type', ''))[:30]}
        def observe(outcome, reason):
            # Only routing diagnostics, never the content of ignored or unbound messages.
            observed.update(last_outcome=outcome, last_reason=reason)
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO observed VALUES (?, ?, ?)", (config["app_id"] + ":" + chat_id, config["app_id"], json.dumps(observed)))
        binding = next((item for item in config["bindings"] if item["chat_id"] == chat_id), None)
        # Self-service bootstrap gate: an unbound chat is only admitted as a
        # command when it is a private chat, the sender is a configured owner,
        # and the text is exactly a setup/confirm directive. Everything else
        # from an unbound chat keeps today's behaviour: diagnostics only, the
        # body is never persisted. This is the sole authorization source, so
        # someone who adds the bot to a group cannot self-authorize.
        owners = config.get("owner_sender_ids") or []
        gated = bool(owners) and sender in owners and event.get("chat_type") == "p2p"
        text = event.get("text") if not binding else None
        if not binding:
            if not gated:
                observe('unbound', '消息已收到；此会话尚未绑定项目群，没有派发任务。')
                return None
            if event.get("message_type") not in {"text", "post"} or not isinstance(text, str):
                observe('unsupported_content', '消息已收到；请使用文字或文字富文本发送 /modeldesk 指令。')
                return None
            text = re.sub(r"<at\b[^>]*>.*?</at>", "", text).strip()
            if not SETUP_COMMAND.match(text):
                observe('unbound', '消息已收到；此会话尚未绑定项目群，没有派发任务。管理员可在私聊中使用 /modeldesk setup 自助开通。')
                return None
            observe('bootstrap_received', '已收到管理员的开通或确认指令；处理结果见最近收发记录。')
            return self._commit_event(config, event, chat_id, sender, message_id, text, None, stamp, "bootstrap")
        if binding["allowed_sender_ids"] and sender not in binding["allowed_sender_ids"]:
            observe('sender_not_allowed', '消息已收到；发起人不在此会话的允许名单中，没有派发任务。')
            return None
        text = event.get("text")
        if event.get("message_type") not in {"text", "post"} or not isinstance(text, str):
            observe('unsupported_content', '消息已收到；请使用文字或文字富文本发送 /modeldesk 指令。')
            return None
        text = re.sub(r"<at\b[^>]*>.*?</at>", "", text).strip()
        prefix = re.match(r'^[/／]modeldesk(?:\s+|$)', text, flags=re.IGNORECASE)
        direct = bool(binding.get('natural_language_tasks'))
        if not prefix and not direct:
            observe('not_command', '消息已收到；未识别到 /modeldesk 指令，没有派发任务。请使用 /modeldesk task 任务内容。')
            return None
        observe('command_received', '已识别任务或控制消息；执行与回传情况见最近收发记录。')
        return self._commit_event(config, event, chat_id, sender, message_id, text, binding, stamp, "command" if prefix else "natural_language")

    def _commit_event(self, config, event, chat_id, sender, message_id, text, binding, stamp, input_mode):
        # A deterministic message key makes Feishu redelivery idempotent: the
        # same message_id can only ever create one durable event.
        command = re.sub(r"^[/／]modeldesk\s+", "", text, count=1, flags=re.IGNORECASE).strip() if input_mode == "bootstrap" else None
        prefix = re.match(r'^[/／]modeldesk(?:\s+|$)', text, flags=re.IGNORECASE) if input_mode != "bootstrap" else None
        parent_id = event.get("parent_id")
        is_reply = bool(parent_id or event.get("root_id"))
        if command is None:
            if prefix:
                command = text[prefix.end():].strip()
            elif not text or text.startswith(('/', '／')) or text.casefold() in {'你好', '您好', 'hi', 'hello', '帮助', 'help'}:
                command = ''  # A greeting or unknown slash command shows help, never runs a task.
            elif text.casefold() in {'status', '状态'}:
                command = 'status'
            elif groups := re.fullmatch(r"(?:groups|群列表|所有群|全部群|项目群列表)(?:\s+([0-9]+))?", text, re.IGNORECASE):
                command = "groups " + (groups[1] or "")
            else:
                switch_match = re.fullmatch(r"(?:切换群|切换项目群|use)(?:[：:\s]+(.*))?", text, re.IGNORECASE | re.DOTALL)
                if switch_match:
                    command = "use " + (switch_match[1] or "").strip()
                else:
                    continuation = re.match(r'^(?:继续|continue)(?:[：:\s]+(.*)|$)', text, flags=re.IGNORECASE | re.DOTALL)
                    if continuation:
                        command = 'continue ' + (continuation[1] or '')
                    else:
                        room_request = text.startswith("@") and event.get("leading_user_mention") is not True
                        command = ('continue ' if is_reply and not room_request else 'task ') + text
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu:" + config["app_id"] + ":" + message_id))
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM events WHERE id=?", (identifier,)).fetchone():
                return identifier
            pending = db.execute("SELECT count(*) FROM events WHERE state IN ('received','dispatching','monitoring')").fetchone()[0]
            since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            rate = db.execute("SELECT count(*) FROM events WHERE app_id=? AND chat_id=? AND created_at>?", (config["app_id"], chat_id, since)).fetchone()[0]
            reason = "" if len(command) <= 12000 and pending < 32 and rate < 10 else "消息过长、请求过快或后台队列已满，请稍后在本机检查"
            if event.get('input_error'):
                reason = '此指令含暂不支持的消息内容，未派发任务。请发送纯文字或文字富文本；图片和附件请先添加到 ModelDesk 项目。'
            record = {"id": identifier, "app_id": config["app_id"], "message_id": message_id, "chat_id": chat_id,
                      "sender_id": sender, "command": command[:12000], "binding": binding, "config_revision": config["revision"],
                      "input_mode": input_mode,
                      "job_id": None if input_mode == "bootstrap" else str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu-job:" + identifier)),
                      "state": "rejected" if reason else "received", "reason": reason, "created_at": stamp, "updated_at": stamp,
                      "notifications": []}
            if event.get("leading_user_mention") is True:
                record["leading_user_mention"] = True
            if is_reply:
                # A reply's root can point at a different revision from its parent.
                record["reply_message_id"] = parent_id if isinstance(parent_id, str) and re.fullmatch(r"om_[A-Za-z0-9_-]{1,197}", parent_id) else None
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (identifier, config["app_id"], message_id, chat_id,
                       record["state"], stamp, stamp, json.dumps(record, ensure_ascii=False)))
        return identifier

    def task_for_reply(self, record):
        message_id = record.get("reply_message_id")
        if not message_id:
            return None
        with self.db() as db:
            rows = db.execute("""
                SELECT events.data FROM events WHERE app_id=? AND chat_id=? AND (
                    message_id=? OR id IN (
                        SELECT event_id FROM outgoing WHERE state='sent'
                        AND json_extract(outgoing.data, '$.response_id')=?
                        AND (json_extract(outgoing.data, '$.kind')='accepted' OR json_extract(outgoing.data, '$.kind') LIKE '%:summary'
                             OR json_extract(outgoing.data, '$.kind') LIKE '%:body:%' OR json_extract(outgoing.data, '$.kind') LIKE '%:file:%')
                    )
                ) LIMIT 2
                """, (record["app_id"], record["chat_id"], message_id, message_id)).fetchall()
        if len(rows) == 1:
            original = json.loads(rows[0]["data"])
            if original["sender_id"] == record["sender_id"] and original["config_revision"] == record["config_revision"]:
                return original.get("job_id")

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

    def observed_chat_types(self, app_id, chat_ids):
        path = self.directory / "receipts.sqlite3"
        if path.is_symlink() or not path.is_file():
            return {}
        result = {}
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            for chat_id in chat_ids:
                row = db.execute("SELECT data FROM observed WHERE id=? AND app_id=?", (f"{app_id}:{chat_id}", app_id)).fetchone()
                if row:
                    chat_type = json.loads(row[0]).get("chat_type")
                    if chat_type in ("p2p", "group"):
                        result[chat_id] = chat_type
        return result

    def current(self, record):
        config = self.config()
        if not config["enabled"] or config["app_id"] != record["app_id"] or config["revision"] != record["config_revision"]:
            return False
        if record.get("input_mode") == "bootstrap":
            # Bootstrap events carry no binding; their authority is the owner
            # allowlist at ingest time plus this revision check.
            return record["sender_id"] in (config.get("owner_sender_ids") or [])
        return binding_is_current(record["binding"], config["bindings"])
