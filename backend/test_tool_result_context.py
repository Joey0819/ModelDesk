"""Structured tool results remain complete and non-retryable calls stay stopped."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from backend import super_platform, workbench
from backend.mcp_protocol import canonical_json


class ToolResultContextTests(unittest.IsolatedAsyncioTestCase):
    async def run_tool_loop(self, name, result=None, error=None, call_arguments=None, expected_invocations=1):
        call_arguments = call_arguments if call_arguments is not None else [{}]
        with tempfile.TemporaryDirectory() as temporary:
            manager = workbench.JobManager(Path(temporary))
            job = {"id": str(uuid.uuid4()), "tools": [name], "_runtime": {"token": "test-token"}, "events": []}
            manager.jobs[job["id"]] = job
            manager.wakeups[job["id"]] = asyncio.Event()
            requests = []
            def respond(request):
                payload = json.loads(request.content)
                requests.append(payload)
                if len(requests) <= len(call_arguments):
                    arguments = json.dumps(call_arguments[len(requests) - 1], ensure_ascii=False)
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-test-{len(requests)}", "type": "function", "function": {"name": name, "arguments": arguments}}]}
                else:
                    message = {"role": "assistant", "content": "已按实际工具成果汇总"}
                return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})
            real_client = httpx.AsyncClient
            invoke = AsyncMock(side_effect=error) if error else AsyncMock(return_value={"tool": name, "result": result})
            with patch.object(workbench.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)), \
                 patch.object(super_platform, "runtime_context", return_value={"tools": {name}}), \
                 patch.object(super_platform, "invoke_tool", invoke):
                answer = await manager.tool_response(job, "model", [{"role": "user", "content": "处理完整结果"}])
            self.assertEqual(invoke.await_count, expected_invocations)
            self.assertFalse(answer["incomplete"])
            return requests[-1]["messages"][-1]["content"], job["events"][-1]

    async def test_mcp_32k_result_is_valid_complete_json_in_history_and_ui_event(self):
        payload = {"content": [{"type": "text", "text": "正文"}], "structuredContent": {"id": "result", "last": "尾部必须保留"}, "isError": False}
        padding = 32000 - len(canonical_json(payload))
        payload["content"][0]["text"] += "中" * padding
        self.assertEqual(len(canonical_json(payload)), 32000)
        history, event = await self.run_tool_loop("mcp_call", payload)
        self.assertGreater(len(history), 20000)
        self.assertEqual(json.loads(history), {"tool": "mcp_call", "result": payload})
        self.assertEqual(json.loads(event["output"]), payload)
        self.assertEqual(len(event["output"]), 32000)
        self.assertIn("尾部必须保留", history)

    async def test_typed_non_retryable_mcp_error_remains_structured(self):
        failure = {"code": "MCP_RESULT_TOO_LARGE", "retryable": False, "message": "MCP 已执行但结果过大；不要重复派发"}
        history, event = await self.run_tool_loop("mcp_call", error=HTTPException(502, failure))
        self.assertEqual(json.loads(history), {"error": failure})
        self.assertEqual(event["error"], failure)
        self.assertNotIn("output", event)

    async def test_identical_mcp_call_is_not_redispatched_after_non_retryable_error(self):
        failure = {"code": "MCP_RESULT_TOO_LARGE", "retryable": False, "message": "结果已产生；不要重复派发"}
        arguments = [
            {"server_id": "server-a", "tool": "create_video", "arguments": {"prompt": "场景", "seconds": 10}},
            {"arguments": {"seconds": 10, "prompt": "场景"}, "tool": "create_video", "server_id": "server-a"},
        ]
        history, event = await self.run_tool_loop("mcp_call", error=HTTPException(502, failure), call_arguments=arguments)
        self.assertEqual(json.loads(history), {"error": failure})
        self.assertEqual(event["error"], failure)

    async def test_unconfirmed_dispatched_mcp_call_is_not_repeated(self):
        failure = {"code": "MCP_RESULT_UNCONFIRMED", "retryable": False, "message": "已派发，成果未确认；不要重复派发"}
        arguments = {"server_id": "a", "tool": "execute_task", "arguments": {"prompt": "Create the result"}}
        history, event = await self.run_tool_loop("mcp_call", error=HTTPException(502, failure),
                                                 call_arguments=[arguments, arguments])
        self.assertEqual(json.loads(history), {"error": failure})
        self.assertEqual(event["error"], failure)

    async def test_non_retryable_guard_is_scoped_to_same_server_tool_and_arguments(self):
        arguments = [
            {"server_id": "a", "tool": "create_video", "arguments": {"prompt": "one"}},
            {"server_id": "b", "tool": "create_video", "arguments": {"prompt": "one"}},
            {"server_id": "a", "tool": "read_video", "arguments": {"prompt": "one"}},
            {"server_id": "a", "tool": "create_video", "arguments": {"prompt": "two"}},
        ]
        failure = {"code": "MCP_RESULT_TOO_LARGE", "retryable": False}
        await self.run_tool_loop("mcp_call", error=HTTPException(502, failure), call_arguments=arguments, expected_invocations=4)

    async def test_retryable_mcp_error_does_not_suppress_authorized_retry(self):
        failure = {"code": "TEMPORARILY_UNAVAILABLE", "retryable": True}
        arguments = {"server_id": "a", "tool": "read_video", "arguments": {"id": "video"}}
        await self.run_tool_loop("mcp_call", error=HTTPException(503, failure), call_arguments=[arguments, arguments], expected_invocations=2)

    async def test_memory_32k_result_preserves_provenance_partial_state_and_limitations(self):
        payload = [{"id": "memory-a", "source": "job-a", "content": "原始正文",
                    "result_state": {"status": "partial", "limitations": ["视频尚未交付"], "job_id": "job-a"}}]
        payload[0]["content"] += "文" * (32000 - len(canonical_json(payload)))
        self.assertEqual(len(canonical_json(payload)), 32000)
        history, event = await self.run_tool_loop("memory_read", payload)
        self.assertEqual(json.loads(history), {"tool": "memory_read", "result": payload})
        self.assertEqual(json.loads(event["output"]), payload)
        self.assertIn("视频尚未交付", history)

    async def test_oversized_memory_returns_structured_error_without_truncated_content(self):
        payload = [{"id": "memory-a", "content": "不得使用的截断前缀" + "文" * 32000,
                    "result_state": {"status": "partial", "limitations": ["图片缺失"]}}]
        history, event = await self.run_tool_loop("memory_read", payload)
        failure = json.loads(history)["error"]
        self.assertEqual(failure["code"], "MEMORY_RESULT_TOO_LARGE")
        self.assertTrue(failure["omitted"])
        self.assertFalse(failure["retryable"])
        self.assertIn("缩小查询范围", failure["message"])
        self.assertEqual(event["error"], failure)
        self.assertNotIn("output", event)
        self.assertNotIn("不得使用的截断前缀", history)

    async def test_non_mcp_tool_retains_existing_bounded_behavior(self):
        payload = {"text": "x" * 25000}
        history, event = await self.run_tool_loop("web_fetch", payload)
        self.assertEqual(len(history), 20000)
        self.assertEqual(len(event["output"]), 16000)
