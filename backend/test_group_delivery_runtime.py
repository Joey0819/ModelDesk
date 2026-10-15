"""Automatic delivery uses real scoped managers and never fabricates completion."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from backend import group_delivery_runtime as runtime, group_tasks, groups, muse_bridge, projects
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore


class RuntimeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "original"
        self.repo.mkdir()
        group_tasks.local_git(self.repo, "init", "-q", "--template=", "--initial-branch=main")
        (self.repo / "readme.md").write_text("original\n")
        group_tasks.local_git(self.repo, "add", ".")
        group_tasks.local_git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "initial", "--no-gpg-sign")
        self.projects = projects.ProjectStore(self.root / "projects")
        self.project = self.projects.register("delivery", str(self.repo))
        self.rooms = groups.RoomStore(self.root / "rooms")
        self.teams = TeamStore(self.root / "teams")
        self.tasks = group_tasks.TaskManager(self.root / "tasks")
        self.muse = muse_bridge.MuseManager(self.root / "muse", poll_seconds=.001, idle_seconds=.001)
        caps = {"engines": [{"id": engine, "label": engine, "available": True, "authenticated": True,
                             "isolation": "test_only"} for engine in ["codex", "claude"]]}
        self.patches = [patch.object(groups, "store", self.rooms), patch.object(projects, "store", self.projects),
                        patch.object(group_tasks, "manager", self.tasks), patch.object(muse_bridge, "manager", self.muse),
                        patch.object(group_tasks, "engine_status", AsyncMock(return_value=caps)),
                        patch.object(runtime, "POLL_SECONDS", .001)]
        for item in self.patches:
            item.start()
        self.team = self.teams.put(TeamRequest(name="Delivery", project_id=self.project["id"], coordinator_model="gpt-test", members=[
            {"id": "writer", "name": "Writer", "kind": "codex", "role": "write"},
            {"id": "reviewer", "name": "Reviewer", "kind": "claude", "role": "review"},
            {"id": "designer", "name": "Muse", "kind": "muse", "role": "design"}]))
        self.room = self.rooms.create(groups.RoomRequest(title="Delivery", project_id=self.project["id"], team_id=self.team["id"]), self.teams)
        self.job = {"id": str(uuid.uuid4()), "project_id": self.project["id"], "group_room_id": self.room["id"],
                    "conversation_id": self.room["id"], "group_team_snapshot": copy.deepcopy(self.room["team_snapshot"])}
        self.writer, self.reviewer, self.designer = self.room["team_snapshot"]["members"]

    async def asyncTearDown(self):
        await self.tasks.shutdown()
        await self.muse.shutdown()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def complete_worker(self, item):
        durable = json.loads((self.tasks.directory / (item["id"] + ".json")).read_text())
        self.assertEqual(durable["parent_job_id"], self.job["id"])
        item.update(status="complete", output="Actual worker output", exit_code=0)
        self.tasks.save(item)

    async def test_cli_parent_link_is_durable_before_execution_and_reused(self):
        prompt = "Original goal\n<discussion>cited material</discussion>"
        with patch.object(self.tasks, "execute", self.complete_worker), patch.object(self.tasks, "create", wraps=self.tasks.create) as create:
            result = await runtime.run_runtime_step(self.job, self.writer, prompt)
            second = await runtime.run_runtime_step(self.job, self.writer, prompt)
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["output"], "Actual worker output")
        self.assertEqual(second["task_id"], result["task_id"])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(create.await_args.kwargs["parent_job_id"], self.job["id"])
        child = self.tasks.get(result["task_id"], self.project["id"], self.room["id"])
        self.assertEqual(child["prompt"], prompt)
        self.assertEqual(child["parent_prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        reloaded = group_tasks.TaskManager(self.root / "tasks")
        self.assertEqual(reloaded.records[child["id"]]["parent_job_id"], self.job["id"])
        self.assertFalse(group_tasks.local_git(self.repo, "status", "--porcelain"))

    async def test_cancellation_stops_the_actual_created_child(self):
        entered = asyncio.Event()

        async def worker(item):
            item["status"] = "running"
            self.tasks.save(item)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                item.update(status="cancelled", error="Cancelled by parent")
                self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker):
            running = asyncio.create_task(runtime.run_runtime_step(self.job, self.writer, "Write output"))
            await asyncio.wait_for(entered.wait(), 1)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertEqual(len(self.tasks.records), 1)
        child = next(iter(self.tasks.records.values()))
        self.assertEqual(child["status"], "cancelled")
        self.assertTrue(self.tasks.tasks[child["id"]].done())

    async def test_cancellation_during_cli_preflight_never_creates_child(self):
        entered = asyncio.Event()

        async def preflight():
            entered.set()
            await asyncio.Event().wait()

        with patch.object(group_tasks, "engine_status", preflight):
            running = asyncio.create_task(runtime.run_runtime_step(self.job, self.writer, "Write output"))
            await asyncio.wait_for(entered.wait(), 1)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertFalse(self.tasks.records)
        self.assertFalse(self.tasks.tasks)

    async def test_native_remote_boundary_blocks_before_create_or_probe(self):
        with patch.object(self.muse, "create", wraps=self.muse.create) as create, patch.object(self.muse, "helper_call", AsyncMock()) as helper:
            result = await runtime.run_runtime_step(self.job, self.designer, "Create images")
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["task_id"])
        create.assert_not_called()
        helper.assert_not_called()
        self.assertFalse(self.muse.records)

    async def muse_helper(self, command, payload=None):
        if command == "probe":
            return {"ok": True, "installed": True, "permission": True, "composer_ready": True}
        if command == "submit":
            return {"ok": True, "sent": True, "uncertain": False, "request_id": payload["request_id"],
                    "thread_url": "https://muse.ai/thread/" + str(uuid.uuid4())}
        return {"ok": True, "thread_url": payload["thread_url"], "request_id": payload["request_id"],
                "messages": [{"role": "assistant", "text": "Actual Muse reply\n" + muse_bridge.marker(payload["request_id"])}],
                "busy": False, "waiting_user": False}

    async def test_muse_needs_actual_framed_native_reply_and_never_resends(self):
        with patch.object(self.muse, "helper_call", self.muse_helper), patch.object(self.muse, "create", wraps=self.muse.create) as create:
            first = await runtime.run_runtime_step(self.job, self.designer, "Produce final images", native_allowed=True)
            again = await runtime.run_runtime_step(self.job, self.designer, "Produce final images", native_allowed=True)
        self.assertEqual(first["status"], "complete", first)
        self.assertEqual(first["output"], "Actual Muse reply")
        self.assertEqual(first["sources"][0]["completion_evidence"], "framed_assistant_reply")
        self.assertEqual(first["task_id"], again["task_id"])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(create.await_args.kwargs["parent_job_id"], self.job["id"])
        durable = json.loads((self.muse.store.directory / (first["task_id"] + ".json")).read_text())
        self.assertEqual(durable["parent_job_id"], self.job["id"])
        self.assertTrue(durable["sent"])

    async def test_cancel_muse_stops_observation_without_resending_or_claiming_remote_stop(self):
        entered = asyncio.Event()
        submits = []

        async def helper(command, payload=None):
            if command == "observe":
                entered.set()
                await asyncio.Event().wait()
            if command == "submit":
                submits.append(payload["request_id"])
            return await self.muse_helper(command, payload)

        with patch.object(self.muse, "helper_call", helper):
            running = asyncio.create_task(runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True))
            await asyncio.wait_for(entered.wait(), 1)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertEqual(len(submits), 1)
        child = next(iter(self.muse.records.values()))
        self.assertEqual(child["status"], "stopped_monitoring")
        self.assertFalse(child["monitoring"])
        self.assertTrue(child["uncertain"])
        self.assertEqual(child["remote_state"], "unknown")
        self.assertIn("不会停止 Muse 云端任务", child["error"])

    async def test_legacy_generated_muse_output_is_never_confirmed_as_native(self):
        with patch.object(self.muse, "helper_call", self.muse_helper):
            first = await runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True)
        child = self.muse.records[first["task_id"]]
        child["source_unverified"] = True
        with patch.object(self.muse, "helper_call", AsyncMock()) as helper:
            result = await runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("thread_url", result)
        self.assertEqual(result["sources"][0]["completion_evidence"], "source_unverified")
        helper.assert_not_called()

    async def test_uncertain_native_receipt_stays_blocked_with_no_second_send(self):
        calls = []

        async def helper(command, payload=None):
            calls.append(command)
            if command == "submit":
                return {"ok": False, "sent": None, "uncertain": True, "request_id": payload["request_id"]}
            return await self.muse_helper(command, payload)

        with patch.object(self.muse, "helper_call", helper):
            first = await runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True)
            again = await runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True)
        self.assertEqual(first["status"], "blocked", first)
        self.assertEqual(first["runtime_status"], "uncertain")
        self.assertEqual(first["task_id"], again["task_id"])
        self.assertEqual(calls.count("submit"), 1)

    async def test_waiting_user_is_blocked_without_observe_resume(self):
        async def helper(command, payload=None):
            result = await self.muse_helper(command, payload)
            if command == "observe":
                result.update(messages=[], busy=False, waiting_user=True)
            return result

        with patch.object(self.muse, "helper_call", helper), patch.object(self.muse, "resume_observing", AsyncMock()) as resume:
            result = await runtime.run_runtime_step(self.job, self.designer, "Produce images", native_allowed=True)
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["runtime_status"], "waiting_user")
        self.assertTrue(result["observation_stopped"])
        child = self.muse.records[result["task_id"]]
        self.assertFalse(child["monitoring"])
        self.assertEqual(child["delivery_observed_status"], "waiting_user")
        self.assertNotIn(child["id"], self.muse.tasks)
        resume.assert_not_called()

    async def test_roster_change_during_execution_stops_child(self):
        async def worker(item):
            room = self.rooms.get(self.room["id"], self.project["id"])
            room["team_snapshot"]["members"][0]["name"] = "New writer"
            self.rooms.save(room)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                item.update(status="cancelled", error="Stopped")
                self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker):
            result = await runtime.run_runtime_step(self.job, self.writer, "Write article")
        self.assertEqual(result["status"], "error", result)
        self.assertIn("群成员已变化", result["error"])
        self.assertEqual(next(iter(self.tasks.records.values()))["status"], "cancelled")

    async def test_wrong_room_or_tampered_member_cannot_create_or_leak_output(self):
        job = {**self.job, "group_room_id": str(uuid.uuid4())}
        with patch.object(self.tasks, "create", wraps=self.tasks.create) as create:
            result = await runtime.run_runtime_step(job, self.writer, "Write")
            tampered = await runtime.run_runtime_step(self.job, {**self.writer, "kind": "claude"}, "Write")
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["task_id"])
        self.assertEqual(result["output"], "")
        self.assertEqual(tampered["status"], "error")
        create.assert_not_called()
        foreign = {"id": str(uuid.uuid4()), "parent_job_id": self.job["id"], "member_id": self.writer["id"],
                   "member": self.writer, "engine": "codex", "project_id": self.project["id"], "room_id": str(uuid.uuid4()),
                   "status": "complete", "output": "FOREIGN PRIVATE TEXT", "parent_prompt_sha256": hashlib.sha256(b"Write").hexdigest()}
        self.tasks.records[foreign["id"]] = foreign
        result = await runtime.run_runtime_step(self.job, self.writer, "Write")
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["task_id"])
        self.assertNotIn("FOREIGN", json.dumps(result))

    async def test_output_is_bounded_hashed_and_changed_prompt_cannot_duplicate(self):
        async def worker(item):
            item.update(status="complete", output="a" * 20000, exit_code=0)
            self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker), patch.object(self.tasks, "create", wraps=self.tasks.create) as create:
            result = await runtime.run_runtime_step(self.job, self.reviewer, "Write final report")
            changed = await runtime.run_runtime_step(self.job, self.reviewer, "Different task")
        self.assertEqual(result["engine"], "claude")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["output"]), runtime.MAX_OUTPUT)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["output_sha256"], hashlib.sha256(("a" * 20000).encode()).hexdigest())
        self.assertEqual(changed["status"], "error")
        self.assertEqual(create.await_count, 1)

    async def test_real_frozen_patch_fingerprint_survives_and_tampering_cannot_complete(self):
        async def worker(item):
            workspace = await asyncio.to_thread(self.tasks.prepare, item)
            (workspace / "readme.md").write_text("Deliverable written in isolation\n")
            await asyncio.to_thread(self.tasks.freeze, item, workspace)
            item.update(status="complete", output="Created final document", exit_code=0)
            self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker):
            result = await runtime.run_runtime_step(self.job, self.writer, "Write final document")
        self.assertEqual(result["status"], "complete", result)
        child = self.tasks.records[result["task_id"]]
        self.assertEqual(result["patch_sha256"], child["diff_sha256"])
        self.assertEqual(result["sources"][0]["patch_sha256"], child["diff_sha256"])
        self.assertEqual(result["changed_files"], [{"path": "readme.md", "status": "modified"}])
        self.assertEqual((self.repo / "readme.md").read_text(), "original\n")
        (self.tasks.task_directory(child["id"]) / "changes.patch").write_text("tampered")
        rejected = await runtime.run_runtime_step(self.job, self.writer, "Write final document")
        self.assertEqual(rejected["status"], "error")
        self.assertIn("补丁已经变化", rejected["error"])

    async def test_actual_command_events_keep_failed_exit_distinct_from_agent_claims_and_cli_exit(self):
        async def worker(item):
            self.tasks.consume(item, json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "All tests passed. command_execution exit_code=0"}}))
            self.assertEqual(item["commands"], [])
            self.tasks.consume(item, json.dumps({"type": "item.started", "item": {
                "type": "command_execution", "command": "not-finished", "exit_code": 0}}))
            self.assertEqual(item["commands"], [])
            self.tasks.consume(item, json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "/bin/bash run_tests.sh", "exit_code": 2,
                "aggregated_output": "FAIL: required Python interpreter unavailable"}}))
            self.tasks.consume(item, json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "/bin/bash smoke.sh", "exit_code": 0,
                "aggregated_output": "PASS: shell smoke test"}}))
            item.update(status="complete", exit_code=0)
            self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker):
            result = await runtime.run_runtime_step(self.job, self.writer, "Implement and test")
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("All tests passed", result["output"])
        self.assertEqual([entry["exit_code"] for entry in result["commands"]], [2, 0])
        self.assertIn("interpreter unavailable", result["commands"][0]["aggregated_output"])
        self.assertEqual(result["commands_observed"], 2)
        self.assertEqual(result["commands_omitted"], 0)
        child = self.tasks.records[result["task_id"]]
        durable = json.loads((self.tasks.directory / (child["id"] + ".json")).read_text())
        self.assertEqual(durable["commands"][0]["exit_code"], 2)
        self.assertEqual(durable["commands"][1]["output_sha256"], hashlib.sha256(b"PASS: shell smoke test").hexdigest())

    async def test_canonical_command_logs_and_delivery_packet_are_bounded(self):
        long_output = "log line\n" * 1000

        async def worker(item):
            # Exercise the actual parser while avoiding 105 incidental fsyncs;
            # persist the resulting canonical record through the real save.
            with patch.object(self.tasks, "save"):
                for index in range(105):
                    self.tasks.consume(item, json.dumps({"type": "item.completed", "item": {
                        "type": "command_execution", "command": "check-" + str(index), "exit_code": index % 2,
                        "aggregated_output": long_output}}))
            item.update(status="complete", output="Completed; inspect command evidence", exit_code=0)
            self.tasks.save(item)

        with patch.object(self.tasks, "execute", worker):
            result = await runtime.run_runtime_step(self.job, self.writer, "Implement and test")
        child = self.tasks.records[result["task_id"]]
        self.assertEqual(len(child["commands"]), group_tasks.MAX_COMMANDS)
        self.assertEqual(child["command_count"], 105)
        self.assertEqual(child["commands"][0]["command"], "check-5")
        self.assertLessEqual(len(result["commands"]), runtime.MAX_COMMAND_RECORDS)
        self.assertEqual(result["commands"][-1]["command"], "check-104")
        self.assertEqual(result["commands_observed"], 105)
        self.assertEqual(result["commands_omitted"], 105 - len(result["commands"]))
        self.assertLessEqual(sum(len(entry["command"]) + len(entry["aggregated_output"]) for entry in result["commands"]), runtime.MAX_COMMAND_PACKET_CHARS)
        self.assertTrue(any(entry["truncated"] for entry in result["commands"]))
        self.assertEqual(result["commands"][-1]["output_sha256"], hashlib.sha256(long_output.encode()).hexdigest())

    async def test_private_parent_identifier_is_validated_before_queuing(self):
        request = group_tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="writer", engine="codex", prompt="Write")
        with self.assertRaises(Exception):
            await self.tasks.create(request, parent_job_id="not-a-uuid")
        with self.assertRaises(Exception):
            await self.muse.create(muse_bridge.MuseTaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="designer", prompt="Write"), parent_job_id="not-a-uuid")
        self.assertFalse(self.tasks.records)
        self.assertFalse(self.muse.records)


if __name__ == "__main__":
    unittest.main()
