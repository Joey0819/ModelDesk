"""Native bridge state and HTTP boundaries without touching a user's window."""
import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import muse_bridge as muse
from backend.muse_bridge import MuseManager, MuseTaskRequest, marker, valid_thread
from backend.projects import ProjectStore


class MuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = MuseManager(self.root / "muse", poll_seconds=0.001, idle_seconds=0)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temp.cleanup()

    async def settle(self, task):
        runner = self.manager.tasks.get(task["id"])
        if runner:
            await runner
        return self.manager.get(task["id"], task["project_id"])

    def request(self, **fields):
        return MuseTaskRequest(prompt="写一份本机测试说明", **fields)

    def driver(self, observations=None):
        observations = list(observations or [])
        async def call(command, payload=None):
            if command == "probe":
                return {"ok": True, "installed": True, "permission": True, "composer_ready": True, "version": "7.0", "thread_url": "https://muse.ai/thread/private-other-chat"}
            if command == "submit":
                self.assertIn(marker(payload["request_id"]), payload["prompt"])
                self.assertIsNone(payload["thread_url"])
                return {"ok": True, "sent": True, "request_id": payload["request_id"], "thread_url": "https://muse.ai/thread/" + payload["request_id"]}
            if command == "observe":
                result = observations.pop(0) if observations else {"messages": [{"role": "assistant", "text": "真实回复\n" + marker(payload["request_id"])}], "busy": False, "waiting_user": False}
                return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"], **result}
            self.fail(command)
        return AsyncMock(side_effect=call)

    async def test_native_reply_requires_matching_assistant_boundary_and_not_busy(self):
        held = asyncio.Event()
        calls = []
        async def driver(command, payload=None):
            calls.append(command)
            if command == "probe":
                return {"ok": True, "installed": True, "permission": True}
            if command == "submit":
                stored = json.loads((self.manager.store.directory / (payload["request_id"] + ".json")).read_text())
                self.assertEqual(stored["status"], "dispatching")
                self.assertIsNone(stored["sent"])
                self.assertTrue(stored["uncertain"])
                return {"ok": True, "sent": True, "request_id": payload["request_id"], "thread_url": "https://muse.ai/thread/" + payload["request_id"]}
            count = calls.count("observe")
            if count == 1:
                return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"], "messages": [{"role": "user", "text": marker(payload["request_id"])}], "busy": False, "waiting_user": False}
            if count == 2:
                return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"], "messages": [{"role": "assistant", "text": "answer\n" + marker(payload["request_id"])}], "busy": True, "waiting_user": False}
            held.set()
            return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"], "messages": [{"role": "assistant", "text": "answer\n" + marker(payload["request_id"])}], "busy": False, "waiting_user": False}
        with patch.object(self.manager, "helper_call", side_effect=driver):
            task = await self.manager.create(self.request())
            result = await self.settle(task)
        self.assertTrue(held.is_set())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["response"], "answer")
        self.assertEqual(result["completion_evidence"], "framed_assistant_reply")
        self.assertIn("不证明", result["verification"])
        self.assertFalse(result["monitoring"])

    async def test_missing_marker_preserves_output_and_explicit_observe_never_resends(self):
        driver = self.driver([{ "messages": [{"role": "assistant", "text": "未确认回复"}], "busy": False, "waiting_user": False}] * 2)
        with patch.object(self.manager, "helper_call", driver):
            task = await self.manager.create(self.request())
            result = await self.settle(task)
            self.assertEqual(result["status"], "waiting_user")
            self.assertTrue(result["uncertain"])
            self.assertEqual(result["response"], "未确认回复")
            self.assertFalse(result["monitoring"])
            await self.manager.resume_observing(task["id"])
            final = await self.settle(task)
        self.assertEqual(final["status"], "complete")
        self.assertEqual([call.args[0] for call in driver.await_args_list].count("submit"), 1)

    async def test_one_native_account_serializes_whole_tasks(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        original = self.driver()
        async def driver(command, payload=None):
            calls.append((command, payload))
            if command == "observe" and not release.is_set():
                started.set()
                await release.wait()
            return await original(command, payload)
        with patch.object(self.manager, "helper_call", side_effect=driver):
            first = await self.manager.create(self.request())
            second = await self.manager.create(self.request())
            await started.wait()
            self.assertEqual(sum(command == "submit" for command, _ in calls), 1)
            self.assertEqual(self.manager.get(second["id"])["status"], "queued")
            release.set()
            await self.settle(first)
            await self.settle(second)
        submitted = [payload["request_id"] for command, payload in calls if command == "submit"]
        self.assertEqual(submitted, [first["id"], second["id"]])

    async def test_cancellation_during_send_preserves_remote_uncertainty(self):
        dispatch = asyncio.Event()
        async def driver(command, payload=None):
            if command == "probe":
                return {"ok": True, "installed": True, "permission": True}
            dispatch.set()
            await asyncio.Event().wait()
        with patch.object(self.manager, "helper_call", side_effect=driver):
            task = await self.manager.create(self.request())
            await dispatch.wait()
            result = await self.manager.cancel(task["id"].upper())
        self.assertEqual(result["status"], "stopped_monitoring")
        self.assertEqual(result["remote_state"], "unknown")
        self.assertTrue(result["uncertain"])
        self.assertIsNone(result["sent"])
        self.assertFalse(self.manager.tasks)

    async def test_cancel_before_dispatch_never_sends(self):
        with patch.object(self.manager, "helper_call", self.driver()) as driver:
            task = await self.manager.create(self.request())
            result = await self.manager.cancel(task["id"])
        self.assertEqual(result["remote_state"], "not_sent")
        self.assertFalse(result["uncertain"])
        self.assertFalse(driver.called)

    async def test_observation_failure_does_not_replay_or_claim_remote_stopped(self):
        driver = self.driver()
        async def broken(command, payload=None):
            if command == "observe":
                raise RuntimeError("private raw diagnostics must not reach UI")
            return await driver(command, payload)
        with patch.object(self.manager, "helper_call", side_effect=broken):
            result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["sent"])
        self.assertEqual(result["remote_state"], "unknown")
        self.assertNotIn("private raw", result["error"])
        self.assertEqual([call.args[0] for call in driver.await_args_list].count("submit"), 1)

    async def test_request_id_mismatch_or_wrong_thread_cannot_complete(self):
        for variant in ["receipt", "thread", "observation_id"]:
            driver = self.driver()
            async def mismatch(command, payload=None):
                result = await driver(command, payload)
                if variant == "receipt" and command == "submit":
                    result["request_id"] = str(uuid.uuid4())
                if variant == "thread" and command == "observe":
                    result["thread_url"] = "https://muse.ai/thread/different"
                if variant == "observation_id" and command == "observe":
                    result["request_id"] = str(uuid.uuid4())
                return result
            with patch.object(self.manager, "helper_call", side_effect=mismatch):
                result = await self.settle(await self.manager.create(self.request()))
            self.assertEqual(result["status"], "uncertain")
            self.assertIsNone(result["completion_evidence"])

    async def test_restart_recovers_no_tasks_and_never_resubmits(self):
        with patch.object(self.manager, "helper_call", self.driver()):
            completed = await self.settle(await self.manager.create(self.request()))
        persisted = copy.deepcopy(completed)
        persisted.update(status="dispatching", sent=None, uncertain=True, thread_url=None, monitoring=True)
        self.manager.store.save(persisted)
        (self.manager.store.directory / (str(uuid.uuid4()) + ".json")).write_text('{"broken":true}')
        recovered = MuseManager(self.manager.store.directory)
        recovered.recover()
        task = recovered.get(completed["id"])
        self.assertEqual(task["status"], "interrupted")
        self.assertTrue(task["uncertain"])
        self.assertFalse(task["monitoring"])
        self.assertFalse(recovered.tasks)
        self.assertEqual(len(recovered.records), 1)

    async def test_missing_permission_blocks_send_and_status_hides_unrelated_chat(self):
        with patch.object(self.manager, "helper_call", self.driver()):
            status = await self.manager.status()
        self.assertNotIn("thread_url", status)
        self.assertEqual(status["kind"], "external_agent")
        with patch.object(self.manager, "helper_call", new=AsyncMock(return_value={"ok": True, "installed": True, "permission": False})) as driver:
            result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["sent"])
        self.assertEqual(driver.await_count, 1)

    async def test_http_loopback_origin_scope_and_export(self):
        projects = ProjectStore(self.root / "projects")
        app = FastAPI()
        app.include_router(muse.router)
        origin = {"Origin": "http://localhost:5173"}
        with patch.object(muse, "manager", self.manager), patch("backend.projects.store", projects), patch.object(self.manager, "helper_call", self.driver()):
            first, second = projects.register("first"), projects.register("second")
            body = self.request(project_id=first["id"]).model_dump(mode="json")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 3000)), base_url="http://localhost") as client:
                for headers in [{}, {"Origin": "https://evil.example"}, {**origin, "Sec-Fetch-Site": "cross-site"}]:
                    response = await client.post("/api/muse/tasks", json=body, headers=headers)
                    self.assertEqual(response.status_code, 403)
                created = await client.post("/api/muse/tasks", json=body, headers=origin)
                self.assertEqual(created.status_code, 202, created.text)
                task = await self.settle(created.json())
                self.assertEqual((await client.get("/api/muse/tasks")).json(), [])
                denied = await client.get(f"/api/muse/tasks/{task['id']}", params={"project_id": second["id"]})
                self.assertEqual(denied.status_code, 403)
                exported = await client.get(f"/api/muse/tasks/{task['id']}/export", params={"project_id": first["id"]})
                self.assertIn("真实回复", exported.text)
                self.assertIn(task["thread_url"], exported.text)
                self.assertIn("不证明", exported.text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.20", 3000)), base_url="http://localhost") as remote:
                response = await remote.post("/api/muse/tasks", json=body, headers=origin)
                self.assertEqual(response.status_code, 403)

    async def test_room_scope_checked_before_enqueue(self):
        from backend import groups
        project = str(uuid.uuid4())
        room = str(uuid.uuid4())
        with patch("backend.projects.store.get"), patch.object(groups.store, "get", side_effect=HTTPException(403, "wrong project")) as get_room:
            with self.assertRaises(HTTPException):
                await self.manager.create(self.request(project_id=project, room_id=room))
        get_room.assert_called_once_with(room, project)
        self.assertFalse(self.manager.records)

    async def test_timeout_preserves_reply_without_remote_cancel(self):
        driver = self.driver([{ "messages": [{"role": "assistant", "text": "still working"}], "busy": True, "waiting_user": False}])
        with patch.object(self.manager, "helper_call", driver), patch.object(muse, "time", SimpleNamespace(monotonic=iter([0, 0, 1801]).__next__)):
            result = await self.settle(await self.manager.create(self.request(timeout_seconds=30)))
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["response"], "still working")
        self.assertEqual(result["remote_state"], "unknown")

    def test_thread_urls_are_exact_public_muse_threads(self):
        self.assertTrue(valid_thread("https://muse.ai/thread/" + str(uuid.uuid4())))
        for value in ["https://muse.ai", "https://muse.ai/thread/new", "https://muse.ai/api/auth/check", "https://muse.ai/thread/abc?token=value", "https://muse.ai/thread/abc#hash", "https://user@muse.ai/thread/abc", "https://muse.ai.evil.com/thread/abc", "hatch://thread/abc", "https://muse.ai:999/thread/abc"]:
            self.assertFalse(valid_thread(value), value)


if __name__ == "__main__":
    unittest.main()
