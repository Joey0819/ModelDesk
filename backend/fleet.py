"""Fleet node registry: enrollment, heartbeat, telemetry, visualization.

Phase 1 scope is strictly registration + heartbeat + telemetry + display.
No remote command execution, remote shell, file delivery, or SSH orchestration
is implemented or allowed here. Admin endpoints follow the local-admin
boundary used by model connections; the remote-access gateway blocks /api/fleet.
"""
import copy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .model_connections import require_local_admin

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data/fleet"
NODES_FILE = STATE_DIR / "nodes.json"

NODE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,39}$"
OFFLINE_AFTER_SECONDS = 90.0
HEARTBEAT_INTERVAL_SECONDS = 15
ENROLL_TOKEN_TTL_SECONDS = 600
MAX_ENROLL_FAILURES = 10
ENROLL_FAILURE_WINDOW_SECONDS = 300
MAX_NODES = 200
PLATFORMS = {"linux", "darwin", "windows"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    mem_percent: float | None = Field(default=None, ge=0, le=100)
    load1: float | None = Field(default=None, ge=0)
    disk_percent: float | None = Field(default=None, ge=0, le=100)
    uptime_seconds: float | None = Field(default=None, ge=0)
    agent_version: str = Field(default="", max_length=40)
    running_jobs: int = Field(default=0, ge=0)
    queued_jobs: int = Field(default=0, ge=0)


class NodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(pattern=NODE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    platform: str
    hostname: str = Field(min_length=1, max_length=120)
    labels: list[str] = Field(default_factory=list, max_length=8)
    enroll_token_hash: str = ""
    node_token_hash: str = ""
    created_at: str = ""
    last_seen_at: str | None = None
    telemetry: Telemetry = Field(default_factory=Telemetry)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("节点名称不能为空")
        return value.strip()

    @field_validator("platform")
    @classmethod
    def known_platform(cls, value):
        if value not in PLATFORMS:
            raise ValueError("platform 必须是 linux、darwin 或 windows")
        return value

    @field_validator("labels")
    @classmethod
    def label_shape(cls, value):
        for label in value:
            if not label.strip() or len(label) > 40:
                raise ValueError("标签不能为空，且最长 40 个字符")
        return [label.strip() for label in value]


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=10, max_length=200)
    node_id: str = Field(pattern=NODE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    platform: str
    hostname: str = Field(min_length=1, max_length=120)
    labels: list[str] = Field(default_factory=list, max_length=8)
    agent_version: str = Field(default="", max_length=40)

    @field_validator("platform")
    @classmethod
    def known_platform(cls, value):
        if value not in PLATFORMS:
            raise ValueError("platform 必须是 linux、darwin 或 windows")
        return value


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telemetry: Telemetry = Field(default_factory=Telemetry)


def _read_timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return None
        return stamp.timestamp()
    except (ValueError, TypeError):
        return None


class FleetStore:
    """Registry persisted atomically; tokens stored only as sha256 hashes."""

    def __init__(self, directory=None, clock=time.time):
        self.directory = Path(directory or STATE_DIR)
        self.nodes_file = self.directory / "nodes.json"
        self.clock = clock
        self.lock = threading.RLock()
        self._enroll_tokens = {}  # token_hash -> {"expires": epoch, "label": str}
        self._enroll_failures = []  # list of epoch seconds
        self._nodes_cache = None

    def _stamp(self):
        """Timestamps come from the injectable clock so tests control time."""
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()

    def _load(self):
        # Callers mutate the returned mapping and then persist it. Always hand
        # back a copy: if the write fails, the in-memory cache must not keep the
        # mutation, otherwise memory and disk disagree until restart.
        with self.lock:
            if self._nodes_cache is not None:
                return copy.deepcopy(self._nodes_cache)
            if self.directory.is_symlink() or self.nodes_file.is_symlink():
                raise RuntimeError("节点注册存储路径不安全")
            if not self.nodes_file.exists():
                self._nodes_cache = {}
                return {}
            try:
                raw = self.nodes_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict) or "nodes" not in data or not isinstance(data["nodes"], dict):
                    raise ValueError("missing nodes")
                nodes = {}
                for node_id, record in data["nodes"].items():
                    if not re.fullmatch(NODE_ID_PATTERN, node_id) or record.get("node_id") != node_id:
                        raise ValueError("node id mismatch")
                    nodes[node_id] = NodeRecord.model_validate(record).model_dump()
            except (OSError, ValueError, TypeError) as error:
                # Never silently wipe a damaged registry; keep the original file.
                raise RuntimeError(f"节点注册数据无法读取（{self.nodes_file}）：{error}") from None
            self._nodes_cache = nodes
            return copy.deepcopy(nodes)

    def _write(self, nodes):
        if self.directory.is_symlink():
            raise RuntimeError("节点注册存储路径不安全")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        payload = {"nodes": nodes}
        descriptor, temporary = tempfile.mkstemp(prefix=".nodes-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.nodes_file)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._nodes_cache = nodes

    def create_enroll_token(self, label=""):
        """One-time server-generated enrollment token; only its hash is kept."""
        token = secrets.token_urlsafe(32)
        with self.lock:
            current = self.clock()
            self._enroll_tokens = {hashed: value for hashed, value in self._enroll_tokens.items()
                                   if value["expires"] > current}
            self._enroll_tokens[digest(token)] = {"expires": current + ENROLL_TOKEN_TTL_SECONDS, "label": label or ""}
        return {"token": token, "expires_in": ENROLL_TOKEN_TTL_SECONDS}

    def _check_enroll_rate(self):
        current = self.clock()
        self._enroll_failures = [stamp for stamp in self._enroll_failures
                                 if current - stamp < ENROLL_FAILURE_WINDOW_SECONDS]
        if len(self._enroll_failures) >= MAX_ENROLL_FAILURES:
            raise HTTPException(429, "注册尝试过多，请五分钟后重试")

    def enroll(self, body):
        with self.lock:
            self._check_enroll_rate()
            nodes = self._load()
            token_hash = digest(body.token)
            pending = self._enroll_tokens.get(token_hash)
            if pending is None or pending["expires"] <= self.clock():
                self._enroll_tokens.pop(token_hash, None)
                self._enroll_failures.append(self.clock())
                raise HTTPException(401, "注册令牌无效或已使用，请重新生成")
            if body.node_id in nodes:
                raise HTTPException(409, "该节点 ID 已注册；请先移除旧节点或更换 ID")
            if len(nodes) >= MAX_NODES:
                raise HTTPException(422, "注册节点数量已达上限")
            # One-time: consume immediately, even if the write later fails.
            self._enroll_tokens.pop(token_hash, None)
            node_token = secrets.token_urlsafe(32)
            record = NodeRecord(node_id=body.node_id, name=body.name, platform=body.platform,
                                hostname=body.hostname, labels=body.labels,
                                enroll_token_hash=token_hash, node_token_hash=digest(node_token),
                                created_at=self._stamp(), last_seen_at=None,
                                telemetry=Telemetry(agent_version=body.agent_version))
            nodes[body.node_id] = record.model_dump()
            self._write(nodes)
            return {"node_id": body.node_id, "node_token": node_token,
                    "heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS}

    def _bearer_token(self, request):
        header = request.headers.get("authorization") or ""
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise HTTPException(401, "缺少节点 Bearer 令牌")
        return parts[1].strip()

    def heartbeat(self, request, body):
        token = self._bearer_token(request)
        token_hash = digest(token)
        with self.lock:
            nodes = self._load()
            match = None
            for node in nodes.values():
                if node.get("node_token_hash") == token_hash:
                    match = node
                    break
            if match is None:
                raise HTTPException(401, "节点令牌无效")
            match["last_seen_at"] = self._stamp()
            match["telemetry"] = body.telemetry.model_dump()
            self._write(nodes)
            return {"node_id": match["node_id"], "heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS}

    def node_status(self, record, at_epoch=None):
        """Derive status at read time; no background thread mutates state."""
        if not record.get("last_seen_at"):
            return "never"
        seen = _read_timestamp(record["last_seen_at"])
        if seen is None:
            return "offline"
        current = self.clock() if at_epoch is None else at_epoch
        return "online" if current - seen <= OFFLINE_AFTER_SECONDS else "offline"

    def public(self, record, at_epoch=None):
        result = {key: copy.deepcopy(value) for key, value in record.items()
                  if key not in {"enroll_token_hash", "node_token_hash"}}
        result["status"] = self.node_status(record, at_epoch)
        return result

    def listing(self, at_epoch=None):
        with self.lock:
            nodes = copy.deepcopy(self._load())
        rows = [self.public(record, at_epoch) for record in
                sorted(nodes.values(), key=lambda item: item.get("created_at") or "")]
        current = self.clock() if at_epoch is None else at_epoch
        summary = self.summarize(rows, current)
        return {"nodes": rows, "summary": summary}

    @staticmethod
    def summarize(rows, current=None):
        total = len(rows)
        online = sum(1 for row in rows if row.get("status") == "online")
        offline = sum(1 for row in rows if row.get("status") == "offline")
        never = sum(1 for row in rows if row.get("status") == "never")
        cpu_values = [row.get("telemetry", {}).get("cpu_percent") for row in rows
                      if row.get("status") == "online" and row.get("telemetry", {}).get("cpu_percent") is not None]
        mem_values = [row.get("telemetry", {}).get("mem_percent") for row in rows
                      if row.get("status") == "online" and row.get("telemetry", {}).get("mem_percent") is not None]
        return {"total": total, "online": online, "offline": offline, "never": never,
                "cpu_percent_avg": round(sum(cpu_values) / len(cpu_values), 1) if cpu_values else None,
                "mem_percent_avg": round(sum(mem_values) / len(mem_values), 1) if mem_values else None,
                "offline_after_seconds": OFFLINE_AFTER_SECONDS}

    def remove(self, node_id):
        with self.lock:
            nodes = self._load()
            if nodes.pop(node_id, None) is None:
                raise HTTPException(404, "节点不存在或已移除")
            self._write(nodes)
            return {"removed": node_id}


store = FleetStore()


def fleet_admin_guard(request: Request):
    """Same loopback + origin allowlist as require_local_admin, named for fleet."""
    return require_local_admin(request)


router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("", dependencies=[Depends(require_local_admin)])
async def list_nodes():
    try:
        return store.listing()
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/enroll-tokens", dependencies=[Depends(require_local_admin)])
async def generate_enroll_token():
    return store.create_enroll_token()


@router.post("/enroll")
async def enroll_node(body: EnrollRequest):
    # No loopback restriction: remote machines enroll with a one-time token.
    try:
        return store.enroll(body)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/heartbeat")
async def node_heartbeat(request: Request, body: HeartbeatRequest):
    try:
        return store.heartbeat(request, body)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.delete("/{node_id}", dependencies=[Depends(require_local_admin)])
async def remove_node(node_id: str):
    if not re.fullmatch(NODE_ID_PATTERN, node_id):
        raise HTTPException(404, "节点不存在或已移除")
    try:
        return store.remove(node_id)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None
