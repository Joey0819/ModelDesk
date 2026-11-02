"""Observe acknowledged MCP tasks; never call tools/call from recovery."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from jsonschema import Draft202012Validator
from mcp.types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field
from referencing import Registry

from . import mcp_agent, mcp_tasks
from .harness.context import revision
from .harness.store import checked_id
from .workbench import now

router = APIRouter(prefix="/api/mcp-agent-tasks", tags=["mcp-agent-tasks"])


def read_record(identifier):
    path = mcp_agent.STORE.directory / (checked_id(identifier) + '.json')
    if path.is_symlink() or mcp_agent.STORE.directory.is_symlink() or not path.is_file():
        raise HTTPException(404, "MCP 原始执行记录不存在")
    return json.loads(path.read_text())


async def checked_record(job, step):
    from .group_delivery_runtime import _scope
    member = next(item for item in job["group_team_snapshot"]["members"] if item["id"] == step["member_id"])
    result = await mcp_agent.run_mcp_step(job, member, step["dispatch_prompt"], scope=_scope(job, member),
                                        skill_snapshot=job.get("_skill_snapshots"), observe_only=True)
    return member, read_record(result["task_id"])


async def poll(job, step):
    from .group_delivery_runtime import _step_lock
    member, checked = await checked_record(job, step)
    async with _step_lock((job["id"], member["id"])):
        record = read_record(checked["id"])
        if record["status"] != "observing":
            return record
        task = record["remote_task"]
        if record.get("seed_pending"):
            due = not record.get("next_poll_at") or datetime.now(timezone.utc) >= datetime.fromisoformat(record["next_poll_at"])
        else:
            due = mcp_tasks.poll_due(record)
        if not due:
            return record
        if mcp_tasks.expired(task) or (datetime.now(timezone.utc) - datetime.fromisoformat(record["accepted_at"])).total_seconds() > 86400:
            record.update(status="error", error="MCP 任务查询期限已到；保留原任务标识，未重新派发")
            mcp_agent.STORE.save(record)
            return record
        record["next_poll_at"] = mcp_tasks.next_poll(task)
        mcp_agent.STORE.save(record)
        try:
            config = mcp_agent.configured_server(job, member)
            async with asyncio.timeout(25), mcp_agent.agent_session(config) as session:
                value = await mcp_tasks.request(session, "tasks/get", {"taskId":task["taskId"]})
            if value.get("resultType") != "complete":
                raise ValueError("MCP 查询返回的任务格式无效")
            observed = mcp_tasks.task_state(value, task["taskId"])
            if observed["createdAt"] != task["createdAt"] or datetime.fromisoformat(observed["lastUpdatedAt"].replace('Z','+00:00')) < datetime.fromisoformat(task["lastUpdatedAt"].replace('Z','+00:00')):
                raise ValueError("MCP 原始任务时间不一致，保留已确认记录")
            record.update(remote_task=observed, last_observed_at=now(), observation_error=None,
                          observation_failures=0, seed_pending=False, next_poll_at=mcp_tasks.next_poll(observed))
            if observed["status"] == "completed":
                result = CallToolResult.model_validate(observed["result"])
                if not result.isError:
                    mcp_agent.validate_output_schema(record.get("output_schema"), result)
                output, warning = mcp_agent.actual_text(result)
                record.update(status="complete", output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(),
                              warning=warning, result_provenance=mcp_agent.result_provenance(result.model_dump(mode="json")), error=None)
            elif observed["status"] in {"failed", "cancelled"}:
                record.update(status="error", error="MCP 远端任务已失败；未重复派发" if observed["status"] == "failed" else "MCP 远端已确认任务取消")
            elif observed["status"] == "input_required":
                record["error"] = "MCP Agent 需要你的答复；请在任务中查看请求，平台不会代为批准"
            else:
                record["error"] = None
        except asyncio.CancelledError:
            raise  # Original durable handle stays observable after restart.
        except Exception as error:
            if (record.get("remote_task") or {}).get("status") == "completed":
                record.update(status="error", error=str(error) if isinstance(error, ValueError) and str(error).startswith("MCP") else "MCP 最终成果验证失败；原任务不会重发")
            else:
                failures = record.get("observation_failures", 0) + 1
                delay = min(300, 5 * 2 ** min(failures, 6))
                next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                record.update(observation_failures=failures, observation_error="暂时无法查询 MCP 原任务；保留任务标识并按退避间隔重连，不会重新派发",
                              next_poll_at=max(record["next_poll_at"], next_at.isoformat()))
        record["updated_at"] = now()
        mcp_agent.STORE.save(record)
        return record


async def observe_pending(job):
    delivery = (job.get("result") or {}).get("delivery") or {}
    for step in delivery.get("steps", []):
        if step.get("kind") != "mcp" or step.get("status") == "complete" or not step.get("dispatch_prompt"):
            continue
        try:
            record = await poll(job, step)
            receipt = mcp_agent.public_record(record)
            # The step remains pending until normal continuation consumes the
            # validated output. Observing is never counted as completed work.
            step["task_observation"] = receipt.get("task_observation")
            if record["status"] == "observing":
                waiting = record["remote_task"]["status"] == "input_required"
                return {"state":"needs_attention" if waiting else "running", "reason":record.get("error") if waiting else
                        record.get("observation_error") or "MCP Agent 已接收任务，正在后台查询进度；无需保持页面打开"}
        except Exception:
            continue  # Existing fail-closed continuation handles missing proof.
    return None


async def wait_for_result(job, member, prompt, receipt):
    """Keep an ordinary parent run alive while the server owns the long work."""
    from .workbench import manager
    step = {"member_id":member["id"], "dispatch_prompt":prompt}
    # Long server waits do not consume the parent's model/assembly budget.
    # The durable supervisor takes over after this short foreground window.
    deadline = asyncio.get_running_loop().time() + 30 if job.get("autonomy", {}).get("enabled") else float('inf')
    while receipt.get("remote_state") == "observing":
        observation = receipt.get("task_observation") or {}
        parent = manager.jobs.get(job["id"])
        saved = next((item for item in (parent or {}).get("result", {}).get("delivery", {}).get("steps", [])
                      if item.get("member_id") == member["id"] and item.get("kind") == "mcp"), None)
        if saved is not None:
            saved.update(task_id=receipt["task_id"], task_observation=observation)
            manager.save(parent)
        if (observation.get("remote_task") or {}).get("status") == "input_required":
            return receipt
        if asyncio.get_running_loop().time() >= deadline:
            return receipt
        await asyncio.sleep(1)
        receipt = mcp_agent.public_record(await poll(job, step))
    return receipt


class TaskAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^(accept|decline|cancel_input|cancel_task)$")
    request_key: str | None = Field(default=None, max_length=512)
    request_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content: dict | None = None


def resolve(identifier):
    from .workbench import manager
    from .coordination import load_inputs
    record = read_record(identifier)
    job = manager.get(record["parent_job_id"])
    load_inputs(manager, job)
    step = next((item for item in (job.get("result") or {}).get("delivery", {}).get("steps", [])
                 if item.get("kind") == "mcp" and item.get("member_id") == record["member"]["id"]), None)
    if not step:
        raise HTTPException(409, "MCP 原始步骤不存在")
    return job, step


@router.get("/{identifier}")
async def get_task(identifier: str):
    job, step = resolve(identifier)
    _, record = await checked_record(job, step)
    return mcp_agent.public_record(record)


@router.post("/{identifier}/actions")
async def act_on_task(identifier: str, body: TaskAction):
    from .group_delivery_runtime import _step_lock
    job, step = resolve(identifier)
    if job.get("status") == "cancelled" and body.action != "cancel_task":
        raise HTTPException(409, "本地任务已停止，不能继续批准远端执行")
    member, checked = await checked_record(job, step)
    async with _step_lock((job["id"], member["id"])):
        record = read_record(checked["id"])
        if record["status"] != "observing":
            raise HTTPException(409, "原任务已结束或不支持远端操作")
        task = record["remote_task"]
        params = {"taskId":task["taskId"]}
        if body.action == "cancel_task":
            if record.get("cancel_requested"):
                raise HTTPException(409, "取消请求已经发出；请等待远端实际状态，不会重复发送")
            method = "tasks/cancel"
            record["cancel_requested"] = {"state":"dispatching", "at":now()}
            dispatch = record["cancel_requested"]
        else:
            request = (task.get("inputRequests") or {}).get(body.request_key)
            if not request or revision(request) != body.request_sha256:
                raise HTTPException(409, "待答复的请求已变化，请刷新后核对")
            if request.get("method") != "elicitation/create" or request.get("params", {}).get("mode", "form") != "form":
                raise HTTPException(422, "当前仅支持表单答复；其他请求需要在原服务处理，平台不会自动执行")
            responses = record.setdefault("input_responses", {})
            if body.request_key in responses:
                raise HTTPException(409, "该请求已答复或发送状态尚未确认，不会自动重发")
            if body.action == "accept":
                from .attachments import secret_text
                if body.content is None or len(json.dumps(body.content)) > 16000 or secret_text(json.dumps(body.content)):
                    raise HTTPException(422, "请填写不含凭据、长度合适的答复")
                try:
                    Draft202012Validator(request["params"]["requestedSchema"], registry=Registry()).validate(body.content)
                except Exception:
                    raise HTTPException(422, "答复不符合 Agent 请求的字段格式") from None
            result = {"action":"cancel" if body.action == "cancel_input" else body.action}
            if body.action == "accept":
                result["content"] = body.content
            method = "tasks/update"
            params["inputResponses"] = {body.request_key:result}
            responses[body.request_key] = {"state":"dispatching", "at":now(), "request_sha256":body.request_sha256,
                                            "response_sha256":revision(result), "action":body.action}
            dispatch = responses[body.request_key]
        mcp_agent.STORE.save(record)
        try:
            async with asyncio.timeout(25), mcp_agent.agent_session(mcp_agent.configured_server(job, member)) as session:
                result = await mcp_tasks.request(session, method, params)
            if result.get("resultType") != "complete":
                raise ValueError("Invalid acknowledgement")
            dispatch["state"] = "acknowledged"
        except Exception:
            dispatch["state"] = "uncertain"
        record.update(next_poll_at=mcp_tasks.next_poll(task), updated_at=now())
        mcp_agent.STORE.save(record)
        return mcp_agent.public_record(record)
