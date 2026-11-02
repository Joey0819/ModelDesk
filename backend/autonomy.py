"""Bounded, durable supervision of an existing group delivery, not another executor.

The job record is the single source of truth. Continue pure model work, verified
result collection, and originally authorized dependent steps proven never sent.
Uncertain external dispatch is never replayed.
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from .workbench import TERMINAL, manager, now

MAX_ATTEMPTS = 2
STATES = ("running", "retry_wait", "needs_attention", "complete", "cancelled", "paused")
logger = logging.getLogger(__name__)


def initial_state():
    return {"enabled": True, "state": "running", "attempts": 0, "max_attempts": MAX_ATTEMPTS,
            "next_action_at": None, "reason": "后台托管已开启，完成或需要处理时会通知", "updated_at": now(),
            "notification_id": None, "read": True}


def delivery_of(job):
    return (job.get("result") or {}).get("delivery") or {}


def completion_problem(job):
    """Verify current content and real exports, rather than trusting a passed badge."""
    from .coordination import active_steps, actual_checks
    from .document_delivery import document_checks
    delivery = delivery_of(job)
    try:
        validation = delivery.get("validation") or {}
        artifact = delivery.get("artifact") or {}
        latest = (delivery.get("versions") or [{}])[-1]
        review = (delivery.get("reviews") or [{}])[-1]
        content = delivery.get("content", "")
        digest = hashlib.sha256(content.encode()).hexdigest()
        requirements = {entry["id"] for entry in delivery["task_spec"]["requirements"]}
        reviewed = review.get("checks") or []
        if (delivery.get("status") != "complete" or validation.get("status") != "passed"
                or not content.strip() or not requirements or not artifact.get("version")
                or artifact.get("id") != job["id"] + ":delivery"
                or latest.get("number") != artifact["version"] or review.get("artifact_version") != artifact["version"]
                or latest.get("content") != content or latest.get("title") != delivery.get("title")
                or latest.get("asset_ids") != [entry.get("id") for entry in delivery.get("media", [])]
                or artifact.get("content_sha256") != digest or latest.get("content_sha256") != digest
                or validation.get("review") != review or review.get("goal_met") is not True
                or review.get("missing_requirements") or delivery.get("limitations")
                or len(reviewed) != len(requirements) or {entry.get("requirement_id") for entry in reviewed} != requirements
                or any(entry.get("met") is not True or entry.get("evidence_status") != "verified" for entry in reviewed)
                or not active_steps(delivery) or any(step.get("status") != "complete" for step in active_steps(delivery))):
            return "交付的正文版本、步骤或逐项审查证据尚未通过，请查看成果"
        failed = [entry["reason"] for entry in [*actual_checks(job, delivery), *document_checks(job, delivery)] if not entry["met"]]
        if failed:
            return "交付文件回读校验未通过：" + "；".join(failed)
    except (KeyError, TypeError, ValueError, OSError, HTTPException):
        return "交付记录或实际文件无法完整校验，请检查成果"
    return None


def continuation_blocker(job):
    """Return a concrete reason when re-execution is not demonstrably safe."""
    from .coordination import MAX_RESUMES, active_steps, actual_checks, document_retry_ready, run_budget
    delivery = delivery_of(job)
    workflow = delivery.get("workflow") or {}
    if job.get("group_harness_version") != 1 or job.get("group_delivery_mode") != "auto":
        return "此任务没有可恢复的协作流程"
    if job.get("status") == "cancelled" or (job.get("autonomy") or {}).get("stop_reason") == "user_cancel":
        return "任务已由用户停止，不会自动重新执行"
    steps = active_steps(delivery) if delivery.get("steps") else []
    if not workflow.get("context_digest") or not steps:
        return "计划或原始输入尚未完整保存，不能在后台重新派发，请重新创建任务"
    if workflow.get("resume_count", MAX_RESUMES) >= MAX_RESUMES or workflow.get("elapsed_seconds", run_budget(delivery)) >= run_budget(delivery):
        return "本轮继续次数或执行时间预算已用完，请检查现有成果"
    unfinished = [step for step in steps if step.get("status") != "complete"]
    from .operation_recovery import completed_operation, unstarted_operation
    if any(step.get("kind") != "model" and not (completed_operation(job, step) or unstarted_operation(job,step)) for step in unfinished):
        return "尚有原生 Agent、绘图或 MCP 步骤未确认完成；需要检查执行结果，后台不会重复派发"
    if unfinished and job.get("tools"):
        return "未完成的模型步骤启用了工具，可能已产生外部操作；请检查后手动继续"
    if any(step.get("approval_id") or step.get("status") in {"waiting_approval", "waiting_user", "uncertain"} for step in unfinished):
        return "任务正在等待批准或存在未确认操作，请先处理"
    if document_retry_ready(job, delivery):
        return None
    # Missing runtime evidence cannot be manufactured by another text response.
    requirements = {entry["id"]: entry for entry in delivery.get("task_spec", {}).get("requirements", [])}
    # Verified child results can contain files not yet copied into the parent.
    # Collection and final checks run during continuation; absence in the old
    # parent manifest must not prevent that first collection from happening.
    pending_external = any(step.get("kind") != "model" for step in unfinished)
    for check in ([] if pending_external else actual_checks(job, delivery)):
        if check.get("met") is not True and requirements.get(check["requirement_id"], {}).get("kind") in {"image", "video", "code", "tests"}:
            return "缺少可验证的实际文件或执行证据：" + check["reason"]
    if not unfinished:
        review = (delivery.get("validation") or {}).get("review") or {}
        if any(entry.get("met") is not True and entry.get("evidence_status") in {"limited", "unverified"}
               for entry in review.get("checks", [])):
            return "现有证据不足以完成验收，需要补充检查；后台不会重复生成或执行"
    return None


class AutonomySupervisor:
    def __init__(self, jobs, *, retry_delay=5, interval=3):
        self.jobs = jobs
        self.retry_delay = retry_delay
        self.interval = interval
        self.lock = asyncio.Lock()
        self.task = None

    def set_state(self, job, state, reason, *, next_action_at=None):
        meta = job["autonomy"]
        if (meta.get("state"), meta.get("reason"), meta.get("next_action_at")) == (state, reason, next_action_at):
            return
        old_state = meta.get("state")
        meta.update(state=state, reason=reason, next_action_at=next_action_at, updated_at=now())
        if state in {"complete", "needs_attention", "cancelled"} and old_state != state:
            meta.update(notification_id=str(uuid.uuid4()), read=False, outcome_at=now())
        elif state in {"running", "retry_wait", "paused"}:
            meta.update(notification_id=None, read=True)
        self.jobs.emit(job, "autonomy", autonomy=meta.copy())

    async def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop())

    async def shutdown(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def loop(self):
        while True:
            await self.tick()
            await asyncio.sleep(self.interval)

    async def tick(self):
        async with self.lock:
            for job in list(self.jobs.jobs.values()):
                if not isinstance(job.get("autonomy"), dict):
                    continue
                try:
                    await self.advance(job)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One damaged record must not stop all delegated projects.
                    try:
                        self.set_state(job, "needs_attention", "后台任务记录无法安全恢复，请打开任务检查")
                    except Exception:
                        logger.warning("Could not persist autonomous task state for %s", job.get("id", "unknown"))

    async def advance(self, job):
        from .coordination import ContinueRequest, continue_job, load_inputs
        meta = job["autonomy"]
        if job.get("status") == "cancelled" or meta.get("stop_reason") == "user_cancel":
            self.set_state(job, "cancelled", "任务已停止，不会自动继续")
            return
        if job.get("status") not in TERMINAL or self.jobs.tasks.get(job["id"]):
            if meta.get("enabled") is True:
                self.set_state(job, "running", "后台正在执行；可以离开此页面，Mac 和 ModelDesk 服务需要保持运行")
            return
        if delivery_of(job).get("status") == "complete":
            # Verify once at the completed transition; export endpoints also
            # re-read bytes. Avoid repeatedly hashing large videos every poll.
            if meta.get("state") == "complete":
                return
            problem = completion_problem(job)
            self.set_state(job, "needs_attention" if problem else "complete", problem or "成果及交付文件已完成校验，可打开查看")
            return
        if meta.get("enabled") is not True:
            return
        # Restore frozen MCP/skill context before checking saved external
        # receipts; this performs no model, native or tool operation.
        try:
            if delivery_of(job).get("workflow", {}).get("context_digest"):
                load_inputs(self.jobs, job)
        except HTTPException as error:
            self.set_state(job, "needs_attention", str(error.detail))
            return
        from .operation_recovery import resume_confirmed_observation
        from .mcp_observation import observe_pending
        mcp_observation = await observe_pending(job)
        if mcp_observation:
            self.jobs.save(job)
            self.set_state(job, mcp_observation["state"], mcp_observation["reason"])
            return
        observation = await resume_confirmed_observation(job)
        if observation:
            self.set_state(job, "running", "正在找回 Muse 原会话的结果；只继续观察，不重新发送创作任务")
            return
        blocker = continuation_blocker(job)
        if blocker:
            self.set_state(job, "needs_attention", blocker)
            return
        if meta.get("attempts", 0) >= MAX_ATTEMPTS:
            self.set_state(job, "needs_attention", "已达到两次后台继续上限，请检查保留的成果")
            return
        try:
            from .groups import store as rooms
            room = rooms.get(job["group_room_id"], job.get("project_id"))
            if room["team_snapshot"] != job.get("group_team_snapshot"):
                raise HTTPException(409, "群成员已经变化，请创建新任务")
            load_inputs(self.jobs, job)
        except HTTPException as error:
            self.set_state(job, "needs_attention", str(error.detail))
            return
        due = meta.get("next_action_at")
        if not due:
            due = (datetime.now(timezone.utc) + timedelta(seconds=self.retry_delay * (meta.get("attempts", 0) + 1))).isoformat()
            self.set_state(job, "retry_wait", "已保留完成的步骤，稍后继续安全的剩余工作", next_action_at=due)
            return
        if datetime.fromisoformat(due) > datetime.now(timezone.utc):
            return
        if sum(entry["status"] not in TERMINAL for entry in self.jobs.jobs.values()) >= 4:
            self.set_state(job, "retry_wait", "等待执行名额，已完成的成果会保留", next_action_at=due)
            return
        # No await between reservation and continue_job's synchronous guards and
        # scheduling. Pause/read use this same lock; crashes consume a bounded
        # attempt rather than risk an unrecorded repeated dispatch.
        meta.update(attempts=meta.get("attempts", 0) + 1, max_attempts=MAX_ATTEMPTS)
        self.set_state(job, "running", "正在继续未完成工作，已完成的步骤不会重跑")
        try:
            await continue_job(self.jobs, job["id"], job["group_room_id"], job.get("project_id"),
                               ContinueRequest(expected_revision=delivery_of(job)["workflow"]["revision"]),
                               native_allowed=job.get("group_native_allowed") is True, automatic=True)
        except HTTPException as error:
            meta["attempts"] -= 1  # No continuation was scheduled by a rejected admission.
            self.set_state(job, "needs_attention", str(error.detail))

    def rows(self, project_id=None, *, all_projects=False):
        from .harness.store import checked_id
        project_id = checked_id(project_id) if project_id else None
        rows = []
        for job in self.jobs.jobs.values():
            meta = job.get("autonomy")
            if not isinstance(meta, dict) or (not all_projects and job.get("project_id") != project_id):
                continue
            rows.append({"id": job["id"], "project_id": job.get("project_id"), "room_id": job.get("group_room_id"),
                         "title": (delivery_of(job).get("title") or job.get("prompt") or "后台任务")[:160],
                         "created_at": job.get("created_at"), **{key: meta.get(key) for key in [
                             "state", "reason", "attempts", "max_attempts", "next_action_at", "notification_id", "read", "updated_at", "outcome_at", "read_at"]}})
        rows.sort(key=lambda row: (row.get("updated_at") or "", row["id"]), reverse=True)
        counts = {state: sum(row["state"] == state for row in rows) for state in STATES}
        counts.update(total=len(rows), unread=sum(row.get("notification_id") is not None and not row.get("read") for row in rows))
        from scripts.modeldesk_worker import read_status
        worker = read_status()
        return {"tasks": rows, "counts": counts, "worker": {key: worker.get(key) for key in ["online", "state", "checked_at", "heartbeat_age_seconds", "scope", "services"]}}

    async def mutate(self, identifier, project_id, action):
        from .harness.store import checked_id
        identifier = checked_id(identifier)
        project_id = checked_id(project_id) if project_id else None
        async with self.lock:
            job = self.jobs.get(identifier)
            if job.get("project_id") != project_id or not job.get("autonomy"):
                raise HTTPException(404, "当前项目没有此后台任务")
            meta = job["autonomy"]
            if action == "read":
                meta.update(read=True, read_at=now())
                self.jobs.save(job)
            elif action == "pause":
                meta["enabled"] = False
                self.set_state(job, "paused", "已暂停自动继续；当前执行会继续完成，可在任务中手动继续或停止")
            return next(row for row in self.rows(project_id)["tasks"] if row["id"] == identifier)


supervisor = AutonomySupervisor(manager)
router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])


@router.get("/tasks")
async def tasks(project_id: str | None = None):
    return supervisor.rows(project_id)


@router.get("/inbox")
async def inbox(transport: Request):
    from .remote_access import require_local
    require_local(transport)
    if transport.headers.get("x-modeldesk-remote"):
        raise HTTPException(403, "全部项目的通知仅限本机")
    return supervisor.rows(all_projects=True)


@router.post("/tasks/{identifier}/read")
async def read(identifier: str, transport: Request, project_id: str | None = None):
    from .muse_bridge import local_mutation
    local_mutation(transport)
    return await supervisor.mutate(identifier, project_id, "read")


@router.post("/tasks/{identifier}/pause")
async def pause(identifier: str, transport: Request, project_id: str | None = None):
    from .muse_bridge import local_mutation
    local_mutation(transport)
    return await supervisor.mutate(identifier, project_id, "pause")
