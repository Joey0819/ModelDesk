"""Fleet node registry & remote worker execution: enrollment, heartbeat, telemetry, job queue.

Phase 2 adds pull-based worker polling, structured job specifications, and status reporting.
Admin endpoints follow the local-admin boundary; the remote-access gateway blocks /api/fleet.
"""
import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .model_connections import require_local_admin

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data/fleet"
NODES_FILE = STATE_DIR / "nodes.json"
JOBS_FILE = STATE_DIR / "jobs.json"

NODE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,39}$"
OFFLINE_AFTER_SECONDS = 90.0
HEARTBEAT_INTERVAL_SECONDS = 15
ENROLL_TOKEN_TTL_SECONDS = 600
MAX_ENROLL_FAILURES = 10
ENROLL_FAILURE_WINDOW_SECONDS = 300
MAX_NODES = 200
PLATFORMS = {"linux", "darwin", "windows"}
TERMINAL_JOB_STATUSES = {"complete", "error", "cancelled"}


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


class JobConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int = Field(default=300, ge=10, le=7200)
    memory_mb: int = Field(default=4096, ge=256, le=65536)
    require_labels: list[str] = Field(default_factory=list, max_length=8)
    target_node_id: str | None = None
    target_platform: str | None = None

    @field_validator("target_platform")
    @classmethod
    def valid_target_platform(cls, value):
        if value is not None and value not in PLATFORMS:
            raise ValueError("target_platform 必须是 linux、darwin 或 windows")
        return value


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    task_type: str = Field(default="generic", max_length=50)
    worker_script: str = Field(default="modeldesk_worker", max_length=80)
    project_id: str | None = None
    inputs: dict = Field(default_factory=dict)
    constraints: JobConstraints = Field(default_factory=JobConstraints)


class PollJobsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telemetry: Telemetry = Field(default_factory=Telemetry)


class JobReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    status: str = Field(pattern=r"^(running|complete|error|cancelled)$")
    progress: float | None = Field(default=None, ge=0.0, le=100.0)
    log_chunk: str = Field(default="", max_length=32768)
    result: dict | None = None
    error: str | None = None


class ReassignJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_node_id: str | None = None


