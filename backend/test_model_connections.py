"""Credential, routing, and native bridge regression tests without real keys."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend import model_connections as connections
from backend import openrouter, workbench, remote_access

ORIGIN = "http://localhost:5173"
KEY = "private-fixture-provider-credential"


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = connections.ConnectionStore(self.temporary.name)
        self.patches = [patch.object(connections, "store", self.store), patch.object(workbench, "connection_store", self.store)]
        for context in self.patches:
            context.start()
        app = FastAPI()
        app.include_router(connections.router)
        app.include_router(connections.runtime_router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url="http://127.0.0.1:8001", headers={"Origin": ORIGIN})

    async def asyncTearDown(self):
        await self.client.aclose()
        connections._worker_grants.clear()
        for context in reversed(self.patches):
            context.stop()
        self.temporary.cleanup()

    def body(self, **changes):
        return {"name": "Grok 测试连接", "provider": "xai", "base_url": "https://api.x.ai/v1", "api_key": KEY, "models": [{"id": "grok-test", "label": "Grok 测试"}], **changes}

    def add(self, **changes):
        return self.store.save(connections.ConnectionInput(**self.body(**changes)))

    def upstream(self, handler, observed=None):
        original = httpx.AsyncClient
        def client(**kwargs):
            if observed is not None:
                observed.append(kwargs)
            return original(transport=httpx.MockTransport(handler), **kwargs)
        return patch.object(connections.httpx, "AsyncClient", side_effect=client)

    async def test_crud_secrecy_permissions_replacement_and_delete(self):
        created = await self.client.post("/api/model-connections", json=self.body())
        self.assertEqual(created.status_code, 201)
        item = created.json()
        self.assertTrue(item["has_api_key"])
        self.assertNotIn(KEY, created.text)
        path = Path(self.temporary.name) / "connections.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(KEY, path.read_text())
        listing = await self.client.get("/api/model-connections")
        self.assertNotIn(KEY, listing.text)
        changed = await self.client.put("/api/model-connections/" + item["id"], json=self.body(name="renamed", api_key=""))
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["models"][0]["alias"], item["models"][0]["alias"])
        self.assertEqual(self.store.get_private(item["id"])["api_key"], KEY)
        denied = await self.client.put("/api/model-connections/" + item["id"], json=self.body(provider="openai_compatible", base_url="https://another.example/v1", api_key=""))
        self.assertEqual(denied.status_code, 422)
        self.assertNotIn(KEY, denied.text)
        removed = await self.client.delete("/api/model-connections/" + item["id"])
        self.assertEqual(removed.status_code, 200)
        self.assertNotIn(KEY, path.read_text())
        with self.assertRaisesRegex(ValueError, "删除"):
            connections.request_options({"model": item["models"][0]["alias"]})

    async def test_validation_never_echoes_key_or_bad_json(self):
        for payload in [self.body(api_key=KEY + "\n"), self.body(base_url="https://user:" + KEY + "@example.com/v1"), self.body(models=[{"id": KEY + " invalid"}])]:
            response = await self.client.post("/api/model-connections", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(KEY, response.text)
            self.assertNotIn("input", response.json())
        response = await self.client.post("/api/model-connections", content=KEY)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(KEY, response.text)

    async def test_origin_host_and_phone_admin_boundary(self):
        for origin in ["https://evil.example", "null", ""]:
            response = await self.client.post("/api/model-connections", json=self.body(), headers={"origin": origin})
            self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/model-connections", json=self.body(), headers={"host": "rebind.evil.example"})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/model-connections", json=self.body(), headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        for route in ["/api/model-connections", "/api/model-connections/id/test", "/api/model-runtime/v1/chat/completions"]:
            for method in ["GET", "POST", "PUT", "DELETE"]:
                self.assertFalse(remote_access.api_allowed(route, method))

    async def test_endpoint_constraints_and_explicit_local_opt_in(self):
        for value in ["http://api.example/v1", "https://api.example/v1?key=secret", "https://api.example/v1#fragment", "https://169.254.169.254/v1", "https://127.0.0.2/v1", "http://localhost:8080/v1", "https://api.example/../v1"]:
            with self.assertRaises(ValueError):
                connections.checked_endpoint(value)
        with self.assertRaises(ValueError):
            connections.checked_endpoint("http://localhost:8001", True)
        self.assertEqual(connections.checked_endpoint("http://127.0.0.1:11434/v1/", True), "http://127.0.0.1:11434/v1")
        local = self.add(provider="openai_compatible", base_url="http://127.0.0.1:11434/v1", allow_local=True, api_key="")
        self.assertFalse(local["has_api_key"])

    async def test_model_aliases_route_distinct_credentials_without_mutation(self):
        first = self.add()
        second = self.add(name="Other", provider="openai_compatible", base_url="https://second.example/v1", api_key="other-fixture-key")
        original = {"model": first["models"][0]["alias"], "messages": [{"role": "user", "content": "task"}], "tools": [{"type": "function", "function": {"name": "add"}}], "stream": True}
        options = connections.request_options(original)
        self.assertEqual(options["json"]["model"], "grok-test")
        self.assertTrue(original["model"].startswith("connection:"))
        self.assertEqual(options["json"]["messages"], original["messages"])
        self.assertEqual(options["json"]["tools"], original["tools"])
        self.assertEqual(options["headers"]["Authorization"], "Bearer " + KEY)
        other = connections.request_options({**original, "model": second["models"][0]["alias"]})
        self.assertEqual(other["url"], "https://second.example/v1/chat/completions")
        self.assertEqual(other["headers"]["Authorization"], "Bearer other-fixture-key")
        existing = connections.request_options({"model": "gemini-3-flash"}, "http://127.0.0.1:8317/v1/chat/completions", {"Authorization": "Bearer existing"})
        self.assertEqual(existing["headers"]["Authorization"], "Bearer existing")
        self.store.save(connections.ConnectionInput(**self.body(enabled=False)), first["id"])
        with self.assertRaisesRegex(ValueError, "停用"):
            connections.request_options(original)

    async def test_catalog_api_models_work_when_subscription_gateway_offline(self):
        item = self.add()
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(side_effect=httpx.ConnectError("offline"))):
            self.assertEqual(await workbench.model_catalog(), [item["models"][0]["alias"]])
            status = await workbench.status()
        self.assertFalse(status["gateway"]["online"])
        self.assertTrue(status["tools"]["chat"]["available"])
        self.assertEqual(status["models"][0]["label"], "Grok 测试")
        self.assertNotIn(KEY, json.dumps(status))

    async def test_damaged_private_store_does_not_break_subscription_status_or_echo_credentials(self):
        self.add()
        (Path(self.temporary.name) / "connections.json").write_text('{"invalid": "' + KEY)
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=["gemini-3-flash"])):
            status = await workbench.status()
            self.assertEqual(await workbench.model_catalog(), ["gemini-3-flash"])
        self.assertTrue(status["gateway"]["online"])
        self.assertIn("connections_error", status)
        response = await self.client.get("/api/model-connections")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(KEY, response.text)

    async def test_discover_does_not_test_or_save_and_uses_language_endpoint(self):
        item = self.add(models=[])
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"models": [{"id": "grok-discovered"}, {"id": "bad id"}]})
        with self.upstream(handler):
            response = await self.client.post(f"/api/model-connections/{item['id']}/discover")
        self.assertEqual(response.json(), {"models": [{"id": "grok-discovered", "label": ""}]})
        self.assertEqual(str(requests[0].url), "https://api.x.ai/v1/language-models")
        self.assertEqual(requests[0].headers["authorization"], "Bearer " + KEY)
        self.assertEqual(self.store.list()[0]["models"], [])
        self.assertIsNone(self.store.list()[0]["last_test"])

    async def test_actual_probe_sanitizes_upstream_errors_and_does_not_follow_redirects(self):
        item = self.add(models=[{"id": "grok-test"}, {"id": "untested"}])
        requests, options = [], []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://evil.example/"}, json={"error": KEY})
        with self.upstream(handler, options):
            response = await self.client.post(f"/api/model-connections/{item['id']}/test", json={"model": "grok-test"})
        self.assertFalse(response.json()["ok"])
        self.assertNotIn(KEY, response.text)
        self.assertEqual(len(requests), 1)
        self.assertFalse(options[0]["follow_redirects"])
        self.assertEqual(json.loads(requests[0].content)["max_tokens"], 16)
        def success(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        with self.upstream(success):
            response = await self.client.post(f"/api/model-connections/{item['id']}/test", json={"model": "grok-test"})
        self.assertTrue(response.json()["ok"])
        self.assertEqual([model["verified"] for model in connections.model_catalog()], [True, False])

    async def test_streaming_chat_and_council_reach_real_routed_mock_transport(self):
        item = self.add()
        alias = item["models"][0]["alias"]
        observed = []
        def handler(request):
            body = json.loads(request.content)
            observed.append((request, body))
            if body.get("stream"):
                return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Actual streamed answer"}}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={"choices": [{"message": {"content": "Actual council answer"}, "finish_reason": "stop"}]})
        with tempfile.TemporaryDirectory() as directory:
            manager = workbench.JobManager(directory)
            job = {"id": str(uuid.uuid4()), "models": [alias], "prompt": "task", "events": [], "tools": [], "_conversation_history": {"turns": []}}
            manager.jobs[job["id"]] = job
            manager.wakeups[job["id"]] = asyncio.Event()
            with self.upstream(handler):
                result = await manager.chat(job)
                council = await openrouter.query_model(alias, [{"role": "user", "content": "task"}])
            self.assertEqual(result["responses"][0]["text"], "Actual streamed answer")
            self.assertEqual(result["responses"][0]["model"], alias)
            self.assertEqual(council["content"], "Actual council answer")
        self.assertEqual(len(observed), 2)
        for request, body in observed:
            self.assertEqual(str(request.url), "https://api.x.ai/v1/chat/completions")
            self.assertEqual(body["model"], "grok-test")
            self.assertEqual(request.headers["authorization"], "Bearer " + KEY)

    async def test_native_proxy_scope_expiry_revocation_and_upstream_error_secrecy(self):
        item = self.add()
        alias = item["models"][0]["alias"]
        token, environment = connections.grant_worker([alias])
        self.assertNotIn(KEY, json.dumps(environment))
        headers = {"authorization": "Bearer " + token, "origin": ""}
        path = "/api/model-runtime/v1/chat/completions"
        payload = {"model": alias, "messages": [{"role": "user", "content": "task"}]}
        for changes in [{"authorization": "Bearer wrong"}, {"origin": ORIGIN}]:
            response = await self.client.post(path, json=payload, headers={**headers, **changes})
            self.assertEqual(response.status_code, 403)
        response = await self.client.post(path, json={**payload, "model": "not-selected"}, headers=headers)
        self.assertEqual(response.status_code, 403)
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(401, json={"error": "never expose " + KEY})
        with self.upstream(handler):
            response = await self.client.post(path, json=payload, headers=headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(KEY, response.text)
        self.assertEqual(seen[0].headers["authorization"], "Bearer " + KEY)
        self.assertNotIn(token, seen[0].content.decode())
        connections._worker_grants[token]["expires"] = 0
        self.assertEqual((await self.client.post(path, json=payload, headers=headers)).status_code, 403)
        connections.revoke_worker(token)
        self.assertEqual((await self.client.post(path, json=payload, headers=headers)).status_code, 403)

    async def test_native_proxy_preserves_images_tools_stream_and_nonstream(self):
        item = self.add()
        alias = item["models"][0]["alias"]
        token, _ = connections.grant_worker([alias, "gemini-3-flash"])
        headers = {"authorization": "Bearer " + token, "origin": ""}
        parts = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,cGl4ZWxz"}}]
        payload = {"model": alias, "messages": [{"role": "user", "content": parts}], "tools": [{"type": "function", "function": {"name": "tool", "parameters": {"type": "object"}}}], "response_format": {"type": "json_object"}}
        calls = []
        def handler(request):
            body = json.loads(request.content); calls.append(body)
            if body.get("stream"):
                return httpx.Response(200, text='data: {"choices":[{"delta":{"tool_calls":[{"id":"actual-tool"}]}}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={"choices": [{"message": {"content": "actual proxy answer"}}]})
        with self.upstream(handler):
            response = await self.client.post("/api/model-runtime/v1/chat/completions", json=payload, headers=headers)
            streamed = await self.client.post("/api/model-runtime/v1/chat/completions", json={**payload, "stream": True}, headers=headers)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "actual proxy answer")
        self.assertIn("actual-tool", streamed.text)
        self.assertIn("[DONE]", streamed.text)
        for call in calls:
            self.assertEqual(call["messages"][0]["content"], parts)
            self.assertEqual(call["tools"], payload["tools"])
            self.assertEqual(call["response_format"], payload["response_format"])

    async def test_real_http_loopback_provider_receives_rewritten_model_and_key(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append({"body": body, "auth": self.headers.get("Authorization"), "path": self.path})
                output = json.dumps({"choices": [{"message": {"content": "loopback answer"}, "finish_reason": "stop"}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(output))); self.end_headers(); self.wfile.write(output)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            item = self.add(provider="openai_compatible", base_url=f"http://127.0.0.1:{server.server_port}/v1", allow_local=True)
            result = await openrouter.query_model(item["models"][0]["alias"], [{"role": "user", "content": "test"}])
            self.assertEqual(result["content"], "loopback answer")
            self.assertEqual(observed[0]["body"]["model"], "grok-test")
            self.assertEqual(observed[0]["auth"], "Bearer " + KEY)
            self.assertEqual(observed[0]["path"], "/v1/chat/completions")
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close(); thread.join(timeout=3)


class NativeConnectionTests(unittest.TestCase):
    def test_installed_camel_and_crewai_send_scoped_bridge_request_with_images(self):
        root = Path(__file__).resolve().parents[1]
        source = r'''
import json, httpx, os
from unittest.mock import patch
from backend.vision import GATEWAY, GATEWAY_KEY, crew_vision_llm
from backend.workers.workflow import camel_clients
alias="connection:00000000-0000-0000-0000-000000000001:grok-test"
parts=[{"type":"image_url","image_url":{"url":"data:image/png;base64,cGl4ZWxz"}}]
report={"image_requests":0,"requests":[]}; seen=[]
def handle(self, request):
    body=json.loads(request.content); seen.append(body)
    assert str(request.url)=="http://127.0.0.1:8001/api/model-runtime/v1/chat/completions"
    assert request.headers["authorization"]=="Bearer opaque-worker-test-grant"
    assert body["model"]==alias
    assert any(isinstance(m.get("content"),list) and any(p.get("type")=="image_url" for p in m["content"]) for m in body["messages"])
    return httpx.Response(200,request=request,json={"id":"mock","object":"chat.completion","created":1,"model":alias,"choices":[{"index":0,"message":{"role":"assistant","content":"Final Answer: real SDK transport"},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}})
with patch.object(httpx.HTTPTransport, "handle_request", handle):
    if os.environ["TEST_NATIVE_SDK"]=="camel":
        from camel.agents import ChatAgent
        from camel.models import ModelFactory
        from camel.types import ModelPlatformType
        client, async_client=camel_clients(alias,image_parts=parts,vision_report=report)
        model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,model_type=alias,url=GATEWAY,api_key=GATEWAY_KEY,client=client,async_client=async_client,model_config_dict={"max_tokens":64})
        assert ChatAgent("Read the image", model=model).step("look").msgs
    else:
        llm=crew_vision_llm(alias,parts,report,max_tokens=64)
        assert "real SDK transport" in llm.call([{"role":"user","content":"look"}])
assert len(seen)==1 and report["image_requests"]==1
'''
        for sdk in ["camel", "crewai"]:
            python = root.parent / "llm-tools" / (".venv-" + sdk) / "bin/python"
            if not python.exists():
                self.skipTest("native runtime unavailable")
            result = subprocess.run([str(python), "-c", source], cwd=root, env={**os.environ, "MODELDESK_MODEL_BASE": connections.RUNTIME_BASE, "MODELDESK_MODEL_TOKEN": "opaque-worker-test-grant", "TEST_NATIVE_SDK": sdk}, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, sdk + "\n" + result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
