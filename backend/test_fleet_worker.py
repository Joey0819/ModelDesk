"""Tests for Fleet Phase 2 remote worker job queue, polling, scheduling and reporting."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from backend import fleet

ORIGIN = "http://localhost:5173"
FAKE_TOKEN = "fixture-enroll-token-not-real-000"


def _make_app():
    app = FastAPI()
    app.include_router(fleet.router)
    return app


class FleetWorkerTests(unittest.IsolatedAsyncioTestCase):
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
            base_url="http://127.0.0.1:8001",
            headers={"Origin": ORIGIN},
        )

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    async def asyncTearDown(self):
        await self.client.aclose()

    async def _enroll_node(self, node_id="win-desk", platform="windows", labels=None):
        labels = labels or ["gpu", "win"]
        secret = (await self.client.post("/api/fleet/enroll-tokens")).json()["token"]
        payload = {
            "token": secret,
            "node_id": node_id,
            "name": f"节点 {node_id}",
            "platform": platform,
            "hostname": f"host-{node_id}",
            "labels": labels,
            "agent_version": "0.2.0",
        }
        res = await self.client.post("/api/fleet/enroll", json=payload)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    async def test_enqueue_and_list_jobs(self):
        created = await self.client.post(
            "/api/fleet/jobs",
            json={
                "title": "测试图像渲染任务",
                "task_type": "media_render",
                "inputs": {"prompt": "A sunset"},
                "constraints": {"require_labels": ["gpu"]},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        job = created.json()
        self.assertTrue(job["job_id"].startswith("flt_"))
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["title"], "测试图像渲染任务")

        listing = (await self.client.get("/api/fleet/jobs")).json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["jobs"][0]["job_id"], job["job_id"])

    async def test_poll_job_matches_labels_and_platform(self):
        # Create node A (linux, cpu) and node B (windows, gpu)
        node_a = await self._enroll_node("linux-cpu", "linux", ["cpu"])
        node_b = await self._enroll_node("win-gpu", "windows", ["gpu", "win"])

        # Enqueue job requiring gpu
        created = (
            await self.client.post(
                "/api/fleet/jobs",
                json={
                    "title": "GPU 训练子任务",
                    "task_type": "model_inference",
                    "constraints": {"require_labels": ["gpu"]},
                },
            )
        ).json()

        # Node A polls -> does not match
        poll_a = await self.client.post(
            "/api/fleet/jobs/poll",
            json={"telemetry": {"cpu_percent": 10.0}},
            headers={"Authorization": f"Bearer {node_a['node_token']}"},
        )
        self.assertEqual(poll_a.status_code, 200)
        self.assertIsNone(poll_a.json()["assigned_job"])

        # Node B polls -> matches!
        poll_b = await self.client.post(
            "/api/fleet/jobs/poll",
            json={"telemetry": {"cpu_percent": 15.0}},
            headers={"Authorization": f"Bearer {node_b['node_token']}"},
        )
        self.assertEqual(poll_b.status_code, 200)
        assigned = poll_b.json()["assigned_job"]
        self.assertIsNotNone(assigned)
        self.assertEqual(assigned["job_id"], created["job_id"])
        self.assertEqual(assigned["status"], "running")
        self.assertEqual(assigned["assigned_node_id"], "win-gpu")

    async def test_report_progress_and_completion(self):
        node = await self._enroll_node("worker-1", "linux", ["worker"])
        created = (
            await self.client.post(
                "/api/fleet/jobs",
                json={"title": "计算斐波那契数列", "task_type": "generic"},
            )
        ).json()

        poll = (
            await self.client.post(
                "/api/fleet/jobs/poll",
                json={"telemetry": {}},
                headers={"Authorization": f"Bearer {node['node_token']}"},
            )
        ).json()
        job_id = poll["assigned_job"]["job_id"]

        # Report progress
        prog = await self.client.post(
            f"/api/fleet/jobs/{job_id}/report",
            json={"status": "running", "progress": 50.0, "log_chunk": "计算完成 50%\n"},
            headers={"Authorization": f"Bearer {node['node_token']}"},
        )
        self.assertEqual(prog.status_code, 200)

        current = (await self.client.get(f"/api/fleet/jobs/{job_id}")).json()
        self.assertEqual(current["progress"], 50.0)
        self.assertIn("计算完成 50%", current["logs"][-1])

        # Complete job
        done = await self.client.post(
            f"/api/fleet/jobs/{job_id}/report",
            json={"status": "complete", "progress": 100.0, "result": {"fib_40": 102334155}},
            headers={"Authorization": f"Bearer {node['node_token']}"},
        )
        self.assertEqual(done.status_code, 200)

        final_job = (await self.client.get(f"/api/fleet/jobs/{job_id}")).json()
        self.assertEqual(final_job["status"], "complete")
        self.assertEqual(final_job["result"]["fib_40"], 102334155)

    async def test_unauthorized_node_cannot_report(self):
        node_1 = await self._enroll_node("node-1", "linux", [])
        node_2 = await self._enroll_node("node-2", "linux", [])

        created = (await self.client.post("/api/fleet/jobs", json={"title": "私有任务"})).json()
        # Assigned to node-1
        await self.client.post(
            "/api/fleet/jobs/poll",
            json={"telemetry": {}},
            headers={"Authorization": f"Bearer {node_1['node_token']}"},
        )

        # Node-2 tries to report -> 403
        bad_report = await self.client.post(
            f"/api/fleet/jobs/{created['job_id']}/report",
            json={"status": "complete"},
            headers={"Authorization": f"Bearer {node_2['node_token']}"},
        )
        self.assertEqual(bad_report.status_code, 403)

    async def test_offline_node_reaping_and_reassignment(self):
        node = await self._enroll_node("flaky-node", "linux", [])
        created = (await self.client.post("/api/fleet/jobs", json={"title": "长任务"})).json()

        # Node claims job
        await self.client.post(
            "/api/fleet/jobs/poll",
            json={"telemetry": {}},
            headers={"Authorization": f"Bearer {node['node_token']}"},
        )

        # 95 seconds pass -> node is now offline
        self.now += 95.0

        # Querying jobs triggers reap
        listing = (await self.client.get("/api/fleet/jobs")).json()
        job = listing["jobs"][0]
        self.assertEqual(job["status"], "blocked")
        self.assertEqual(job["failure_reason"], "node_offline")

        # Admin reassigns the blocked job
        reassigned = await self.client.post(f"/api/fleet/jobs/{job['job_id']}/reassign")
        self.assertEqual(reassigned.status_code, 200)
        self.assertEqual(reassigned.json()["status"], "queued")
        self.assertIsNone(reassigned.json()["assigned_node_id"])

    async def test_cancel_job(self):
        created = (await self.client.post("/api/fleet/jobs", json={"title": "将被取消的任务"})).json()
        cancelled = await self.client.post(f"/api/fleet/jobs/{created['job_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
