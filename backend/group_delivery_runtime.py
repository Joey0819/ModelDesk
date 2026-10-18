"""Bounded, project-scoped adapters for automatic group delivery steps.

The existing managers retain their sandbox, native receipts, durable logs and
human approval boundaries. Confirmed Muse deliveries may receive one linked
revision; uncertain dispatches are never retried. This adapter never merges.
"""
import asyncio
import copy
from contextlib import asynccontextmanager
import hashlib
import time

from fastapi import HTTPException

from .harness.store import checked_id


MAX_PROMPT = 12000
MAX_OUTPUT = 16000
MAX_COMMAND_RECORDS = 20
MAX_COMMAND_PACKET_CHARS = 12000
CREATE_TIMEOUT_SECONDS = 30
RUNTIME_TIMEOUT_SECONDS = 600
POLL_SECONDS = .25
_locks = {}


@asynccontextmanager
async def _step_lock(key):
    # Count waiters as well as holders so concurrent calls cannot dispatch the
    # same native task twice, without retaining locks for every historical job.
    entry = _locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if not entry[1]:
            _locks.pop(key, None)


def _public(error):
    from .workbench import public_error
    return public_error(error.detail if isinstance(error, HTTPException) else error)


def _scope(job, member):
    from . import groups
    parent_id = checked_id(job["id"])
    room_id = checked_id(job["group_room_id"])
    project_id = checked_id(job["project_id"]) if job.get("project_id") else None
    if job.get("conversation_id", room_id) != room_id:
        raise ValueError("交付任务不属于当前项目群")
    frozen = job.get("group_team_snapshot")
    room = groups.store.get(room_id, project_id)
    if not isinstance(frozen, dict) or frozen != room["team_snapshot"]:
        raise ValueError("群成员已变化；请确认当前成员后重新发起交付")
    saved = next((item for item in frozen["members"] if item["id"] == member.get("id")), None)
    if saved != member or member.get("kind") not in {"codex", "claude", "muse"}:
        raise ValueError("自动执行只能使用本群已保存的运行时 Agent 成员")
    return parent_id, project_id, room_id


def _check_child(item, parent_id, project_id, room_id, member, *, linked=True):
    if (item.get("project_id") != project_id or item.get("room_id") != room_id
            or item.get("member_id") != member["id"] or item.get("member") != member
            or (member["kind"] != "muse" and item.get("engine") != member["kind"])
            or (linked and item.get("parent_job_id") != parent_id)):
        raise ValueError("运行时任务与当前项目群、成员或父任务不匹配")


def _get(manager, identifier, project_id, room_id, engine):
    if engine == "muse":
        item = manager.get(identifier, project_id)
    else:
        item = manager.get(identifier, project_id, room_id)
    if item.get("id") != identifier:
        raise ValueError("运行时任务标识不匹配")
    return item


def _link(manager, created, scope, member, prompt_sha256, revision_of=None):
    parent_id, project_id, room_id = scope
    identifier = checked_id(created["id"])
    item = manager.records.get(identifier)
    if item is None:
        raise ValueError("运行时未保存任务记录")
    if item.get("id") != identifier:
        raise ValueError("运行时任务标识不匹配")
    _check_child(created, parent_id, project_id, room_id, member, linked=False)
    _check_child(item, parent_id, project_id, room_id, member, linked=False)
    if item.get("parent_job_id") not in {None, parent_id}:
        raise ValueError("运行时任务已关联其他父任务")
    item.update(parent_job_id=parent_id, parent_step_member_id=member["id"], parent_prompt_sha256=prompt_sha256)
    if revision_of:
        item["revision_of_task_id"] = revision_of
    # No await between create returning and this durable parent linkage.
    manager.save(item)
    return identifier


async def _cancel(manager, identifier, project_id, room_id, engine):
    if engine == "muse":
        await manager.cancel(identifier, project_id)
    else:
        await manager.cancel(identifier, project_id, room_id)


def _command_evidence(item):
    from .group_tasks import clean_output
    values = item.get("commands", [])
    values = values if isinstance(values, list) else []
    remaining, result = MAX_COMMAND_PACKET_CHARS, []
    # Prefer the most recent real commands; report omitted/truncated evidence
    # rather than replacing it with the Agent's prose or inferring test success.
    for value in reversed(values[-MAX_COMMAND_RECORDS:]):
        if not isinstance(value, dict) or remaining <= 0:
            continue
        command = clean_output(str(value.get("command", "")))[:min(500, remaining)]
        remaining -= len(command)
        output = clean_output(str(value.get("aggregated_output", "")))[:min(1500, remaining)]
        remaining -= len(output)
        result.append({"command": command, "exit_code": value.get("exit_code") if type(value.get("exit_code")) is int else None,
                       "aggregated_output": output, "output_sha256": value.get("output_sha256"),
                       "truncated": bool(value.get("truncated")) or len(command) < len(str(value.get("command", "")))
                                    or len(output) < len(str(value.get("aggregated_output", "")))})
    result.reverse()
    observed = item.get("command_count", len(values))
    observed = observed if type(observed) is int and observed >= len(values) else len(values)
    return {"commands": result, "commands_observed": observed, "commands_omitted": observed - len(result)}


