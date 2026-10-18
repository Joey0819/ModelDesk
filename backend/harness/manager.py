"""LangGraph schedules steps; RunStore owns durable checkpoints and replay policy."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .. import workbench
from ..attachments import store as assets
from ..platform_store import store as resources
from .context import capture_revisions, validate_revisions, revision
from ..super_platform import validate_context
from .engines import output_for_job, participants_for_step, request_for_step
from .store import RunStore, checked_id, public_run
from .teams import TeamStore

ACTIVE = {"queued", "running"}



class GraphState(TypedDict):
    run_id: str


class HarnessManager:
    def __init__(self, directory=None, jobs=None):
        self.store = RunStore(directory or workbench.PROJECT / "data/harness")
        self.teams = TeamStore(self.store.directory / "teams")
        self.jobs = jobs or workbench.manager
        self.runs, self.tasks = {}, {}
        self.clocks = {}
        graph = StateGraph(GraphState)
        graph.add_node("execute_step", self.advance)
        graph.add_edge(START, "execute_step")
        graph.add_conditional_edges("execute_step", self.route, {"next": "execute_step", "done": END})
        self.graph = graph.compile()

    def route(self, state):
        return "next" if self.runs[state["run_id"]]["status"] == "running" else "done"

    def save_event(self, run, kind, **fields):
        self.account_time(run)
        timestamp = workbench.now()
        run["updated_at"] = timestamp
        run["events"].append({"seq": len(run["events"]) + 1, "type": kind, "created_at": timestamp, **fields})
        self.store.save(run)

    def account_time(self, run):
        previous = self.clocks.get(run["id"])
        if previous is not None:
            current = time.monotonic()
            run["elapsed_seconds"] += max(0, current - previous)
            self.clocks[run["id"]] = current

    def get(self, identifier):
        run = self.runs.get(checked_id(identifier))
        if run is None:
            raise HTTPException(404, "协作任务不存在")
        return run

    def recover(self):
        for run in self.store.load():
            self.runs[run["id"]] = run
            if run["status"] not in ACTIVE:
                continue
            # Unobserved work after the last durable checkpoint consumes the
            # remaining budget conservatively; restarting never resets a limit.
            try:
                unobserved = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(run["updated_at"])).total_seconds())
                run["elapsed_seconds"] = min(run["request"]["timeout_seconds"], run["elapsed_seconds"] + unobserved)
            except (ValueError, TypeError, KeyError):
                pass
            for step in run["steps"]:
                if step["status"] == "complete" and step["config"]["require_review"] and step.get("review_status") != "approved":
                    step["review_status"] = "pending"
            # A child may have committed its result immediately before parent crash.
            # Reconcile observed success, but never launch pending steps on startup.
            for step in run["steps"]:
                if step["status"] != "running" or not step["attempts"]:
                    continue
                attempt = step["attempts"][-1]
                child = self.jobs.jobs.get(attempt["job_id"])
                if child and child.get("harness_run_id") == run["id"] and child.get("harness_step_id") == step["id"]:
                    output, error = output_for_job(child)
                    if not error:
                        step.update(status="complete", output=output)
                        attempt.update(status="complete", finished_at=child.get("updated_at"))
                        if step["config"]["require_review"]:
                            step["review_status"] = "pending"
                        continue
                step["status"] = "interrupted"
                attempt.update(status="interrupted", error="服务重启中断此步骤；重新执行可能再次调用工具")
            waiting = next((step for step in run["steps"] if step.get("review_status") == "pending"), None)
            if waiting:
                run.update(status="waiting_review", active_step_id=waiting["id"], error=None)
            elif all(step["status"] == "complete" for step in run["steps"]):
                self.complete(run)
                continue
            else:
                run.update(status="interrupted", error="服务已重启。已完成步骤保留；请明确继续未完成步骤。")
            self.save_event(run, "recovered", status=run["status"])

    def capture_revisions(self, body):
        return capture_revisions(body, resources)

    def validate_revisions(self, run):
        validate_revisions(run["resource_revisions"], run.get("project_id"), resources)

    async def create(self, request, *, resolved_team=None):
        if sum(run["status"] in ACTIVE for run in self.runs.values()) >= 4:
            raise HTTPException(429, "最多同时执行四个协作任务")
        if resolved_team is None:
            request, team_snapshot = self.teams.resolve(request)
        else:
            request, team_snapshot = resolved_team
        body = request.model_dump(mode="json")
        await asyncio.to_thread(validate_context, body)
        if sum(run["status"] in ACTIVE for run in self.runs.values()) >= 4:
            raise HTTPException(429, "最多同时执行四个协作任务")
        identifier, timestamp = str(uuid.uuid4()), workbench.now()
        run = {"schema_version": 1, "id": identifier, "title": body["goal"][:100], "goal": body["goal"],
               "project_id": body["project_id"], "status": "queued", "created_at": timestamp, "updated_at": timestamp,
               "request": body, "team_snapshot": team_snapshot, "steps": [{"id": step["id"], "title": step["title"], "mode": step["mode"],
                   "config": step, "status": "pending", "attempts": [], "output": None, "review_status": None} for step in body["steps"]],
               "active_step_id": None, "error": None, "result": None, "events": [], "elapsed_seconds": 0,
               "scheduler": "langgraph", "checkpoint_store": "modeldesk-local", "resource_revisions": self.capture_revisions(body)}
        for step in run["steps"]:
            request_for_step(run, step)
            step["participant_models"], step["participant_policy"] = participants_for_step(run, step)
        # Freeze before starting any child. Every attempt gets these same bytes.
        source = {"id": identifier, **body}
        run["attachment_manifest"] = assets.freeze(source, self.store.directory / "inputs")
        self.runs[identifier] = run
        self.save_event(run, "created", steps=len(run["steps"]))
        self.start(run)
        return public_run(run)

    def start(self, run):
        if run["id"] in self.tasks:
            raise HTTPException(409, "此协作任务正在执行")
        self.check_capacity(run)
        run.update(status="queued", error=None)
        self.store.save(run)
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run))

    def check_capacity(self, run):
        if sum(item["status"] in ACTIVE for item in self.runs.values() if item["id"] != run["id"]) >= 4:
            raise HTTPException(429, "最多同时执行四个协作任务，请等待当前任务完成")

    async def execute(self, run):
        self.clocks[run["id"]] = time.monotonic()
        try:
            run["status"] = "running"
            self.save_event(run, "started")
            remaining = run["request"]["timeout_seconds"] - run["elapsed_seconds"]
            if remaining <= 0:
                raise TimeoutError()
            # Disable inherited LangSmith tracing: graph state stays on this machine.
            with tracing_context(enabled=False):
                async with asyncio.timeout(remaining):
                    await self.graph.ainvoke({"run_id": run["id"]}, {"recursion_limit": 16})
        except asyncio.CancelledError:
            await self.stop_child(run)
            if run["status"] in ACTIVE:
                run.update(status="interrupted", error="协作执行已中断；已完成步骤保留")
                self.save_event(run, "interrupted")
        except Exception as error:
            await self.stop_child(run)
            run.update(status="error", error="协作运行达到时间上限" if isinstance(error, TimeoutError) else workbench.public_error(error))
            self.save_event(run, "error", message=run["error"])
        finally:
            self.account_time(run)
            self.clocks.pop(run["id"], None)
            self.store.save(run)
            self.tasks.pop(run["id"], None)

    async def advance(self, state):
        run = self.runs[state["run_id"]]
        if run["status"] != "running":
            return state
        step = next((item for item in run["steps"] if item["status"] != "complete"), None)
        if step is None:
            self.complete(run)
            return state
        self.validate_revisions(run)
        if len(step["attempts"]) >= run["request"]["max_attempts"]:
            raise HTTPException(409, "此步骤已达到重试次数上限，请创建新协作任务")
        request = request_for_step(run, step)
        identifier = str(uuid.uuid4())
        attempt = {"number": len(step["attempts"]) + 1, "job_id": identifier, "status": "running", "started_at": workbench.now(), "finished_at": None, "error": None}
        step["attempts"].append(attempt)
        step["status"] = "running"
        run["active_step_id"] = step["id"]
        # Commit the reserved ID before child creation so restart cannot duplicate it.
        self.save_event(run, "step_started", step_id=step["id"], job_id=identifier, attempt=attempt["number"])
        try:
            source = {"id": run["id"], "project_id": run["project_id"], "attachment_manifest": run.get("attachment_manifest")}
            self.jobs.create(request, job_id=identifier, metadata={"harness_run_id": run["id"], "harness_step_id": step["id"], "harness_attempt": attempt["number"], "harness_resource_revisions": run["resource_revisions"], "harness_team_snapshot": run.get("team_snapshot")},
                             attachment_source=(source, self.store.directory / "inputs"))
            task = self.jobs.tasks.get(identifier)
            if task:
                async with asyncio.timeout(run["request"]["step_timeout_seconds"]):
                    await asyncio.shield(task)
            child = self.jobs.get(identifier)
            output, error = output_for_job(child)
            step["output"] = output
            if error:
                raise RuntimeError(error)
            step["status"] = "complete"
            attempt.update(status="complete", finished_at=workbench.now())
            if step["config"]["require_review"]:
                step["review_status"] = "pending"
                run["status"] = "waiting_review"
            self.save_event(run, "step_complete", step_id=step["id"], job_id=identifier)
            if step["config"]["require_review"]:
                self.save_event(run, "review_requested", step_id=step["id"])
            elif all(item["status"] == "complete" for item in run["steps"]):
                self.complete(run)
        except asyncio.CancelledError:
            await self.stop_child(run)
            raise
        except Exception as error:
            await self.stop_child(run)
            message = "步骤达到运行时间上限，工作进程已停止" if isinstance(error, TimeoutError) else workbench.public_error(error)
            step["status"] = "error"
            attempt.update(status="error", error=message, finished_at=workbench.now())
            run.update(status="error", error=message)
            self.save_event(run, "step_error", step_id=step["id"], job_id=identifier, message=message)
        return state

    def complete(self, run):
        last = run["steps"][-1]
        run.update(status="complete", error=None, active_step_id=None,
                   result={**last["output"], "step_id": last["id"]})
        self.save_event(run, "complete", step_id=last["id"])

    async def stop_child(self, run):
        step = next((item for item in run["steps"] if item["status"] == "running"), None)
        if not step or not step["attempts"]:
            return
        attempt = step["attempts"][-1]
        child = self.jobs.jobs.get(attempt["job_id"])
        if child and child.get("status") not in workbench.TERMINAL:
            await self.jobs.cancel(child["id"])
        status = "cancelled" if run["status"] == "cancelled" else "interrupted"
        step["status"] = status
        attempt.update(status=status, finished_at=workbench.now())

    async def cancel(self, identifier):
        run = self.get(identifier)
        if run["status"] not in {*ACTIVE, "waiting_review"}:
            return public_run(run)
        run.update(status="cancelled", error=None)
        self.save_event(run, "cancelled")
        task = self.tasks.get(run["id"])
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(run["id"], None)
        await self.stop_child(run)
        self.store.save(run)
        return public_run(run)

    async def resume(self, identifier):
        run = self.get(identifier)
        if run["status"] not in {"error", "interrupted", "cancelled"}:
            raise HTTPException(409, "只有未完成的协作任务可以重试；等待审阅的步骤请使用继续")
        if run["id"] in self.tasks:
            raise HTTPException(409, "上一轮执行正在收尾，请稍后重试")
        if run["elapsed_seconds"] >= run["request"]["timeout_seconds"]:
            raise HTTPException(409, "此任务已达到总运行时间上限，请创建新协作任务")
        self.validate_revisions(run)
        pending_review = next((step for step in run["steps"] if step.get("review_status") == "pending"), None)
        if pending_review:
            run.update(status="waiting_review", active_step_id=pending_review["id"], error=None)
            self.save_event(run, "review_requested", step_id=pending_review["id"])
            return public_run(run)
        step = next((item for item in run["steps"] if item["status"] != "complete"), None)
        if step is None:
            self.complete(run)
            return public_run(run)
        if len(step["attempts"]) >= run["request"]["max_attempts"]:
            raise HTTPException(409, "此步骤已达到重试次数上限，请创建新协作任务")
        self.check_capacity(run)
        step["status"] = "pending"
        self.save_event(run, "retry_requested", step_id=step["id"])
        self.start(run)
        return public_run(run)

    async def approve(self, identifier):
        run = self.get(identifier)
        if run["status"] != "waiting_review":
            raise HTTPException(409, "此任务没有等待审阅的步骤")
        # The graph runner normally has settled before a human clicks; handle fast clients too.
        task = self.tasks.get(run["id"])
        if task:
            await asyncio.shield(task)
        if run["status"] != "waiting_review":
            raise HTTPException(409, "任务状态已变化，请重新查看后继续")
        step = next((item for item in run["steps"] if item.get("review_status") == "pending"), None)
        if not step:
            raise HTTPException(409, "没有待确认的步骤")
        self.validate_revisions(run)
        if not all(item["status"] == "complete" for item in run["steps"]):
            self.check_capacity(run)
        step["review_status"] = "approved"
        self.save_event(run, "review_approved", step_id=step["id"])
        if all(item["status"] == "complete" for item in run["steps"]):
            self.complete(run)
        else:
            self.start(run)
        return public_run(run)

    async def shutdown(self):
        for identifier, task in list(self.tasks.items()):
            run = self.runs[identifier]
            run.update(status="interrupted", error="服务停止；已完成步骤保留，请明确继续未完成步骤")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(identifier, None)
            self.save_event(run, "interrupted")


manager = HarnessManager()
