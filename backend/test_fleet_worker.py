"""Tests for Fleet Phase 2 remote worker job queue, polling, scheduling and reporting."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import fleet
from scripts import fleet_agent

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
        self.kanban_sync = self.enterContext(patch.object(fleet, "_sync_to_kanban"))
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

    async def test_bundled_legacy_worker_sends_heartbeat_and_leaves_job_for_capable_worker(self):
        bundled = await self._enroll_node("bundled-node", "linux", ["fixture"])
        capable = await self._enroll_node("custom-worker", "linux", ["fixture"])
        created = await self.client.post(
            "/api/fleet/jobs",
            json={
                "title": "汇总示例数值",
                "task_type": "sum",
                "inputs": {"values": [1, 2, 3]},
                "constraints": {"require_labels": ["fixture"], "target_platform": "linux"},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        job = created.json()
        job_url = f"/api/fleet/jobs/{job['job_id']}"
        server = "http://127.0.0.1:8001"
        stderr = StringIO()

        with (
            TestClient(self.app, base_url=server) as cli_client,
            patch.object(fleet_agent, "CREDENTIAL_FILE", self.directory / "bundled-credentials.json"),
            patch.object(sys, "argv", ["fleet_agent.py", "--worker"]),
            patch.object(fleet_agent, "collect_telemetry", return_value={"agent_version": fleet_agent.AGENT_VERSION}),
            patch.object(fleet_agent.time, "sleep", side_effect=KeyboardInterrupt),
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
        ):
            def post_to_fixture(url, payload, token):
                response = cli_client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json(), response.status_code

            fleet_agent.save_credentials(bundled["node_id"], bundled["node_token"], server)
            with patch.object(fleet_agent, "post_json", side_effect=post_to_fixture) as posts:
                self.assertEqual(fleet_agent.main(), 0)
        self.assertEqual((await self.client.get(job_url)).json(), job)
        self.assertEqual([call.args[0] for call in posts.call_args_list], [f"{server}/api/fleet/heartbeat"])
        self.assertIn("heartbeat", stderr.getvalue())
        listing = (await self.client.get("/api/fleet")).json()
        bundled_node = next(node for node in listing["nodes"] if node["node_id"] == bundled["node_id"])
        self.assertEqual(bundled_node["status"], "online")

        headers = {"Authorization": f"Bearer {capable['node_token']}"}
        poll = await self.client.post("/api/fleet/jobs/poll", json={}, headers=headers)
        assigned = poll.json()["assigned_job"]
        self.assertEqual(assigned["job_id"], job["job_id"])
        self.assertEqual(assigned["assigned_node_id"], capable["node_id"])
        result = {"sum": sum(assigned["inputs"]["values"])}
        done = await self.client.post(f"{job_url}/report", headers=headers, json={"status": "complete", "result": result})
        self.assertEqual(done.status_code, 200, done.text)
        finished = (await self.client.get(job_url)).json()
        self.assertEqual((finished["status"], finished["result"]), ("complete", {"sum": 6}))

    async def test_late_completion_cannot_override_admin_cancellation(self):
        node = await self._enroll_node()
        headers = {"Authorization": f"Bearer {node['node_token']}"}
        created = await self.client.post("/api/fleet/jobs", json={"title": "可取消任务"})
        job_url = f"/api/fleet/jobs/{created.json()['job_id']}"
        await self.client.post("/api/fleet/jobs/poll", headers=headers, json={})
        cancelled = (await self.client.post(f"{job_url}/cancel")).json()

        late = await self.client.post(
            f"{job_url}/report", headers=headers, json={"status": "complete", "result": {"count": 3}},
        )
        self.assertEqual(late.status_code, 409, late.text)
        self.assertEqual((await self.client.get(job_url)).json(), cancelled)
        self.assertEqual(self.kanban_sync.call_args.args[3], "cancelled")

    async def test_terminal_result_and_log_are_stable_on_report_retry(self):
        node = await self._enroll_node()
        headers = {"Authorization": f"Bearer {node['node_token']}"}
        created = await self.client.post(
            "/api/fleet/jobs", json={"title": "汇总示例数值", "inputs": {"values": [1, 2, 3]}},
        )
        job_url = f"/api/fleet/jobs/{created.json()['job_id']}"
        poll = await self.client.post("/api/fleet/jobs/poll", headers=headers, json={})
        result = {"sum": sum(poll.json()["assigned_job"]["inputs"]["values"])}
        self.now += fleet.OFFLINE_AFTER_SECONDS + 1
        blocked = (await self.client.get(job_url)).json()
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure_reason"], "node_offline")

        report = {"status": "complete", "progress": 100.0, "log_chunk": "汇总完毕", "result": result}
        done = await self.client.post(f"{job_url}/report", headers=headers, json=report)
        self.assertEqual(done.status_code, 200, done.text)
        terminal = (await self.client.get(job_url)).json()
        self.assertEqual(terminal["result"], {"sum": 6})
        self.assertIsNone(terminal["failure_reason"])

        restarted = fleet.FleetStore(self.directory, clock=lambda: self.now)
        self.now += 5
        with patch.object(fleet, "store", restarted):
            retry = await self.client.post(f"{job_url}/report", headers=headers, json=report)
            self.assertEqual(retry.status_code, 200, retry.text)
            conflict = await self.client.post(
                f"{job_url}/report", headers=headers, json={"status": "error", "error": "连接丢失"},
            )
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual((await self.client.get(job_url)).json(), terminal)
        self.assertEqual(
            [call.args[3] for call in self.kanban_sync.call_args_list], ["queued", "running", "error", "complete"],
        )

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
        node = await self._enroll_node("flaky-node", "linux", ["fixture"])
        other = await self._enroll_node("other-node", "linux", ["fixture"])
        created = (await self.client.post("/api/fleet/jobs", json={
            "title": "长任务",
            "constraints": {"target_node_id": node["node_id"], "target_platform": "linux", "require_labels": ["fixture"]},
        })).json()

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
        self.assertEqual(reassigned.json()["constraints"], created["constraints"])
        other_poll = await self.client.post("/api/fleet/jobs/poll", json={},
                                            headers={"Authorization": f"Bearer {other['node_token']}"})
        self.assertIsNone(other_poll.json()["assigned_job"])

    async def test_explicit_reassignment_replaces_target_and_starts_a_clean_attempt(self):
        original = await self._enroll_node("original-node", "linux", ["fixture"])
        replacement = await self._enroll_node("replacement-node", "linux", ["fixture"])
        original_headers = {"Authorization": f"Bearer {original['node_token']}"}
        replacement_headers = {"Authorization": f"Bearer {replacement['node_token']}"}
        created = (await self.client.post("/api/fleet/jobs", json={
            "title": "重新运行示例任务",
            "constraints": {
                "target_node_id": original["node_id"], "target_platform": "linux", "require_labels": ["fixture"],
            },
        })).json()
        job_url = f"/api/fleet/jobs/{created['job_id']}"
        poll = await self.client.post("/api/fleet/jobs/poll", json={}, headers=original_headers)
        self.assertEqual(poll.json()["assigned_job"]["job_id"], created["job_id"])
        previous_error = "原节点无法读取示例数据"
        failed = await self.client.post(f"{job_url}/report", headers=original_headers, json={
            "status": "error", "progress": 60.0, "error": previous_error, "result": {"partial": True},
        })
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertIsNotNone((await self.client.get(job_url)).json()["completed_at"])

        reset = await self.client.post(f"{job_url}/reassign", json={"target_node_id": replacement["node_id"]})
        self.assertEqual(reset.status_code, 200, reset.text)
        queued = reset.json()
        with self.subTest("previous attempt"):
            self.assertEqual(queued["status"], "queued")
            for field in ("assigned_node_id", "assigned_at", "completed_at", "failure_reason", "error", "result"):
                self.assertIsNone(queued[field], field)
            self.assertEqual(queued["progress"], 0.0)
            self.assertTrue(any(previous_error in line for line in queued["logs"]))

        original_poll = await self.client.post("/api/fleet/jobs/poll", json={}, headers=original_headers)
        replacement_poll = await self.client.post("/api/fleet/jobs/poll", json={}, headers=replacement_headers)
        with self.subTest("replacement node routing"):
            self.assertIsNone(original_poll.json()["assigned_job"])
            self.assertEqual(replacement_poll.json()["assigned_job"]["job_id"], created["job_id"])
            self.assertEqual(queued["constraints"], created["constraints"] | {"target_node_id": replacement["node_id"]})

        stale = await self.client.post(f"{job_url}/report", json={"status": "complete"}, headers=original_headers)
        self.assertEqual(stale.status_code, 403)
        complete = await self.client.post(f"{job_url}/report", headers=replacement_headers, json={
            "status": "complete", "progress": 100.0, "result": {"partial": False},
        })
        self.assertEqual(complete.status_code, 200, complete.text)
        finished = (await self.client.get(job_url)).json()
        self.assertEqual(finished["result"], {"partial": False})
        self.assertIsNone(finished["error"])

    async def test_already_queued_legacy_reassignment_keeps_its_requested_node(self):
        original = await self._enroll_node("legacy-original", "linux", ["fixture"])
        replacement = await self._enroll_node("legacy-replacement", "linux", ["fixture"])
        created = (await self.client.post("/api/fleet/jobs", json={
            "title": "已有重新调度任务", "constraints": {"target_node_id": original["node_id"]},
        })).json()
        job_id = created["job_id"]
        legacy = created | {"assigned_node_id": replacement["node_id"], "completed_at": created["created_at"],
                            "progress": 75.0, "result": {"old": True}, "error": "历史执行错误"}
        self.store.jobs_file.write_text(json.dumps({"jobs": {job_id: legacy}}), encoding="utf-8")

        with patch.object(fleet, "store", fleet.FleetStore(self.directory, clock=lambda: self.now)):
            queued = (await self.client.get(f"/api/fleet/jobs/{job_id}")).json()
            self.assertEqual(queued["constraints"]["target_node_id"], replacement["node_id"])
            self.assertIsNone(queued["assigned_node_id"])
            self.assertIsNone(queued["completed_at"])
            self.assertIsNone(queued["result"])
            self.assertIsNone(queued["error"])
            self.assertEqual(queued["progress"], 0.0)
            self.assertTrue(any("历史执行错误" in line for line in queued["logs"]))
            poll = await self.client.post("/api/fleet/jobs/poll", json={},
                                          headers={"Authorization": f"Bearer {replacement['node_token']}"})
            self.assertEqual(poll.json()["assigned_job"]["job_id"], job_id)

    async def test_cancel_job(self):
        created = (await self.client.post("/api/fleet/jobs", json={"title": "将被取消的任务"})).json()
        cancelled = await self.client.post(f"/api/fleet/jobs/{created['job_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
