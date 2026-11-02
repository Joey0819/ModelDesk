"""One explicitly configured MCP tool as an independently dispatched group member.

No model chooses an endpoint, tool or arbitrary arguments. Every dispatch is
checkpointed before call_tool; uncertain calls are never replayed. This first
adapter accepts synchronous text/structured results, not unverified file links.
"""
import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import uuid

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from referencing import Registry

from .harness.context import revision
from .harness.store import RunStore, checked_id
from .platform_store import store as resources
from .mcp_protocol import (list_all_tools, validate_output_schema, complete_result_text,
                           result_provenance)

MAX_OUTPUT = 80000
TIMEOUT_SECONDS = 120
STORE = RunStore(Path(__file__).resolve().parents[1] / "data/mcp-agent-tasks")


@asynccontextmanager
async def agent_session(config):
    from mcp import ClientSession
    if config["transport"] == "sse":
        from mcp.client.sse import sse_client
        transport = sse_client(config["url"], timeout=15, sse_read_timeout=TIMEOUT_SECONDS)
    else:
        from mcp.client.streamable_http import streamablehttp_client
        transport = streamablehttp_client(config["url"], timeout=timedelta(seconds=15),
                                          sse_read_timeout=timedelta(seconds=TIMEOUT_SECONDS))
    async with transport as streams:
        async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=TIMEOUT_SECONDS)) as session:
            await session.initialize()
            yield session


def configured_server(job, member):
    identifier = str(member.get("mcp_server_id") or "")
    record = resources.get("mcp", identifier)
    if record.get("project_id") not in {None, job.get("project_id")}:
        raise HTTPException(403, "MCP Agent 的连接不属于当前项目")
    if not record.get("enabled") or member.get("mcp_tool") not in record.get("allowed_tools", []):
        raise HTTPException(403, "MCP Agent 工具尚未明确允许，或服务已停用")
    return record


def validate_schema(schema, field, prompt=None):
    # Accept a single string handoff, with optional server-side parameters.
    # Complex endpoints can expose a small task wrapper using the same MCP SDK.
    if (not isinstance(schema, dict) or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
            or not isinstance(schema["properties"].get(field), dict)
            or schema["properties"][field].get("type") != "string"
            or not isinstance(schema.get("required", []), list)
            or any(name != field for name in schema.get("required", []))):
        raise ValueError("此 MCP 工具需以一个字符串参数接收完整任务；请选择正确任务参数，或提供单参数 Agent 工具")
    Draft202012Validator.check_schema(schema)
    if prompt is not None:
        # No schema-triggered remote fetch. Unknown references fail locally.
        Draft202012Validator(schema, registry=Registry()).validate({field: prompt})


async def discover_snapshot(job, member):
    config = configured_server(job, member)
    async with asyncio.timeout(25), agent_session(config) as session:
        tools = await list_all_tools(session)
    tool = next((item for item in tools if item.name == member["mcp_tool"]), None)
    if tool is None:
        raise ValueError("MCP 服务未发布所选工具，请重新检查连接")
    field = member.get("mcp_prompt_field", "prompt")
    validate_schema(tool.inputSchema, field)
    validate_output_schema(tool.outputSchema)
    return {"server_id": config["id"], "server_revision": revision(config), "tool": tool.name,
            "prompt_field": field, "input_schema": copy.deepcopy(tool.inputSchema),
            "schema_sha256": revision(tool.inputSchema), "output_schema": copy.deepcopy(tool.outputSchema),
            "output_schema_sha256": revision(tool.outputSchema),
            "output_kind": "text", "timeout_seconds": TIMEOUT_SECONDS}


async def freeze_mcp_members(job, members=None):
    members = members if members is not None else (job.get("group_team_snapshot") or {}).get("members", [])
    snapshots = {}
    for member in members:
        if member.get("kind") == "mcp":
            snapshots[member["id"]] = await discover_snapshot(job, member)
    job["_mcp_agent_snapshots"] = snapshots
    return snapshots


def actual_text(result):
    value = result.model_dump(mode="json")
    if value.get("isError"):
        raise ValueError("MCP Agent 返回工具执行错误；没有标记为完成，也没有自动重发")
    # Both channels are server output; a human-readable summary is not a substitute
    # for the returned rows, measurements or other structured evidence.
    unsupported = sorted({item.get("type", "unknown") for item in value.get("content", []) if item.get("type") != "text"})
    output = complete_result_text(value)
    if not output:
        raise ValueError("MCP Agent 没有返回文本成果；图片、文件或外部资源链接尚未作为实际文件导入")
    warning = ("MCP 返回了尚未导入的非文本资源（" + "、".join(unsupported) + "）；不作为已验证文件交付。") if unsupported else None
    if warning:
        output += "\n\n" + warning
    if len(output) > MAX_OUTPUT:
        raise ValueError("MCP Agent 成果超过 80000 字符，原任务未重发；请在服务端提供可分段读取的输出")
    from .attachments import secret_text
    if secret_text(output):
        raise ValueError("MCP Agent 返回内容含疑似凭据，未传入群聊或文档")
    return output, warning


def public_record(record):
    status = record["status"]
    if status == "dispatching":
        status = "uncertain"
    return {"status": "complete" if status == "complete" else "blocked" if status == "uncertain" else "error",
            "task_id": record["id"], "engine": "mcp", "output": record.get("output", ""), "sources": [],
            "output_sha256": record.get("output_sha256"), "error": record.get("error") or
            ("MCP 调用结果未确认；请检查原服务，不会重复派发。" if status == "uncertain" else None),
            "remote_state": status, "dispatch_count": record.get("dispatch_count", 0),
            "mcp_receipt": {key: record.get(key) for key in ["server_id", "tool", "schema_sha256", "output_schema_sha256",
                                                          "prompt_sha256", "warning", "result_provenance"]},
            "skill_application": record.get("skill_application")}