def _sync_to_kanban(platform, task_id, title, status, detail="", source_label="", project_id=None):
    try:
        from .todo_sync import sync_task
        sync_task(
            platform=platform,
            task_id=task_id,
            title=title,
            status=status,
            detail=detail,
            source_label=source_label,
            project_id=project_id,
        )
    except Exception:
        pass


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
        self.jobs_file = self.directory / "jobs.json"
        self.clock = clock
        self.lock = threading.RLock()
        self._enroll_tokens = {}  # token_hash -> {"expires": epoch, "label": str}
        self._enroll_failures = []  # list of epoch seconds
        self._nodes_cache = None
        self._jobs_cache = None

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

    def _load_jobs(self):
        with self.lock:
            if self._jobs_cache is not None:
                return copy.deepcopy(self._jobs_cache)
            if self.directory.is_symlink() or self.jobs_file.is_symlink():
                raise RuntimeError("节点任务存储路径不安全")
            if not self.jobs_file.exists():
                self._jobs_cache = {}
                return {}
            try:
                raw = self.jobs_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], dict):
                    raise ValueError("missing jobs")
                for job in data["jobs"].values():
                    if isinstance(job, dict) and job.get("status") == "queued":
                        # Older releases stored a replacement target as if it already owned the job.
                        self._reset_job_for_queue(job, job.get("updated_at") or self._stamp(), job.get("assigned_node_id"))
                self._jobs_cache = data["jobs"]
                return copy.deepcopy(self._jobs_cache)
            except (OSError, ValueError, TypeError) as error:
                raise RuntimeError(f"节点任务数据无法读取（{self.jobs_file}）：{error}") from None

    def _write_jobs(self, jobs):
        if self.directory.is_symlink():
            raise RuntimeError("节点任务存储路径不安全")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        payload = {"jobs": jobs}
        descriptor, temporary = tempfile.mkstemp(prefix=".jobs-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.jobs_file)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._jobs_cache = jobs

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

    def _find_calling_node(self, request, nodes=None):
        token = self._bearer_token(request)
        token_hash = digest(token)
        if nodes is None:
            nodes = self._load()
        for node in nodes.values():
            if node.get("node_token_hash") == token_hash:
                return node
        raise HTTPException(401, "节点令牌无效")

    def enqueue_job(self, body: CreateJobRequest):
        with self.lock:
            jobs = self._load_jobs()
            job_id = f"flt_{uuid.uuid4().hex[:12]}"
            created_at = self._stamp()
            record = {
                "job_id": job_id,
                "title": body.title,
                "task_type": body.task_type,
                "worker_script": body.worker_script,
                "project_id": body.project_id,
                "inputs": copy.deepcopy(body.inputs),
                "constraints": body.constraints.model_dump(),
                "status": "queued",
                "assigned_node_id": None,
                "assigned_at": None,
                "created_at": created_at,
                "updated_at": created_at,
                "completed_at": None,
                "progress": 0.0,
                "logs": [f"[{created_at}] 任务已进入就绪队列"],
                "result": None,
                "error": None,
                "failure_reason": None,
            }
            jobs[job_id] = record
            self._write_jobs(jobs)
            _sync_to_kanban("fleet", job_id, body.title, "queued", detail=body.task_type, source_label="Fleet 队列", project_id=body.project_id)
            return copy.deepcopy(record)

    def reap_offline_jobs(self, nodes=None):
        with self.lock:
            if nodes is None:
                nodes = self._load()
            jobs = self._load_jobs()
            changed = False
            now_epoch = self.clock()
            now_iso = self._stamp()
            for job in jobs.values():
                if job.get("status") == "running" and job.get("assigned_node_id"):
                    node_id = job["assigned_node_id"]
                    node_record = nodes.get(node_id)
                    if node_record is None or self.node_status(node_record, now_epoch) != "online":
                        job["status"] = "blocked"
                        job["failure_reason"] = "node_offline"
                        job["updated_at"] = now_iso
                        job["logs"].append(f"[{now_iso}] 节点 {node_id} 离线，任务已自动置为阻塞")
                        changed = True
                        _sync_to_kanban("fleet", job["job_id"], job["title"], "error", detail=f"节点 {node_id} 离线", source_label=f"节点 {node_id}", project_id=job.get("project_id"))
            if changed:
                self._write_jobs(jobs)

    def poll_job(self, request, body: PollJobsRequest):
        with self.lock:
            nodes = self._load()
            node = self._find_calling_node(request, nodes)
            node["last_seen_at"] = self._stamp()
            node["telemetry"] = body.telemetry.model_dump()
            self._write(nodes)
            self.reap_offline_jobs(nodes)

            jobs = self._load_jobs()
            target = None
            node_labels = set(node.get("labels") or [])
            for job in sorted(jobs.values(), key=lambda j: j.get("created_at") or ""):
                if job.get("status") != "queued":
                    continue
                constraints = job.get("constraints") or {}
                req_node = constraints.get("target_node_id")
                if req_node and req_node != node["node_id"]:
                    continue
                req_plat = constraints.get("target_platform")
                if req_plat and req_plat != node["platform"]:
                    continue
                req_labels = set(constraints.get("require_labels") or [])
                if not req_labels.issubset(node_labels):
                    continue
                target = job
                break

            if target is None:
                return {"assigned_job": None, "poll_interval_seconds": 10}

            now_iso = self._stamp()
            target["status"] = "running"
            target["assigned_node_id"] = node["node_id"]
            target["attempt_id"] = uuid.uuid4().hex
            target["assigned_at"] = now_iso
            target["updated_at"] = now_iso
            target["logs"].append(f"[{now_iso}] 任务被节点 {node['name']} ({node['node_id']}) 认领")
            self._write_jobs(jobs)

            _sync_to_kanban("fleet", target["job_id"], target["title"], "running", detail=f"执行中 · {node['name']}", source_label=f"节点 {node['name']}", project_id=target.get("project_id"))

            return {
                "assigned_job": copy.deepcopy(target),
                "poll_interval_seconds": 5,
            }

    def report_job(self, request, job_id, body: JobReportRequest):
        with self.lock:
            nodes = self._load()
            node = self._find_calling_node(request, nodes)
            jobs = self._load_jobs()
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "任务不存在")
            if job.get("assigned_node_id") != node["node_id"]:
                raise HTTPException(403, "当前节点无权报告此任务")
            if body.attempt_id is not None:
                if body.attempt_id != job.get("attempt_id"):
                    raise HTTPException(409, "报告属于此任务的旧认领，请核对此次 poll 返回的 attempt_id")
            elif node["node_id"] in job.get("previous_node_ids", []):
                raise HTTPException(409, "此节点重新认领了同一任务，报告必须携带此次 poll 返回的 attempt_id")
            current_status = job["status"]
            if current_status in TERMINAL_JOB_STATUSES and body.status == current_status:
                return {"job_id": job_id, "status": current_status}
            if current_status not in {"running", "blocked"}:
                raise HTTPException(409, "任务已结束或尚未认领，无法接受本次报告")

            now_iso = self._stamp()
            job["updated_at"] = now_iso
            if body.progress is not None:
                job["progress"] = body.progress
            if body.log_chunk:
                job["logs"].append(body.log_chunk)
                if len(job["logs"]) > 500:
                    job["logs"] = job["logs"][-500:]

            if body.status in TERMINAL_JOB_STATUSES:
                job["status"] = body.status
                job["completed_at"] = now_iso
                job["failure_reason"] = None
                if body.result is not None:
                    job["result"] = copy.deepcopy(body.result)
                if body.error is not None:
                    job["error"] = body.error
                job["logs"].append(f"[{now_iso}] 任务结束，状态：{body.status}")

            self._write_jobs(jobs)
            if body.status in TERMINAL_JOB_STATUSES:
                _sync_to_kanban("fleet", job_id, job["title"], body.status, detail=body.error or "", source_label=f"节点 {node['name']}", project_id=job.get("project_id"))
            return {"job_id": job_id, "status": job["status"]}

    def list_jobs(self, status=None, project_id=None, node_id=None):
        with self.lock:
            self.reap_offline_jobs()
            jobs = self._load_jobs()
            rows = []
            for j in sorted(jobs.values(), key=lambda item: item.get("created_at") or "", reverse=True):
                if status and j.get("status") != status:
                    continue
                if project_id and j.get("project_id") != project_id:
                    continue
                if node_id and j.get("assigned_node_id") != node_id:
                    continue
                rows.append(copy.deepcopy(j))
            return {"jobs": rows, "total": len(rows)}

    def get_job(self, job_id):
        with self.lock:
            self.reap_offline_jobs()
            jobs = self._load_jobs()
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "任务不存在")
            return copy.deepcopy(job)

    def cancel_job(self, job_id):
        with self.lock:
            jobs = self._load_jobs()
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "任务不存在")
            if job.get("status") in {"complete", "cancelled"}:
                return copy.deepcopy(job)
            now_iso = self._stamp()
            job["status"] = "cancelled"
            job["completed_at"] = now_iso
            job["updated_at"] = now_iso
            job["logs"].append(f"[{now_iso}] 任务已被管理员/看板取消")
            self._write_jobs(jobs)
            _sync_to_kanban("fleet", job_id, job["title"], "cancelled", detail="用户取消", source_label="Fleet 队列", project_id=job.get("project_id"))
            return copy.deepcopy(job)

    @staticmethod
    def _reset_job_for_queue(job, stamp, target_node_id):
        if target_node_id:
            job["constraints"] = (job.get("constraints") or {}) | {"target_node_id": target_node_id}
        diagnostics = [value for value in (job.get("failure_reason"), job.get("error")) if value]
        if diagnostics:
            job["logs"].append(f"[{stamp}] 上次尝试诊断：{' · '.join(diagnostics)}")
        previous_node = job.get("assigned_node_id")
        if previous_node and previous_node not in job.get("previous_node_ids", []):
            job.setdefault("previous_node_ids", []).append(previous_node)
        job.update(status="queued", failure_reason=None, assigned_node_id=None, assigned_at=None, attempt_id=None,
                   completed_at=None, progress=0.0, result=None, error=None, updated_at=stamp)

    def reassign_job(self, job_id, target_node_id=None):
        with self.lock:
            jobs = self._load_jobs()
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "任务不存在")
            if job.get("status") not in {"blocked", "error"}:
                raise HTTPException(400, "只有阻塞或失败的任务可以重新调度")
            now_iso = self._stamp()
            self._reset_job_for_queue(job, now_iso, target_node_id)
            job["logs"].append(f"[{now_iso}] 重新排队待调度" + (f"（指定节点 {target_node_id}）" if target_node_id else ""))
            self._write_jobs(jobs)
            _sync_to_kanban("fleet", job_id, job["title"], "queued", detail="重新排队", source_label="Fleet 队列", project_id=job.get("project_id"))
            return copy.deepcopy(job)


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