def _result(item, engine, status, error=None):
    from .attachments import secret_text
    if engine == "muse":
        from .muse_bridge import public_task
        item = public_task(item)
    raw = item.get("response" if engine == "muse" else "output", "")
    raw = raw if isinstance(raw, str) else ""
    hidden = secret_text(raw)
    output = "[已隐藏包含疑似凭据的运行时输出]" if hidden else raw[:MAX_OUTPUT]
    digest = hashlib.sha256(raw.encode()).hexdigest()
    source = {"kind": "runtime_agent", "task_id": item["id"], "engine": engine,
              "project_id": item["project_id"], "room_id": item["room_id"],
              "member_id": item["member_id"], "status": status,
              "output_sha256": digest, "truncated": len(raw) > MAX_OUTPUT,
              "redacted": hidden, "patch_sha256": item.get("diff_sha256"),
              "completion_evidence": item.get("completion_evidence"),
              "verification": item.get("verification", "")}
    result = {"status": status, "task_id": item["id"], "engine": engine,
              "runtime_status": item.get("status"), "output": output,
              "output_sha256": digest, "truncated": len(raw) > MAX_OUTPUT,
              "redacted": hidden, "sources": [source],
              "patch_sha256": item.get("diff_sha256"),
              "changed_files": copy.deepcopy(item.get("changed_files", [])),
              "exit_code": item.get("exit_code")}
    if engine in {"codex", "claude"}:
        result.update(_command_evidence(item))
    if engine == "muse" and status == "complete":
        source["thread_url"] = result["thread_url"] = item.get("thread_url")
    if error:
        result["error"] = _public(error)
    return result


def _finished(manager, item, engine, project_id, room_id):
    status = item.get("status")
    if engine == "muse":
        from .muse_bridge import unverified_generation, valid_thread
        if status == "complete":
            if (item.get("sent") is not True or item.get("uncertain") is not False
                    or item.get("completion_evidence") != "framed_assistant_reply"
                    or item.get("source_kind") != "native_muse" or unverified_generation(item)
                    or not valid_thread(item.get("thread_url"))
                    or not isinstance(item.get("response"), str) or not item["response"].strip()):
                return _result(item, engine, "blocked", "Muse 结束边界或原生送达未确认；不会自动重发")
            return _result(item, engine, "complete")
        if status in {"waiting_user", "uncertain", "interrupted", "stopped_monitoring"}:
            return _result(item, engine, "blocked", item.get("error") or "Muse 需要本机确认；不会自动重发")
    elif status == "complete":
        if item.get("exit_code") != 0 or not isinstance(item.get("output"), str) or not item["output"].strip():
            return _result(item, engine, "error", "执行 Agent 没有成功的实际输出")
        if item.get("changed_files"):
            # read_patch validates the saved bytes against the immutable hash.
            manager.read_patch(item["id"], project_id, room_id)
        return _result(item, engine, "complete")
    if status in {"error", "cancelled", "interrupted"}:
        return _result(item, engine, "error", item.get("error") or "执行 Agent 未完成交付")
    return None


