"""Phone boundary regression tests: no network configuration or real credentials."""
import asyncio
import json
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import remote_access as remote

ORIGIN = "https://modeldesk.example.ts.net:8443"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class RemoteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 100000
        self.store = remote.RemoteStore(self.temp.name, clock=lambda: self.now)
        self.store.configure(ORIGIN)
    def tearDown(self):
        self.temp.cleanup()

    def test_pairing_one_use_rotates_and_stores_hashes_only(self):
        old = self.store.create_pairing("old")
        new = self.store.create_pairing("手机")
        with self.assertRaises(HTTPException):
            self.store.pair(old["code"])
        token, session = self.store.pair(new["code"])
        self.assertEqual(self.store.session(token)["id"], session["id"])
        with self.assertRaises(HTTPException):
            self.store.pair(new["code"])
        data = (Path(self.temp.name) / "auth.sqlite3").read_bytes()
        self.assertNotIn(token.encode(), data)
        self.assertNotIn(new["code"].replace("-", "").encode(), data)
        self.assertEqual((Path(self.temp.name) / "auth.sqlite3").stat().st_mode & 0o777, 0o600)


    def test_ios_smart_dashes_and_pasted_whitespace_pair_once(self):
        for separator in ["–", "—", "‑", "−", " ", "\u00a0"]:
            code = self.store.create_pairing("iPhone")["code"]
            entered = "\t" + code.lower().replace("-", separator) + "\n"
            token, _ = self.store.pair(entered)
            self.assertIsNotNone(self.store.session(token))
            with self.assertRaises(HTTPException):
                self.store.pair(code)
        code = self.store.create_pairing("iPhone")["code"]
        for invalid in [code + "0", code[:-1], "G" + code[1:]]:
            with self.assertRaises(HTTPException) as failure:
                self.store.pair(invalid)
            self.assertEqual(failure.exception.status_code, 401)

    def test_pairing_expiry_rate_limit_and_session_expiry(self):
        code = self.store.create_pairing("phone")["code"]
        self.now += 301
        for _ in range(10):
            with self.assertRaises(HTTPException) as failure:
                self.store.pair(code)
            self.assertEqual(failure.exception.status_code, 401)
        with self.assertRaises(HTTPException) as failure:
            self.store.pair(code)
        self.assertEqual(failure.exception.status_code, 429)
        self.now += 301
        code = self.store.create_pairing("phone")["code"]
        token, _ = self.store.pair(code)
        self.now += 43201
        self.assertIsNone(self.store.session(token))

    def test_revoke_survives_other_store_instance_and_origin_change(self):
        token, session = self.store.pair(self.store.create_pairing("phone")["code"])
        another = remote.RemoteStore(self.temp.name, clock=lambda: self.now)
        another.revoke(session["id"])
        self.assertIsNone(self.store.session(token))
        token, _ = self.store.pair(self.store.create_pairing("phone")["code"])
        self.store.configure("https://new.example.ts.net:8443")
        self.assertIsNone(self.store.session(token))

    def test_origin_validation_rejects_public_http_url_credentials_and_paths(self):
        for value in ["https://example.com", "http://a.ts.net", "https://a.ts.net/path", "https://user@a.ts.net", "https://a.ts.net:8001"]:
            with self.assertRaises(ValueError):
                remote.checked_origin(value)

    def test_state_symlink_directory_is_rejected(self):
        link = Path(self.temp.name) / "link"
        target = Path(self.temp.name) / "target"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            remote.RemoteStore(link).configure(ORIGIN)
        self.assertFalse((target / "auth.sqlite3").exists())


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 100000
        self.store = remote.RemoteStore(self.root / "state", clock=lambda: self.now)
        self.store.configure(ORIGIN)
        self.frontend = self.root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text("<html>Phone shell</html>")
        (self.root / "private.txt").write_text("private")
        (self.frontend / "escape.txt").symlink_to(self.root / "private.txt")
        self.calls = []
        async def upstream(request):
            self.calls.append(request)
            if request.url.path.endswith("/events"):
                return httpx.Response(200, stream=Chunks([b"event: update\ndata: 1\n\n", b"event: end\ndata: 2\n\n"]), headers={"Content-Type": "text/event-stream"})
            return httpx.Response(200, stream=Chunks([b'{"ok":true}']), headers={"Content-Type": "application/json", "Set-Cookie": "bad=1", "Access-Control-Allow-Origin": "*"})
        self.app = remote.create_gateway(self.store, frontend=self.frontend, upstream_transport=httpx.MockTransport(upstream))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp.cleanup()

    async def pair(self):
        code = self.store.create_pairing("phone")["code"]
        response = await self.client.post("/remote/pair", json={"code": code}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def test_unauthenticated_shell_allowed_api_denied_and_secure_cookie(self):
        self.assertEqual((await self.client.get("/")).status_code, 200)
        self.assertFalse((await self.client.get("/remote/session")).json()["authenticated"])
        self.assertEqual((await self.client.get("/api/projects")).status_code, 401)
        self.assertFalse(self.calls)
        cookie = (await self.pair()).headers["set-cookie"]
        for flag in ["HttpOnly", "Secure", "SameSite=strict", "Path=/"]:
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertTrue((await self.client.get("/remote/session")).json()["authenticated"])

    async def test_csrf_pairing_and_mutation_origin_rebinding_forwarded_headers(self):
        code = self.store.create_pairing("phone")["code"]
        for headers in [{}, {"Origin": "https://evil.test"}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}]:
            self.assertEqual((await self.client.post("/remote/pair", json={"code": code}, headers=headers)).status_code, 403)
        await self.pair()
        self.assertEqual((await self.client.post("/api/groups", json={})).status_code, 403)
        self.assertEqual((await self.client.get("/api/projects", headers={"Host": "evil.test", "X-Forwarded-Host": "modeldesk.example.ts.net:8443"})).status_code, 403)
        self.assertFalse(self.calls)

    async def test_proxy_strips_auth_identity_and_preserves_remote_origin_by_default(self):
        await self.pair()
        response = await self.client.post("/api/groups?project_id=abc", content=b"{}", headers={
            "Origin": ORIGIN, "Content-Type": "application/json", "Authorization": "secret", "X-Forwarded-For": "127.0.0.1",
            "X-ModelDesk-Remote": "spoof", "Tailscale-User-Login": "admin"})
        self.assertEqual(response.status_code, 200)
        call = self.calls[-1]
        self.assertEqual(str(call.url), "http://127.0.0.1:8001/api/groups?project_id=abc")
        self.assertEqual(call.headers["origin"], ORIGIN)
        self.assertEqual(call.headers["x-modeldesk-remote"], "phone")
        for key in ["authorization", "cookie", "x-forwarded-for", "tailscale-user-login"]:
            self.assertNotIn(key, call.headers)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("access-control-allow-origin", response.headers)

    async def test_native_and_admin_routes_never_forward_and_group_approval_is_explicit(self):
        await self.pair()
        headers = {"Origin": ORIGIN}
        for path in ["/api/remote/pairings", "/api/remote/start", "/api/muse/tasks", "/api/platform/originals/chathub/start", "/api/workbench/projects/a/folders"]:
            self.assertEqual((await self.client.post(path, json={}, headers=headers)).status_code, 403, path)
        self.assertFalse(self.calls)
        await self.client.post("/api/group-approvals/" + str(uuid.uuid4()) + "/decision", json={}, headers=headers)
        self.assertEqual(self.calls[-1].headers["origin"], "http://localhost:5173")

    async def test_paths_upload_limit_and_static_symlink(self):
        await self.pair()
        for path in ["/api/%2e%2e/remote/start", "/api/%2Fremote/start", "/api//remote/status"]:
            self.assertEqual((await self.client.get(path)).status_code, 400, path)
        self.assertEqual((await self.client.get("/escape.txt")).status_code, 404)
        response = await self.client.post("/api/projects/a/assets", content=b"x", headers={"Origin": ORIGIN, "Content-Length": str(remote.MAX_BODY + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.calls)

    async def test_streaming_events_range_download_and_revocation(self):
        await self.pair()
        response = await self.client.get("/api/workbench/jobs/one/events", headers={"Last-Event-ID": "22"})
        self.assertEqual(response.headers["content-type"], "text/event-stream")
        self.assertIn("event: end", response.text)
        self.assertEqual(self.calls[-1].headers["last-event-id"], "22")
        self.store.revoke()
        self.assertEqual((await self.client.get("/api/projects")).status_code, 401)
        self.assertFalse((await self.client.get("/remote/session")).json()["authenticated"])

    async def test_logout_and_session_expiry(self):
        await self.pair()
        self.assertEqual((await self.client.post("/remote/logout", headers={"Origin": ORIGIN})).status_code, 200)
        self.assertEqual((await self.client.get("/api/projects")).status_code, 401)
        await self.pair()
        self.now += 43201
        self.assertEqual((await self.client.get("/api/projects")).status_code, 401)

    async def test_multipart_upload_and_known_route_only_origin_mapping(self):
        await self.pair()
        response = await self.client.post("/api/workbench/projects/project/assets", files={"file": ("photo.png", b"image-content", "image/png")}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"image-content", self.calls[-1].content)
        self.assertIn("multipart/form-data; boundary=", self.calls[-1].headers["content-type"])
        for path in ["/api/group-tasks/a/decision", "/api/group-approvals/" + str(uuid.uuid4()) + "/cancel", "/api/groups"]:
            await self.client.post(path, json={}, headers={"Origin": ORIGIN})
            self.assertEqual(self.calls[-1].headers["origin"], ORIGIN)

    async def test_revoke_during_stream_stops_further_response(self):
        await self.pair()
        store = self.store
        class RevokedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                store.revoke()
                yield b"must-not-arrive"
        async def upstream(_):
            return httpx.Response(200, stream=RevokedStream())
        await self.app.state.upstream.aclose()
        self.app.state.upstream = httpx.AsyncClient(base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(upstream))
        try:
            response = await self.client.get("/api/workbench/jobs/test/events")
            self.assertEqual(response.content, b"first")
        finally:
            await self.app.state.upstream.aclose()

    async def test_native_sdk_execution_is_blocked_before_forwarding(self):
        await self.pair()
        for headers in [{"Origin": ORIGIN}, {"Origin": ORIGIN, "Content-Type": "application/json"}]:
            response = await self.client.post("/api/workbench/jobs", content=json.dumps({"mode": "sdk_script", "approved": True, "prompt": "print(1)"}), headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.calls)
        response = await self.client.post("/api/workbench/jobs", json={"mode": "chat", "prompt": "hello"}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        response = await self.client.get("/api/workbench/jobs/id/export")
        self.assertEqual(response.status_code, 200)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_serve_slot_requires_dedicated_https_hostname(self):
        owned = {"TCP": {"8443": {"HTTPS": True}}, "Web": {
            "modeldesk.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}}}}
        self.assertEqual(remote.RemoteController.slot(owned, ORIGIN), "owned")
        owned["AllowFunnel"] = {"modeldesk.example.ts.net:8443": True}
        self.assertEqual(remote.RemoteController.slot(owned, ORIGIN), "conflict")
        self.assertEqual(remote.RemoteController.slot({"TCP": {"8443": {"TCPForward": "localhost:1"}}}, ORIGIN), "conflict")
        self.assertEqual(remote.RemoteController.slot({"TCP": {"443": {"HTTPS": True}}}, ORIGIN), "conflict")
        self.assertEqual(remote.RemoteController.slot({"TCP": {"443": {"TCPForward": "localhost:9000"}}}, ORIGIN), "conflict")
        self.assertEqual(remote.RemoteController.slot({"Foreground": {"other-session": {"TCP": {"443": {"HTTPS": True}}}}}, ORIGIN), "conflict")
        self.assertEqual(remote.RemoteController.slot({}, ORIGIN), "absent")

    async def test_start_and_recover_revoke_if_cookie_hostname_is_shared(self):
        conflict = {"TCP": {"443": {"HTTPS": True}}, "Web": {
            "modeldesk.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}}}}}
        with tempfile.TemporaryDirectory() as temp:
            auth = remote.RemoteStore(temp); auth.configure(ORIGIN)
            controller = remote.RemoteController(auth)
            for action in ["start", "recover"]:
                token, _ = auth.pair(auth.create_pairing("phone")["code"])
                controller.set_enabled(True)
                with patch.object(remote, "tailscale_status", return_value={"state": "Running", "dns_name": "modeldesk.example.ts.net"}), patch.object(controller, "serve_config", new=AsyncMock(return_value=conflict)), patch.object(controller, "command", new=AsyncMock()) as command, patch.object(controller, "_launch", new=AsyncMock()) as launch:
                    if action == "start":
                        with self.assertRaises(HTTPException): await controller.start()
                    else:
                        await controller.recover()
                    command.assert_not_called()
                    launch.assert_not_called()
                self.assertIsNone(auth.session(token))
                self.assertFalse(controller.enabled())

    async def test_later_shared_https_revokes_cookie_before_gateway_accepts_it(self):
        with tempfile.TemporaryDirectory() as temp:
            auth = remote.RemoteStore(temp); auth.configure(ORIGIN)
            token, _ = auth.pair(auth.create_pairing("phone")["code"])
            controller = remote.RemoteController(auth)
            controller.set_enabled(True)
            app = remote.create_gateway(auth, network_guard=controller.authorize_hostname)
            with patch.object(controller, "serve_config", new=AsyncMock(return_value={"TCP": {"443": {"HTTPS": True}}})):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Cookie": remote.COOKIE + "=" + token}) as client:
                    response = await client.get("/remote/session")
                    self.assertEqual(response.status_code, 403)
            self.assertIsNone(auth.session(token))
            self.assertFalse(controller.enabled())

    async def test_start_stopped_tailscale_never_changes_network(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = remote.RemoteController(remote.RemoteStore(temp))
            with patch.object(remote, "tailscale_status", return_value={"state": "Stopped", "dns_name": "a.ts.net"}), patch.object(controller, "command", new=AsyncMock()) as command:
                with self.assertRaises(HTTPException) as failure:
                    await controller.start()
                self.assertEqual(failure.exception.status_code, 409)
                command.assert_not_called()


    async def test_real_loopback_gateway_lifecycle_without_serve_changes(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp, patch.object(remote, "GATEWAY_PORT", port):
            auth = remote.RemoteStore(temp)
            auth.configure(ORIGIN)
            controller = remote.RemoteController(auth)
            try:
                await controller._launch()
                self.assertTrue(controller.running)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                    info = await client.get("/remote/info", headers={"Host": "modeldesk.example.ts.net:8443"})
                    self.assertTrue(info.json()["remote"])
                    self.assertEqual((await client.get("/api/workbench/projects", headers={"Host": "modeldesk.example.ts.net:8443"})).status_code, 401)
                    self.assertEqual((await client.get("/remote/info")).status_code, 403)
            finally:
                await controller.shutdown()
            self.assertFalse(controller.running)
            self.assertFalse(remote.gateway_running())

    async def test_stop_revokes_without_overwriting_changed_serve_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            auth = remote.RemoteStore(temp)
            auth.configure(ORIGIN)
            token, _ = auth.pair(auth.create_pairing("phone")["code"])
            controller = remote.RemoteController(auth)
            controller.set_enabled(True)
            with patch.object(controller, "serve_config", new=AsyncMock(return_value={"TCP": {"8443": {"TCPForward": "other-service"}}})), patch.object(controller, "command", new=AsyncMock()) as command:
                result = await controller.stop()
                self.assertFalse(result["route_removed"])
                command.assert_not_called()
            self.assertIsNone(auth.session(token))
            self.assertFalse(controller.enabled())

    async def test_local_management_rejects_remote_and_untrusted_origin(self):
        app = FastAPI()
        app.include_router(remote.router)
        for host in ["192.168.1.3", "100.100.1.1"]:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 123)), base_url="http://127.0.0.1:8001") as client:
                response = await client.post("/api/remote/revoke-all", headers={"Origin": "http://localhost:5173"})
                self.assertEqual(response.status_code, 403)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://127.0.0.1:8001") as client:
            response = await client.post("/api/remote/revoke-all", headers={"Origin": "https://evil.test"})
            self.assertEqual(response.status_code, 403)
