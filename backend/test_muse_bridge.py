"""Native bridge state and HTTP boundaries without touching a user's window."""
import asyncio
import copy
import io
import json
import os
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
from backend.delivery_media import DeliveryMediaStore


class MuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.manager = MuseManager(self.root / "muse", poll_seconds=0.001, idle_seconds=0)
        self.stage_patch = patch.object(muse, "NATIVE_STAGING", self.root / "muse-native-staging")
        self.stage_patch.start()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.stage_patch.stop()
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

    async def test_current_completed_reply_keeps_only_valid_private_native_resources(self):
        url = "https://media.example/actual-video.mp4?X-Amz-Signature=private-download-grant"
        original = self.driver()

        async def resource_driver(command, payload=None):
            result = await original(command, payload)
            if command == "observe":
                result.update(resources=[{"url": url, "label": "真实视频" * 100}, {"url": url, "label": "duplicate"},
                                         {"url": "http://media.example/not-secure.mp4"},
                                         {"url": "https://127.0.0.1/private.png"}],
                              resource_request_id=payload["request_id"])
            return result

        with patch.object(self.manager, "helper_call", side_effect=resource_driver):
            result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["_native_resources"], [{"url": url, "label": ("真实视频" * 100)[:200]}])
        stored = json.loads((self.manager.store.directory / (result["id"] + ".json")).read_text())
        self.assertEqual(stored["_native_resources"], result["_native_resources"])
        self.assertNotIn("_native_resources", self.manager.list()[0])
        self.assertNotIn("private-download-grant", json.dumps(self.manager.list()))
        self.assertIn(url, json.dumps(self.manager.get(result["id"])))

    async def test_resources_need_current_request_receipt_and_completed_frame(self):
        url = "https://media.example/old.png?token=not-for-this-task"
        for variant in ["resource_id", "wrong_thread", "busy", "no_marker"]:
            original = self.driver()
            observations = 0

            async def stale_resources(command, payload=None):
                nonlocal observations
                result = await original(command, payload)
                if command == "observe":
                    observations += 1
                    if variant == "busy" and observations > 1:
                        return result
                    result.update(resources=[{"url": url}], resource_request_id=payload["request_id"])
                    if variant == "resource_id":
                        result["resource_request_id"] = str(uuid.uuid4())
                    elif variant == "wrong_thread":
                        result["thread_url"] = "https://muse.ai/thread/" + str(uuid.uuid4())
                    elif variant == "busy":
                        result["busy"] = True
                    elif variant == "no_marker":
                        result["messages"] = [{"role": "assistant", "text": "No current ending marker"}]
                return result

            with patch.object(self.manager, "helper_call", side_effect=stale_resources):
                result = await self.settle(await self.manager.create(self.request()))
            self.assertFalse(result.get("_native_resources"), variant)
            self.assertNotIn("not-for-this-task", json.dumps(result), variant)

    def test_native_resources_reject_untrusted_targets_and_bound_capture(self):
        invalid = ["https://localhost/a", "https://host.local/a", "https://169.254.169.254/a", "https://10.0.0.1/a",
                   "https://[::1]/a", "https://user:password@media.example/a", "file:///tmp/image.png",
                   "data:image/png;base64,aaaa", "https://media.example:8443/a", "https://media.example/a#other",
                   "https://media.example/a\nheader", "https://media.example/" + "a" * 8192]
        self.assertEqual(muse.safe_native_resources([{"url": url} for url in invalid]), [])
        self.assertEqual(muse.safe_native_resources("https://media.example/a"), [])
        self.assertEqual(muse.safe_native_resources([{"url": "https://media.example/a", "label": {}}]), [])
        self.assertEqual(len(muse.safe_native_resources([{"url": f"https://media.example/{index}.png"}
                                                        for index in range(30)])), 12)

    def test_public_tasks_remove_private_nested_fields_and_signed_prose_urls(self):
        first = "https://media.example/image.png?X-Amz-Signature=secret-one"
        second = "https://other.example/video.mp4?token=secret-two"
        task = {"_native_resources": [{"url": first, "label": "native"}],
                "response": first + " " + second + " https://docs.example/guide",
                "messages": [{"role": "assistant", "text": second, "_internal": "private"}],
                "events": [{"_transport": {"secret": "value"}, "type": "reply_observed"}]}
        public = muse.public_task(task)
        encoded = json.dumps(public)
        self.assertNotIn("secret-one", encoded)
        self.assertNotIn("secret-two", encoded)
        self.assertNotIn("_native_resources", encoded)
        self.assertNotIn("_internal", encoded)
        self.assertNotIn("_transport", encoded)
        self.assertIn("https://docs.example/guide", public["response"])
        self.assertIn("secret-one", json.dumps(task))

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

    async def test_rendered_inline_marker_accepts_only_exact_whitespace_suffix(self):
        for prefix, suffix, success in [("实际回复 ", "", True), ("实际回复\n", " ", True),
                                         ("实际回复x", "", False), ("实际回复 ", " trailing", False)]:
            async def inline_driver(command, payload=None):
                if command != "observe":
                    return await self.driver()(command, payload)
                return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"],
                        "messages": [{"role": "assistant", "text": prefix + marker(payload["request_id"]) + suffix}],
                        "busy": False, "waiting_user": False}
            with patch.object(self.manager, "helper_call", side_effect=inline_driver):
                task = await self.manager.create(self.request())
                result = await self.settle(task)
            self.assertEqual(result["status"] == "complete", success)

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

    async def test_production_style_native_failure_never_becomes_synthetic_success(self):
        # Use a plain coroutine, not AsyncMock: the previous implementation
        # bypassed its production fallback whenever helper_call was mocked.
        self.manager.allow_harness_fallback = True
        calls = []

        async def unavailable(command, payload=None):
            calls.append(command)
            return {"ok": False, "installed": True, "permission": False, "composer_ready": False}

        self.manager.helper_call = unavailable
        status = await self.manager.status()
        self.assertFalse(status["ok"])
        self.assertFalse(status["permission"])
        self.assertFalse(status["composer_ready"])
        result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["sent"])
        self.assertFalse(result["response"])
        self.assertIsNone(result["thread_url"])
        self.assertIsNone(result["completion_evidence"])
        self.assertNotIn("video_url", result)
        self.assertEqual(calls, ["probe", "probe"])

    async def test_unconfirmed_native_dispatch_retains_uncertainty_without_fallback(self):
        calls = []

        async def unconfirmed(command, payload=None):
            calls.append(command)
            if command == "probe":
                return {"ok": True, "installed": True, "permission": True}
            return {"ok": False, "sent": None, "uncertain": True, "request_id": payload["request_id"]}

        self.manager.helper_call = unconfirmed
        result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(calls, ["probe", "submit"])
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["uncertain"])
        self.assertIsNone(result["sent"])
        self.assertIsNone(result["thread_url"])
        self.assertIsNone(result["completion_evidence"])
        self.assertFalse(result["response"])

    async def test_known_native_send_failures_preserve_safe_diagnostics_without_resending(self):
        for code, sent, uncertain in [("new_chat_not_confirmed", False, False),
                                      ("send_outcome_unknown", None, True)]:
            original = self.driver()

            async def failed_submit(command, payload=None):
                if command == "submit":
                    return {"ok": False, "sent": sent, "uncertain": uncertain, "error": code,
                            "request_id": payload["request_id"], "private_detail": "unrelated draft text"}
                return await original(command, payload)

            with patch.object(self.manager, "helper_call", side_effect=failed_submit) as helper:
                result = await self.settle(await self.manager.create(self.request()))
            self.assertEqual(result["native_error_code"], code)
            self.assertIn(muse.NATIVE_FAILURES[code], result["error"])
            self.assertEqual(result["events"][-1]["native_error_code"], code)
            self.assertEqual(result["status"], "uncertain" if uncertain else "error")
            self.assertEqual(result["sent"], sent)
            self.assertEqual([call.args[0] for call in helper.await_args_list], ["probe", "submit"])
            self.assertNotIn("unrelated draft text", json.dumps(result))

    async def test_unknown_native_error_is_not_exposed_and_known_probe_error_is_helpful(self):
        async def failed_probe(command, payload=None):
            self.assertEqual(command, "probe")
            return {"ok": False, "installed": True, "permission": False,
                    "error": "accessibility_permission_required"}

        with patch.object(self.manager, "helper_call", side_effect=failed_probe):
            result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["native_error_code"], "accessibility_permission_required")
        self.assertIn(muse.NATIVE_FAILURES["accessibility_permission_required"], result["error"])
        self.assertFalse(result["sent"])
        self.assertEqual(muse.native_failure({"error": "private raw clipboard text"}), ("", {}))
        self.assertEqual(muse.native_failure({"error": {"nested": "private raw clipboard text"}}), ("", {}))

    async def test_native_account_without_composer_never_submits(self):
        async def no_composer(command, payload=None):
            self.assertEqual(command, "probe")
            return {"ok": True, "installed": True, "permission": True, "composer_ready": False}

        self.manager.helper_call = no_composer
        status = await self.manager.status()
        self.assertFalse(status["native_ready"])
        result = await self.settle(await self.manager.create(self.request()))
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["sent"])
        self.assertIsNone(result["completion_evidence"])

    async def test_historical_generated_text_survives_without_native_receipt_or_asset_claim(self):
        with patch.object(self.manager, "helper_call", self.driver()):
            original = await self.settle(await self.manager.create(self.request()))
        persisted = copy.deepcopy(original)
        persisted.pop("member_id")  # Pre-migration request records remain readable.
        persisted.update(response="Old local draft", verification=muse.HARNESS_VERIFICATION,
                         video_url="/muse-media/unrelated.mp4", _native_resources=[{"url": "https://media.example/unsupported.mp4"}])
        self.manager.store.save(persisted)
        recovered = MuseManager(self.manager.store.directory)
        recovered.recover()
        result = recovered.get(original["id"])
        self.assertEqual(result["response"], "Old local draft")
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["source_unverified"])
        self.assertEqual(result["completion_evidence"], "source_unverified")
        self.assertIsNone(result["thread_url"])
        self.assertIsNone(result["video_url"])
        self.assertEqual(result["unverified_video_url"], "/muse-media/unrelated.mp4")
        self.assertNotIn("_native_resources", result)
        with self.assertRaises(HTTPException):
            await recovered.resume_observing(result["id"])

    def typed_room(self):
        return {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()), "team_revision": "a" * 64,
                "team_snapshot": {"members": [{"id": "creative", "name": "创意 Agent", "kind": "muse", "model": None, "role": "创作"}]}}

    async def test_typed_muse_member_is_frozen_from_its_own_room(self):
        from backend import groups
        room = self.typed_room()
        with patch("backend.projects.store.get"), patch.object(groups.store, "get", return_value=room), patch.object(self.manager, "helper_call", self.driver()):
            result = await self.settle(await self.manager.create(self.request(project_id=room["project_id"], room_id=room["id"], member_id="creative")))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["member"], room["team_snapshot"]["members"][0])
        self.assertEqual(result["team_revision"], room["team_revision"])
        self.assertEqual(result["source_kind"], "native_muse")

    async def test_typed_muse_rejects_model_or_foreign_member_before_enqueue(self):
        from backend import groups
        room = self.typed_room()
        room["team_snapshot"]["members"][0]["kind"] = "model"
        with patch("backend.projects.store.get"), patch.object(groups.store, "get", return_value=room), patch.object(self.manager, "helper_call", self.driver()) as helper:
            for member in ["creative", "foreign"]:
                with self.assertRaises(HTTPException) as error:
                    await self.manager.create(self.request(project_id=room["project_id"], room_id=room["id"], member_id=member))
                self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(helper.called)
        self.assertFalse(self.manager.records)

    async def test_member_revision_changes_during_probe_or_submit_cannot_claim_completion(self):
        from backend import groups
        for changed_at in ["probe", "submit", "observe"]:
            room = self.typed_room()
            original = self.driver()
            calls = []

            async def changing_driver(command, payload=None):
                calls.append(command)
                result = await original(command, payload)
                if command == changed_at:
                    room["team_revision"] = "b" * 64
                return result

            with patch("backend.projects.store.get"), patch.object(groups.store, "get", return_value=room), patch.object(self.manager, "helper_call", side_effect=changing_driver):
                result = await self.settle(await self.manager.create(self.request(project_id=room["project_id"], room_id=room["id"], member_id="creative")))
            self.assertEqual(calls, {"probe": ["probe"], "submit": ["probe", "submit"], "observe": ["probe", "submit", "observe"]}[changed_at])
            self.assertEqual(result["status"], "error" if changed_at == "probe" else "uncertain")
            self.assertIsNone(result["completion_evidence"])
            if changed_at == "observe":
                self.assertTrue(result["sent"])
                self.assertIsNotNone(result["thread_url"])
            else:
                self.assertIsNone(result["thread_url"])
            self.assertFalse(result["response"])

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

    async def test_http_tasks_and_exports_never_return_native_download_grants(self):
        app = FastAPI()
        app.include_router(muse.router)
        origin = {"Origin": "http://localhost:5173"}
        with patch.object(self.manager, "helper_call", self.driver()):
            task = await self.settle(await self.manager.create(self.request()))
        private_url = "https://media.example/file.mp4?X-Goog-Signature=private-signed-resource"
        task.update(_native_resources=[{"url": private_url, "label": "Actual output"}], response="真实回复 " + private_url)
        self.manager.save(task)
        with patch.object(muse, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 3000)), base_url="http://localhost") as client:
                responses = [await client.get("/api/muse/tasks"), await client.get(f"/api/muse/tasks/{task['id']}"),
                             await client.get(f"/api/muse/tasks/{task['id']}/export?format=md"),
                             await client.get(f"/api/muse/tasks/{task['id']}/export?format=json"),
                             await client.post(f"/api/muse/tasks/{task['id']}/cancel", headers=origin)]
                with patch.object(self.manager, "create", new=AsyncMock(return_value=task)):
                    responses.append(await client.post("/api/muse/tasks", json=self.request().model_dump(mode="json"), headers=origin))
                with patch.object(self.manager, "resume_observing", new=AsyncMock(return_value=task)):
                    responses.append(await client.post(f"/api/muse/tasks/{task['id']}/observe", headers=origin))
        for response in responses:
            self.assertIn(response.status_code, {200, 202})
            self.assertNotIn("_native_resources", response.text)
            self.assertNotIn("private-signed-resource", response.text)
        self.assertIn(private_url, self.manager.get(task["id"])["response"])

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

    def native_file_fixture(self):
        from PIL import Image
        content = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(content, format="PNG")
        task = {"id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()), "parent_job_id": str(uuid.uuid4())}
        path, descriptor = muse.native_stage(task["id"], create=True)
        os.close(descriptor)
        filename = str(uuid.uuid4()) + ".png"
        (path / filename).write_bytes(content.getvalue())
        return task, path, filename, content.getvalue()

    def test_native_files_import_exact_private_file_into_parent_owned_media(self):
        task, directory, filename, raw = self.native_file_fixture()
        media_store = DeliveryMediaStore(self.root / "verified-media")
        result = {"file_request_id": task["request_id"], "downloaded_files": [{"name": filename, "label": "本轮实际图片"}]}
        with patch("backend.delivery_media.store", media_store):
            assets, errors = muse.import_native_files(task, result)
        self.assertEqual(errors, [])
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset["job_id"], task["parent_job_id"])
        self.assertEqual(asset["project_id"], task["project_id"])
        self.assertEqual(asset["source_task_id"], task["id"])
        self.assertEqual(asset["kind"], "image")
        self.assertEqual(media_store.read({"id": task["parent_job_id"], "project_id": task["project_id"]}, asset["id"])[1], raw)
        self.assertFalse((directory / filename).exists())

    def test_native_files_reject_wrong_receipt_escape_symlink_and_hardlink(self):
        task, directory, filename, _ = self.native_file_fixture()
        assets, errors = muse.import_native_files(task, {"file_request_id": "other", "downloaded_files": [{"name": filename}]})
        self.assertEqual((assets, errors), ([], []))
        outside = self.root / "private.png"
        outside.write_bytes(b"Do not read unrelated user files")
        symlink = str(uuid.uuid4()) + ".png"
        (directory / symlink).symlink_to(outside)
        hardlink = str(uuid.uuid4()) + ".png"
        os.link(outside, directory / hardlink)
        result = {"file_request_id": task["request_id"], "downloaded_files": [
            {"name": "../private.png"}, {"name": str(outside)}, {"name": symlink}, {"name": hardlink}]}
        with patch("backend.delivery_media.store.save_bytes") as save:
            assets, errors = muse.import_native_files(task, result)
        self.assertEqual(assets, [])
        self.assertTrue(errors)
        save.assert_not_called()
        self.assertEqual(outside.read_bytes(), b"Do not read unrelated user files")

    def test_native_file_staging_cannot_traverse_a_symlinked_directory(self):
        outside = self.root / "unrelated"
        outside.mkdir()
        (self.root / "muse-native-staging").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            muse.native_stage(str(uuid.uuid4()), create=True)
        self.assertEqual(list(outside.iterdir()), [])

    async def test_observed_native_downloads_are_private_and_never_paths_in_public_tasks(self):
        media_store = DeliveryMediaStore(self.root / "verified-media")
        original = self.driver()
        from PIL import Image

        async def downloaded(command, payload=None):
            result = await original(command, payload)
            if command == "observe":
                path = Path(payload["download_directory"])
                self.assertEqual(path.name, payload["request_id"])
                self.assertEqual(path.parent.name, "muse-native-staging")
                filename = str(uuid.uuid4()) + ".png"
                Image.new("RGB", (2, 2), "orange").save(path / filename)
                result.update(file_request_id=payload["request_id"], downloaded_files=[{"name": filename, "label": "真实原生附件"}])
            return result

        request = self.request(project_id=str(uuid.uuid4()))
        parent = str(uuid.uuid4())
        with patch("backend.projects.store.get"), patch.object(self.manager, "helper_call", side_effect=downloaded), \
                patch("backend.delivery_media.store", media_store):
            result = await self.settle(await self.manager.create(request, parent_job_id=parent))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["_native_media"]), 1)
        self.assertEqual(result["_native_media"][0]["job_id"], parent)
        public = json.dumps(self.manager.list(str(request.project_id)))
        self.assertNotIn("_native_media", public)
        self.assertNotIn(str(self.root), public)


if __name__ == "__main__":
    unittest.main()