@router.post("/jobs", dependencies=[Depends(require_local_admin)])
async def create_fleet_job(body: CreateJobRequest):
    try:
        return store.enqueue_job(body)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.get("/jobs", dependencies=[Depends(require_local_admin)])
async def list_fleet_jobs(status: str | None = None, project_id: str | None = None, node_id: str | None = None):
    try:
        return store.list_jobs(status=status, project_id=project_id, node_id=node_id)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.get("/jobs/{job_id}", dependencies=[Depends(require_local_admin)])
async def get_fleet_job(job_id: str):
    try:
        return store.get_job(job_id)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_local_admin)])
async def cancel_fleet_job(job_id: str):
    try:
        return store.cancel_job(job_id)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/jobs/{job_id}/reassign", dependencies=[Depends(require_local_admin)])
async def reassign_fleet_job(job_id: str, body: ReassignJobRequest | None = None):
    try:
        target_node_id = body.target_node_id if body else None
        return store.reassign_job(job_id, target_node_id=target_node_id)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/jobs/poll")
async def poll_fleet_job(request: Request, body: PollJobsRequest):
    try:
        return store.poll_job(request, body)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None


@router.post("/jobs/{job_id}/report")
async def report_fleet_job(request: Request, job_id: str, body: JobReportRequest):
    try:
        return store.report_job(request, job_id, body)
    except RuntimeError as error:
        raise HTTPException(500, str(error)) from None