async def run_mcp_step(job, member, prompt, *, scope, skill_snapshot=None, observe_only=False):
    from .group_delivery_runtime import _step_lock, _scope
    from .native_skills import freeze_native_skills
    from .skill_library import skill_prompt
    from .workbench import now
    parent_id, project_id, room_id = scope
    frozen = job.get("_mcp_agent_snapshots", {}).get(member["id"])
    if not frozen:
        raise ValueError("MCP Agent 的能力快照缺失，请新建任务以确认工具")
    config = configured_server(job, member)
    if (frozen.get("server_id") != str(member["mcp_server_id"]) or frozen.get("tool") != member["mcp_tool"]
            or frozen.get("prompt_field") != member.get("mcp_prompt_field", "prompt")
            or frozen.get("server_revision") != revision(config)
            or frozen.get("schema_sha256") != revision(frozen.get("input_schema"))
            or ("output_schema_sha256" in frozen and frozen["output_schema_sha256"] != revision(frozen.get("output_schema")))):
        raise ValueError("MCP Agent 连接或能力已变化，请确认后新建任务")
    skills = freeze_native_skills(member, skill_snapshot)
    guidance = skill_prompt(list(skills), snapshot=skills)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) + len(guidance) > 22000:
        raise ValueError("MCP Agent 交接与 Skills 必须完整且合计不超过 22000 字符，未截断或发送")
    actual_prompt = prompt + guidance
    validate_schema(frozen["input_schema"], frozen["prompt_field"], actual_prompt)
    identifier = str(uuid.uuid5(uuid.UUID(checked_id(parent_id)), "mcp-agent:" + member["id"]))
    expected = {"id": identifier, "parent_job_id": parent_id, "project_id": project_id, "room_id": room_id,
                "member": member, "server_id": frozen["server_id"], "tool": frozen["tool"],
                "schema_sha256": frozen["schema_sha256"], "prompt_sha256": hashlib.sha256(actual_prompt.encode()).hexdigest()}
    if "output_schema_sha256" in frozen:
        expected["output_schema_sha256"] = frozen["output_schema_sha256"]
    async with _step_lock((parent_id, member["id"])):
        _scope(job, member)
        path = STORE.directory / (identifier + ".json")
        if STORE.directory.is_symlink() or path.is_symlink():
            raise ValueError("MCP Agent 执行记录路径无效")
        if path.exists():
            record = json.loads(path.read_text())
            if any(record.get(key) != value for key, value in expected.items()):
                raise ValueError("已有 MCP Agent 执行记录与交接不一致，不会重复派发")
            if (record.get('status') not in {'dispatching', 'uncertain', 'error', 'complete'}
                    or record.get('dispatch_count') not in {0, 1}
                    or (record.get('status') == 'complete' and (not isinstance(record.get('output'), str)
                        or hashlib.sha256(record['output'].encode()).hexdigest() != record.get('output_sha256')))):
                raise ValueError("MCP Agent 已有成果校验失败；保留原记录，不会重复派发")
            return public_record(record)
        if observe_only:
            raise ValueError("MCP 原始执行记录不存在；自动恢复不会创建新的工具调用")
        record = {**expected, "schema_version": 1, "created_at": now(), "status": "preflight", "dispatch_count": 0,
                  "skill_application": {"mode": "role_instructions", "skills": [
                      {key: item.get(key) for key in ["id", "name", "revision", "sha256"]} for item in skills.values()],
                      "native_plugin_installed": False} if skills else None}
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS), agent_session(config) as session:
                tools = await list_all_tools(session)
                tool = next((item for item in tools if item.name == frozen["tool"]), None)
                if tool is None or revision(tool.inputSchema) != frozen["schema_sha256"]:
                    raise ValueError("MCP Agent 工具结构已变化，任务未发送")
                # Old completed records were returned above without replay. Old
                # undispatched snapshots cannot authorize a newly declared schema.
                if (("output_schema_sha256" not in frozen and tool.outputSchema is not None)
                        or ("output_schema_sha256" in frozen
                            and revision(tool.outputSchema) != frozen["output_schema_sha256"])):
                    raise ValueError("MCP Agent 输出结构已变化，请新建任务；任务未发送")
                validate_output_schema(tool.outputSchema)
                _scope(job, member)
                if revision(configured_server(job, member)) != frozen["server_revision"]:
                    raise ValueError("MCP Agent 连接已变化，任务未发送")
                record.update(status="dispatching", dispatch_count=1, sent_at=now())
                STORE.save(record)
                result = await session.call_tool(frozen["tool"], {frozen["prompt_field"]: actual_prompt})
                _scope(job, member)
                if not result.isError:
                    validate_output_schema(tool.outputSchema, result)
                output, warning = actual_text(result)
                record.update(status="complete", output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest(), warning=warning,
                              result_provenance=result_provenance(result.model_dump(mode="json")))
                STORE.save(record)
        except asyncio.CancelledError:
            record.update(status="uncertain" if record["dispatch_count"] else "error", error="MCP 本地等待已停止；远端执行状态请核对原服务，不会自动重发")
            STORE.save(record)
            raise
        except Exception as error:
            # Do not expose connection URLs, transport errors or server secrets.
            known = str(error) if isinstance(error, ValueError) and str(error).startswith("MCP") else None
            if record["status"] != "complete":
                record.update(status="uncertain" if record["dispatch_count"] and not known else "error",
                              error=known or "MCP Agent 未返回可确认的完整结果，请检查原服务；没有自动重发")
                STORE.save(record)
        record["updated_at"] = now()
        STORE.save(record)
        return public_record(record)
