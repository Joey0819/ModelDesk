"""Fleet Phase 1 tests: enrollment, heartbeat, offline derivation, admin boundary.

No real tokens: every token in this file is a fake fixture value.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import fleet, remote_access

ORIGIN = "http://localhost:5173"
FAKE_TOKEN = "fixture-enroll-token-not-real-000"
OTHER_FAKE_TOKEN = "fixture-enroll-token-not-real-111"


def _make_app():
    app = FastAPI()
    app.include_router(fleet.router)
    return app


class FleetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmpdir.name) / "fleet"
        self.now = 100000.0
        self.store = fleet.FleetStore(self.directory, clock=lambda: self.now)
        self._patch = patch.object(fleet, "store", self.store)
        self._patch.start()
        self.app = _make_app()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8001", headers={"Origin": ORIGIN})

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    async def asyncTearDown(self):
        await self.client.aclose()

    def _token_record(self, **changes):
        base = {"token": FAKE_TOKEN, "node_id": "node-alpha", "name": "节点 A",
                "platform": "linux", "hostname": "host-a", "labels": ["gpu"], "agent_version": "0.1.0"}
        base.update(changes)
        return base

    async def _enroll(self, **changes):
        response = await self.client.post("/api/fleet/enroll", json=self._token_record(**changes))
        return response

    async def test_enroll_token_single_use_and_hash_only(self):
        created = await self.client.post("/api/fleet/enroll-tokens")
        self.assertEqual(created.status_code, 200)
        secret = created.json()["token"]
        self.assertTrue(secret)
        payload = self._token_record(token=secret)
        first = await self.client.post("/api/fleet/enroll", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        node_token = first.json()["node_token"]
        self.assertTrue(node_token)
        # The same token cannot enroll a second node.
        second = await self.client.post("/api/fleet/enroll", json=self._token_record(token=secret, node_id="node-beta"))
        self.assertEqual(second.status_code, 401)
        # Storage keeps hashes, never plaintext.
        raw = (self.directory / "nodes.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn(node_token, raw)
        self.assertNotIn(FAKE_TOKEN, raw)

    async def test_bad_or_missing_bearer_is_401(self):
        created = await self.client.post("/api/fleet/enroll-tokens")
        secret = created.json()["token"]
        enrolled = (await self._enroll(token=secret)).json()
        for headers in [{"Authorization": "Bearer wrong-token"},
                        {"Authorization": "Bearer"},
                        {"Authorization": "Basic abc"},
                        {}]:
            response = await self.client.post(
                "/api/fleet/heartbeat", json={"telemetry": {}}, headers=headers)
            self.assertEqual(response.status_code, 401, headers)
        ok = await self.client.post(
            "/api/fleet/heartbeat", json={"telemetry": {"cpu_percent": 12.5, "mem_percent": 40.0}},
            headers={"Authorization": f"Bearer {enrolled['node_token']}"})
        self.assertEqual(ok.status_code, 200)

    async def test_heartbeat_updates_telemetry_and_last_seen(self):
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        enrolled = (await self._enroll(token=secret)).json()
        listing = (await self.client.get("/api/fleet")).json()
        self.assertEqual(listing["nodes"][0]["status"], "never")
        self.assertEqual(listing["summary"]["never"], 1)
        self.now += 10
        heartbeat = await self.client.post(
            "/api/fleet/heartbeat",
            json={"telemetry": {"cpu_percent": 23.5, "mem_percent": 61.0, "load1": 0.42,
                                "disk_percent": 55.0, "uptime_seconds": 9000.0,
                                "agent_version": "0.1.0", "running_jobs": 1, "queued_jobs": 2}},
            headers={"Authorization": f"Bearer {enrolled['node_token']}"})
        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(heartbeat.json()["heartbeat_interval"], fleet.HEARTBEAT_INTERVAL_SECONDS)
        listing = (await self.client.get("/api/fleet")).json()
        node = listing["nodes"][0]
        self.assertEqual(node["status"], "online")
        self.assertEqual(node["telemetry"]["cpu_percent"], 23.5)
        self.assertEqual(node["telemetry"]["queued_jobs"], 2)
        self.assertIsNotNone(node["last_seen_at"])
        self.assertEqual(listing["summary"]["online"], 1)

    async def test_offline_after_90_seconds_and_never_without_heartbeat(self):
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        enrolled = (await self._enroll(token=secret))
        self.assertEqual(enrolled.status_code, 200)
        listing = (await self.client.get("/api/fleet")).json()
        self.assertEqual(listing["nodes"][0]["status"], "never")
        node_token = enrolled.json()["node_token"]
        await self.client.post("/api/fleet/heartbeat", json={"telemetry": {"cpu_percent": 5.0}},
                               headers={"Authorization": f"Bearer {node_token}"})
        self.now += 89
        self.assertEqual((await self.client.get("/api/fleet")).json()["nodes"][0]["status"], "online")
        self.now += 2  # 91 seconds since last heartbeat
        self.assertEqual((await self.client.get("/api/fleet")).json()["nodes"][0]["status"], "offline")
        self.assertEqual((await self.client.get("/api/fleet")).json()["summary"]["offline"], 1)

    async def test_admin_endpoints_rejected_off_loopback_and_remote_gateway(self):
        # Origin/host/sec-fetch boundary via the shared require_local_admin guard.
        for headers in [{"origin": "https://evil.example"},
                        {"origin": "null"},
                        {"host": "rebind.evil.example"},
                        {"sec-fetch-site": "cross-site"}]:
            response = await self.client.post("/api/fleet/enroll-tokens", json={}, headers=headers)
            self.assertEqual(response.status_code, 403, headers)
            response = await self.client.get("/api/fleet", headers=headers)
            self.assertEqual(response.status_code, 403, headers)
        # A non-loopback client is denied even with a good Origin.
        remote_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("192.168.1.9", 4444)),
            base_url="http://127.0.0.1:8001", headers={"Origin": ORIGIN})
        try:
            self.assertEqual((await remote_client.get("/api/fleet")).status_code, 403)
            self.assertEqual((await remote_client.post("/api/fleet/enroll-tokens")).status_code, 403)
            # enroll and heartbeat are intentionally reachable off-loopback.
            enroll = await remote_client.post("/api/fleet/enroll", json=self._token_record())
            self.assertEqual(enroll.status_code, 401)  # invalid one-time token, not 403
        finally:
            await remote_client.aclose()
        # The phone gateway must never forward fleet routes.
        for method in ["GET", "POST", "DELETE"]:
            self.assertFalse(remote_access.api_allowed("/api/fleet", method))
            self.assertFalse(remote_access.api_allowed("/api/fleet/enroll-tokens", method))
            self.assertFalse(remote_access.api_allowed("/api/fleet/node-alpha", method))

    async def test_remove_node_requires_admin_and_persists(self):
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        await self._enroll(token=secret)
        removed = await self.client.delete("/api/fleet/node-alpha")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["removed"], "node-alpha")
        listing = (await self.client.get("/api/fleet")).json()
        self.assertEqual(listing["summary"]["total"], 0)
        again = await self.client.delete("/api/fleet/node-alpha")
        self.assertEqual(again.status_code, 404)
        # Duplicate node_id cannot silently take over an existing entry.
        secret2 = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        await self._enroll(token=secret2)
        secret3 = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        duplicate = await self._enroll(token=secret3)
        self.assertEqual(duplicate.status_code, 409)

    async def test_corrupt_nodes_file_raises_and_is_not_overwritten(self):
        (self.store.directory).mkdir(parents=True, exist_ok=True)
        (self.store.nodes_file).write_text("{not-json", encoding="utf-8")
        original = (self.store.nodes_file).read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.store._load()
        # Reads through the router surface a 500 with a clear message.
        response = await self.client.get("/api/fleet")
        self.assertEqual(response.status_code, 500)
        self.assertIn("无法读取", response.json()["detail"])
        # The damaged file is preserved byte-for-byte.
        self.assertEqual((self.store.nodes_file).read_text(encoding="utf-8"), original)

    async def test_failed_write_does_not_pollute_in_memory_registry(self):
        """A failed disk write must leave memory matching what is on disk.

        The registry hands out copies: if a caller mutates then fails to
        persist, the cache must not keep the mutation until restart.
        """
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        enrolled = await self._enroll(token=secret)
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        with patch.object(self.store, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.remove("node-alpha")
        on_disk = json.loads(self.store.nodes_file.read_text(encoding="utf-8"))["nodes"]
        self.assertIn("node-alpha", on_disk)
        listing = (await self.client.get("/api/fleet")).json()
        self.assertEqual([node["node_id"] for node in listing["nodes"]], ["node-alpha"])

    async def test_enroll_failure_counting_and_rate_limit(self):
        for _ in range(fleet.MAX_ENROLL_FAILURES):
            response = await self.client.post("/api/fleet/enroll", json=self._token_record(token="bogus-" + "x" * 20))
            self.assertEqual(response.status_code, 401)
        limited = await self.client.post("/api/fleet/enroll", json=self._token_record(token="bogus-" + "y" * 20))
        self.assertEqual(limited.status_code, 429)
        # Window passes; a valid token then works.
        self.now += fleet.ENROLL_FAILURE_WINDOW_SECONDS + 1
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        ok = await self._enroll(token=secret)
        self.assertEqual(ok.status_code, 200)

    async def test_summary_averages_only_online_nodes_with_telemetry(self):
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        first = (await self._enroll(token=secret)).json()
        secret2 = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        second = (await self._enroll(token=secret2, node_id="node-beta", name="节点 B")).json()
        secret3 = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        await self._enroll(token=secret3, node_id="node-gamma", name="节点 C")  # never heartbeats
        await self.client.post("/api/fleet/heartbeat",
                               json={"telemetry": {"cpu_percent": 20.0, "mem_percent": 50.0}},
                               headers={"Authorization": f"Bearer {first['node_token']}"})
        await self.client.post("/api/fleet/heartbeat",
                               json={"telemetry": {"cpu_percent": 30.0, "mem_percent": 70.0}},
                               headers={"Authorization": f"Bearer {second['node_token']}"})
        summary = (await self.client.get("/api/fleet")).json()["summary"]
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["online"], 2)
        self.assertEqual(summary["never"], 1)
        self.assertEqual(summary["cpu_percent_avg"], 25.0)
        self.assertEqual(summary["mem_percent_avg"], 60.0)

    async def test_telemetry_bounds_and_invalid_node_id_rejected(self):
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        out_of_range = await self.client.post(
            "/api/fleet/heartbeat", json={"telemetry": {"cpu_percent": 250.0}})
        self.assertEqual(out_of_range.status_code, 422)
        bad_id = await self.client.post(
            "/api/fleet/enroll", json=self._token_record(token=secret, node_id="Bad ID"))
        self.assertEqual(bad_id.status_code, 422)
        # The single-use token was NOT consumed by a rejected request: valid retry works.
        retry = await self._enroll(token=secret)
        self.assertEqual(retry.status_code, 200)


if __name__ == "__main__":
    unittest.main()
