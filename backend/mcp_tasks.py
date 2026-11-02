"""Tasks extension 2026-07-28 over the existing official MCP Python transport.

Use server/discover, explicit per-request capabilities, durable handles, and
read-only polling. The deprecated 2025 experimental wire format is not guessed.
Protocol: https://tasks.extensions.modelcontextprotocol.io/specification/2026-07-28/tasks
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Literal

from mcp.shared.exceptions import McpError
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import Result
from pydantic import BaseModel, ConfigDict, Field, model_validator

EXTENSION = "io.modelcontextprotocol/tasks"
PROTOCOL = "2026-07-28"
TERMINAL = {"completed", "failed", "cancelled"}


class ExtensionRequest(BaseModel):
    # MCP 1.x's generic RequestParams silently discards extension fields. Keep
    # the typed wire envelope here while the SDK owns transport and correlation.
    method: str
    params: dict


class TaskState(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    taskId: str = Field(min_length=1, max_length=512, pattern=r"^[\x21-\x7e]+$")
    status: Literal["working", "input_required", "completed", "failed", "cancelled"]
    statusMessage: str | None = Field(default=None, max_length=4000)
    createdAt: str
    lastUpdatedAt: str
    ttlMs: int | None
    pollIntervalMs: int = Field(default=5000, ge=0)
    result: dict | None = None
    error: dict | None = None
    inputRequests: dict | None = None

    @model_validator(mode="after")
    def valid_state(self, info):
        for raw in [self.createdAt, self.lastUpdatedAt]:
            if datetime.fromisoformat(raw.replace('Z','+00:00')).tzinfo is None:
                raise ValueError("MCP 任务时间缺少时区")
        if self.ttlMs is not None and self.ttlMs < 0:
            raise ValueError("MCP 任务有效期无效")
        if self.inputRequests is not None and (len(self.inputRequests) > 20 or any(
                not isinstance(value, dict) or not isinstance(value.get("method"), str)
                or not isinstance(value.get("params", {}), dict)
                for value in self.inputRequests.values())):
            raise ValueError("MCP 任务输入请求格式无效")
        if not (info.context or {}).get("seed") and (self.status == "input_required" and not self.inputRequests
                or self.status == "completed" and self.result is None
                or self.status == "failed" and self.error is None):
            raise ValueError("MCP 任务缺少状态对应的结果")
        return self


async def request(session, method, params=None):
    params = dict(params or {})
    if method.startswith("tasks/") or method == "server/discover":
        params["_meta"] = {"io.modelcontextprotocol/clientCapabilities":{"extensions":{EXTENSION:{}}}}
    result = await session.send_request(ExtensionRequest(method=method, params=params), Result,
                                        request_read_timeout_seconds=timedelta(seconds=20))
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


async def discover(session):
    if not hasattr(session, "send_request"):
        return None
    try:
        result = await request(session, "server/discover")
    except McpError as error:
        if error.error.code in {-32601, -32602}:
            return None  # Existing MCP 1.x synchronous servers remain usable.
        raise
    capabilities = result.get("capabilities") or {}
    return PROTOCOL if EXTENSION in (capabilities.get("extensions") or {}) else None


async def call(session, name, arguments):
    return await request(session, "tools/call", {"name":name, "arguments":arguments,
        "_meta":{"io.modelcontextprotocol/clientCapabilities":{"extensions":{EXTENSION:{}}}}})


def task_state(value, task_id=None, *, seed=False):
    # Some examples in the spec show a nested seed. Only the normative flat
    # 2026 shape is accepted; ambiguous handles must never authorize polling.
    task = TaskState.model_validate(value, context={"seed":seed}).model_dump(exclude_none=True)
    if task_id is not None and task["taskId"] != task_id:
        raise ValueError("MCP 返回了不同的任务标识，原任务不会重发")
    from .attachments import secret_text
    if len(json.dumps(value)) > 2_000_000 or secret_text(json.dumps(value, ensure_ascii=False)):
        raise ValueError("MCP 任务内容超过上限或包含疑似凭据，未传入群聊")
    return task


def poll_due(record, *, now=None):
    instant = now or datetime.now(timezone.utc)
    task = record.get("remote_task") or {}
    if record.get("status") != "observing" or task.get("status") not in {"working", "input_required"}:
        return False
    if task["status"] == "input_required" and not record.get("seed_pending") and not record.get("input_responses"):
        return False
    return not record.get("next_poll_at") or instant >= datetime.fromisoformat(record["next_poll_at"])


def next_poll(task):
    # Never poll sooner than the service requests. A zero interval is given a
    # local one-second floor; very long intervals remain intact across restart.
    seconds = max(1, task.get("pollIntervalMs", 5000) / 1000)
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def expired(task):
    ttl = task.get("ttlMs")
    if ttl is None:
        return False
    created = datetime.fromisoformat(task["createdAt"].replace('Z','+00:00'))
    return (datetime.now(timezone.utc) - created).total_seconds() * 1000 >= ttl


async def routing_headers(request):
    if request.method != "POST":
        return
    body = json.loads(request.content)
    method, params = body.get("method"), body.get("params") or {}
    if method:
        request.headers["Mcp-Method"] = method
    if method in {"tasks/get", "tasks/update", "tasks/cancel"}:
        request.headers["Mcp-Name"] = params["taskId"]
    elif method == "tools/call":
        request.headers["Mcp-Name"] = params["name"]


def http_client_factory(headers=None, timeout=None, auth=None):
    client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
    client.event_hooks["request"].append(routing_headers)
    return client