async def run_runtime_step(job, member, prompt, *, native_allowed=False, revision_of=None):
    """Run one saved typed member and return actual output with provenance.

    ``complete`` means the child manager observed completion, never that its
    prose, tests or external actions were independently verified. Existing child
    records are reused by parent/member, including uncertain Muse deliveries.
    Parent cancellation stops a CLI or local Muse observation and propagates.
    """
    engine, manager, identifier, scope = member.get("kind"), None, None, None
    try:
        scope = _scope(job, member)
        parent_id, project_id, room_id = scope
        if engine == "muse" and native_allowed is not True:
            return {"status": "blocked", "task_id": None, "engine": engine, "output": "", "sources": [],
                    "error": "Muse 原生任务只能由本机入口授权执行；请在 Mac 上继续"}
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
            raise ValueError("执行任务需包含 1–12000 字符的完整需求和引用材料")
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        if engine == "muse":
            from .muse_bridge import manager, MuseTaskRequest
            request = MuseTaskRequest(project_id=project_id, room_id=room_id, member_id=member["id"], prompt=prompt,
                                      timeout_seconds=RUNTIME_TIMEOUT_SECONDS)
        else:
            from .group_tasks import manager, TaskRequest
            if not project_id:
                raise ValueError("代码 Agent 需要已关联的本地 Git 项目")
            request = TaskRequest(project_id=project_id, room_id=room_id, member_id=member["id"], engine=engine,
                                  prompt=prompt, timeout_seconds=RUNTIME_TIMEOUT_SECONDS)
        async with _step_lock((parent_id, member["id"])):
            _scope(job, member)
            if revision_of is not None:
                if engine != "muse":
                    raise ValueError("此自动修订通道仅支持已确认完成的 Muse 任务")
                original = _get(manager, checked_id(revision_of), project_id, room_id, engine)
                _check_child(original, *scope, member)
                finished = _finished(manager, original, engine, project_id, room_id)
                if original.get("revision_of_task_id") or not finished or finished["status"] != "complete":
                    raise ValueError("只能修订已确认完成的原始任务，不能重发未确认任务或再次修订")
                if any(item.get("parent_job_id") == parent_id and item.get("revision_of_task_id")
                       and item["revision_of_task_id"] != revision_of for item in manager.records.values()):
                    raise ValueError("本轮原生修订预算已用完")
            existing = [item for item in manager.records.values()
                        if item.get("parent_job_id") == parent_id and item.get("member_id") == member["id"]
                        and item.get("revision_of_task_id") == revision_of]
            if len(existing) > 1:
                raise ValueError("父任务存在重复的执行记录；请核对现有任务，不会再次派发")
            if existing:
                item = existing[0]
                _check_child(item, *scope, member)
                if item.get("parent_prompt_sha256") != prompt_sha256:
                    raise ValueError("同一父任务的执行内容已变化；不会重复派发")
                identifier = item["id"]
            else:
                try:
                    # Both existing managers queue a child only after their
                    # awaited preflight, with no await after queuing. Stay in
                    # this coroutine to link it before its runner can execute.
                    async with asyncio.timeout(CREATE_TIMEOUT_SECONDS):
                        created = await manager.create(request, parent_job_id=parent_id)
                        candidate = checked_id(created["id"])
                        canonical = _get(manager, candidate, project_id, room_id, engine)
                        _check_child(created, *scope, member)
                        _check_child(canonical, *scope, member)
                        identifier = candidate
                        identifier = _link(manager, created, scope, member, prompt_sha256, revision_of)
                except asyncio.TimeoutError:
                    raise ValueError("创建执行任务超时；没有自动重试")
            deadline = time.monotonic() + RUNTIME_TIMEOUT_SECONDS + CREATE_TIMEOUT_SECONDS
            while True:
                _scope(job, member)
                item = _get(manager, identifier, project_id, room_id, engine)
                _check_child(item, *scope, member)
                finished = _finished(manager, item, engine, project_id, room_id)
                if finished is not None:
                    if engine == "muse" and finished["status"] == "blocked" and item.get("monitoring"):
                        canonical = manager.records[identifier]
                        canonical.update(delivery_observed_status=item["status"], delivery_waiting_reason=item.get("error"))
                        manager.save(canonical)
                        await _cancel(manager, identifier, project_id, room_id, engine)
                        finished.update(observation_stopped=True, native_cancel_supported=False)
                    return finished
                runner = manager.tasks.get(identifier)
                if not runner or runner.done():
                    return _result(item, engine, "blocked", "运行时观察已停止；请核对原任务，不会自动重试")
                if time.monotonic() >= deadline:
                    await _cancel(manager, identifier, project_id, room_id, engine)
                    item = _get(manager, identifier, project_id, room_id, engine)
                    _check_child(item, *scope, member)
                    return _result(item, engine, "blocked", "等待执行任务超时；已停止本地等待，没有自动重试")
                await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        if manager is not None and identifier and scope:
            await asyncio.shield(_cancel(manager, identifier, scope[1], scope[2], engine))
        raise
    except Exception as error:
        # A changed roster must stop work rather than attach a stale member's
        # delivery to the new group. Scope-check the original child before cancel.
        if manager is not None and identifier and scope:
            try:
                item = _get(manager, identifier, scope[1], scope[2], engine)
                _check_child(item, *scope, member)
                await _cancel(manager, identifier, scope[1], scope[2], engine)
                item = _get(manager, identifier, scope[1], scope[2], engine)
                _check_child(item, *scope, member)
                return _result(item, engine, "error", error)
            except Exception:
                pass
        return {"status": "error", "task_id": None, "engine": engine, "output": "", "sources": [], "error": _public(error)}
