import json
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from backend import mcp_protocol, super_platform


def tool(name="agent", **kwargs):
    return Tool(name=name, inputSchema={"type": "object", "properties": {"prompt": {"type": "string"}}}, **kwargs)


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_preserve_opaque_cursor_and_do_not_call_tools(self):
        session = SimpleNamespace(list_tools=AsyncMock(side_effect=[
            ListToolsResult(tools=[tool("first")], nextCursor="a opaque / token?"),
            ListToolsResult(tools=[], nextCursor="empty-page-followup"),
            ListToolsResult(tools=[tool("last")]),
        ]), call_tool=AsyncMock())
        self.assertEqual([t.name for t in await mcp_protocol.list_all_tools(session)], ["first", "last"])
        self.assertEqual(session.list_tools.await_args_list[1].kwargs["params"].cursor, "a opaque / token?")
        session.call_tool.assert_not_awaited()

    async def test_duplicate_cyclic_and_oversized_catalogs_are_not_partial_success(self):
        for pages in [
            [ListToolsResult(tools=[tool()], nextCursor="more"), ListToolsResult(tools=[tool()])],
            [ListToolsResult(tools=[], nextCursor="same"), ListToolsResult(tools=[], nextCursor="same")],
        ]:
            with self.assertRaises(ValueError):
                await mcp_protocol.list_all_tools(SimpleNamespace(list_tools=AsyncMock(side_effect=pages)))
        with patch.object(mcp_protocol, "MAX_CATALOG_PAGES", 1), self.assertRaisesRegex(ValueError, "分页过多"):
            await mcp_protocol.list_all_tools(SimpleNamespace(list_tools=AsyncMock(return_value=ListToolsResult(tools=[], nextCursor="more"))))
        with patch.object(mcp_protocol, "MAX_CATALOG_TOOLS", 1), self.assertRaisesRegex(ValueError, "上限"):
            await mcp_protocol.list_all_tools(SimpleNamespace(list_tools=AsyncMock(return_value=ListToolsResult(tools=[tool("a"), tool("b")]))))

    def test_structured_results_kept_and_only_exact_json_mirrors_deduplicated(self):
        result = {"content": [{"type": "text", "text": "Analysis complete"}], "structuredContent": {"rows": [{"count": 42}]}}
        output = mcp_protocol.complete_result_text(result)
        self.assertIn("Analysis complete", output)
        self.assertEqual(json.loads(output.split("：\n", 1)[1]), result["structuredContent"])
        result["content"][0]["text"] = '{ "rows": [{ "count": 42 }] }'
        self.assertEqual(mcp_protocol.complete_result_text(result), result["content"][0]["text"])
        result = {"content": [{"type": "text", "text": '{"ok":1}'}], "structuredContent": {"ok": True}}
        self.assertIn('"ok":true', mcp_protocol.complete_result_text(result))
        result["content"] = []
        self.assertIn('"ok":true', mcp_protocol.complete_result_text(result))

    def test_output_schema_validates_actual_results_and_does_not_resolve_remote_refs(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
        mcp_protocol.validate_output_schema(schema, CallToolResult(content=[], structuredContent={"count": 42}))
        for result in [CallToolResult(content=[]), CallToolResult(content=[], structuredContent={"count": "secret invalid value"})]:
            with self.assertRaisesRegex(ValueError, "未通过") as error:
                mcp_protocol.validate_output_schema(schema, result)
            self.assertNotIn("secret", str(error.exception))
        with self.assertRaisesRegex(ValueError, "未通过"):
            mcp_protocol.validate_output_schema({"$ref": "https://untrusted.invalid/schema"}, CallToolResult(content=[], structuredContent={}))

    async def test_platform_discovery_and_call_use_page_two_and_never_slice_json(self):
        selected = tool("agent", outputSchema={"type": "object", "properties": {"count": {"type": "integer"}}})
        session = SimpleNamespace(list_tools=AsyncMock(), call_tool=AsyncMock(return_value=CallToolResult(
            content=[TextContent(type="text", text="Analysis complete")], structuredContent={"count": 42})))
        def pages():
            return [ListToolsResult(tools=[tool("other")], nextCursor="second"), ListToolsResult(tools=[selected])]
        @asynccontextmanager
        async def connection(config):
            session.list_tools.side_effect = pages()
            yield session
        config = {"enabled": True, "allowed_tools": ["agent"]}
        with patch.object(super_platform.store, "get", return_value=config), patch.object(super_platform, "mcp_session", connection):
            catalog = await super_platform.mcp_discover("test")
            self.assertEqual([t["name"] for t in catalog["tools"]], ["other", "agent"])
            self.assertFalse(catalog["tools"][0]["allowed"])
            self.assertEqual(catalog["tools"][1]["outputSchema"], selected.outputSchema)
            result = await super_platform.mcp_call("test", "agent", {})
            self.assertEqual(json.loads(json.dumps(result))["structuredContent"], {"count": 42})
            self.assertEqual(session.call_tool.await_count, 1)
            session.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="x" * 40000)], structuredContent={"count": 42})
            with self.assertRaises(HTTPException) as error:
                await super_platform.mcp_call("test", "agent", {})
            self.assertEqual(error.exception.status_code, 502)
            self.assertFalse(error.exception.detail['retryable'])
            self.assertIn("未截断", error.exception.detail['message'])
            self.assertEqual(session.call_tool.await_count, 2)

    def test_native_worker_never_recommends_replaying_overflow_or_leaks_server_error_body(self):
        from backend.workers.workflow import runtime_functions
        for code in ["MCP_RESULT_TOO_LARGE", "MCP_RESULT_UNCONFIRMED"]:
            message = {"detail": {"code": code, "message": "private-server-body"}}
            error = urllib.error.HTTPError("http://fixture", 502, "fixture", {}, io.BytesIO(json.dumps(message).encode()))
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=error) as request:
                with self.assertRaisesRegex(RuntimeError, "不要重复派发") as raised:
                    runtime_functions({"runtime": {"token": "private-token"}})["mcp_call"]("server", "agent", {})
                self.assertNotIn("private", str(raised.exception))
                request.assert_called_once()

    async def test_dispatched_schema_transport_and_timeout_errors_are_typed_non_retryable(self):
        selected = tool(outputSchema={"type": "object", "required": ["count"]})
        for failure in [TimeoutError("private provider timeout"), RuntimeError("private SDK schema error"),
                        CallToolResult(content=[TextContent(type="text", text="Done")], structuredContent={})]:
            session = SimpleNamespace(list_tools=AsyncMock(return_value=ListToolsResult(tools=[selected])), call_tool=AsyncMock())
            if isinstance(failure, Exception):
                session.call_tool.side_effect = failure
            else:
                session.call_tool.return_value = failure
            @asynccontextmanager
            async def connection(config):
                yield session
            with self.subTest(failure=type(failure).__name__), patch.object(super_platform.store, "get", return_value={"enabled": True, "allowed_tools": ["agent"]}), patch.object(super_platform, "mcp_session", connection):
                with self.assertRaises(HTTPException) as error:
                    await super_platform.mcp_call("test", "agent", {})
                self.assertEqual(error.exception.status_code, 502)
                self.assertEqual(error.exception.detail["code"], "MCP_RESULT_UNCONFIRMED")
                self.assertFalse(error.exception.detail["retryable"])
                self.assertNotIn("private", str(error.exception.detail))
                session.call_tool.assert_awaited_once()

    async def test_preflight_schema_and_catalog_errors_do_not_claim_dispatch(self):
        selected = tool(outputSchema={"type": "invalid-type"})
        session = SimpleNamespace(list_tools=AsyncMock(return_value=ListToolsResult(tools=[selected])), call_tool=AsyncMock())
        @asynccontextmanager
        async def connection(config):
            yield session
        with patch.object(super_platform.store, "get", return_value={"enabled": True, "allowed_tools": ["agent"]}), patch.object(super_platform, "mcp_session", connection):
            with self.assertRaises(ValueError):
                await super_platform.mcp_call("test", "agent", {})
            session.list_tools.side_effect = TimeoutError("catalog did not arrive")
            with self.assertRaises(TimeoutError):
                await super_platform.mcp_call("test", "agent", {})
            session.call_tool.assert_not_awaited()


