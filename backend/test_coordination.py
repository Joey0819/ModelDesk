import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import coordination, group_delivery, workbench
from backend.harness.scheduler import run_graph, validate_graph


def plan(steps=None, requirements=None):
    return {"title": "共同交付", "requirements": requirements or [{"id": "body", "kind": "text", "description": "完整文章"}],
            "steps": steps or [{"id": "draft", "member_id": "writer", "instruction": "写出文章", "expected_output": "完整初稿", "depends_on": []},
                               {"id": "facts", "member_id": "reader", "instruction": "核查初稿", "expected_output": "具体纠正意见", "depends_on": ["draft"]}]}


def transport(remote=False):
    return Request({"type": "http", "client": ("127.0.0.1", 1), "headers": [(b"origin", b"http://localhost:5173")]
                    + ([(b"x-modeldesk-remote", b"phone")] if remote else [])})


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_graphs(self):
        for nodes in [[{"id": "a", "depends_on": ["b"]}], [{"id": "a", "depends_on": ["a"]}],
                      [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}], [{"id": "a"}, {"id": "a"}]]:
            with self.assertRaises(ValueError):
                validate_graph(nodes)

    async def test_parallel_branches_dependency_failure_and_reuse(self):
        nodes = [{"id": "saved", "status": "complete"}, {"id": "a", "status": "pending"}, {"id": "b", "status": "pending"},
                 {"id": "child", "status": "pending", "depends_on": ["a"]},
                 {"id": "last", "status": "pending", "depends_on": ["b", "saved"]}]
        both_started = asyncio.Event()
        seen = []
        async def execute(step, parents):
            seen.append(step["id"])
            if step["id"] in {"a", "b"}:
                if len(seen) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 1)
            step["status"] = "error" if step["id"] == "a" else "complete"
        await run_graph(nodes, execute, lambda: None)
        self.assertEqual(set(seen), {"a", "b", "last"})
        self.assertEqual(nodes[3]["status"], "blocked")
        self.assertEqual(nodes[4]["status"], "complete")

    async def test_cancel_drains_all_workers(self):
        started, stopped = [], []
        nodes = [{"id": str(i), "status": "pending"} for i in range(3)]
        async def execute(step, parents):
            started.append(step["id"])
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(step["id"])
        task = asyncio.create_task(run_graph(nodes, execute, lambda: None))
        while len(started) != 2:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(set(started), set(stopped))
        self.assertTrue(all(step["status"] == "cancelled" for step in nodes))


class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = workbench.JobManager(Path(self.tmp.name))
        room = str(uuid.uuid4())
        self.job = {"id": str(uuid.uuid4()), "project_id": None, "conversation_id": room, "group_room_id": room,
                    "mode": "group", "models": ["model-a", "model-b"], "prompt": "写一篇完整文章", "status": "running", "events": [],
                    "group_harness_version": 1, "group_delivery_mode": "auto", "group_broadcast": True,
                    "group_runtime_allowed": True, "group_native_allowed": True, "group_output_format": "text",
                    "group_recipient_member_ids": ["writer", "reader"], "group_team_snapshot": {"members": [
                        {"id": "writer", "kind": "model", "name": "作者", "model": "model-a", "role": "写作"},
                        {"id": "reader", "kind": "model", "name": "审阅者", "model": "model-b", "role": "核查"}]}}
        self.manager.jobs[self.job["id"]] = self.job
        self.manager.wakeups[self.job["id"]] = asyncio.Event()
        self.calls = []

    async def provider(self, job, model, system, prompt, **kwargs):
        self.calls.append((model, system, prompt))
        if "你是项目统筹者" in system:
            return json.dumps(plan(), ensure_ascii=False), False
        if "最终交付编辑" in system:
            return json.dumps({"title": "文章", "content": "# 最终文章\n\n真实正文，已经采用审阅意见。", "missing_outputs": []}), False
        if "你是交付审查者" in system:
            return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [{"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "正文完整"}], "revision_instructions": ""}), False
        if "核查初稿" in prompt:
            self.assertIn("INITIAL_OUTPUT", prompt)
            return "REVIEWED_OUTPUT", False
        return "INITIAL_OUTPUT", False

    async def run_delivery(self, provider=None):
        with patch.object(group_delivery, "model_output", side_effect=provider or self.provider):
            return await coordination.run(self.manager, copy.deepcopy(self.job))

    async def test_dependencies_final_artifact_and_persistent_evidence(self):
        result = await self.run_delivery()
        delivery = result["delivery"]
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual([item["status"] for item in delivery["steps"]], ["complete", "complete"])
        self.assertIn("REVIEWED_OUTPUT", next(prompt for _, system, prompt in self.calls if "最终交付编辑" in system))
        self.assertEqual(delivery["validation"]["status"], "passed")
        self.assertTrue(delivery["reviews"][0]["independent_model"])
        self.assertEqual(delivery["artifact"]["version"], 1)
        persisted = json.loads((Path(self.tmp.name) / (self.job["id"] + ".json")).read_text())
        self.assertEqual(persisted["result"]["delivery"]["task_spec"]["goal"], self.job["prompt"])
        self.assertEqual(coordination.load_inputs(self.manager, self.job)["prompt"], self.job["prompt"])

    async def test_one_bounded_revision_retains_both_versions_and_reviews(self):
        reviews = 0
        async def provider(job, model, system, prompt, **kwargs):
            nonlocal reviews
            if "你是交付审查者" in system:
                reviews += 1
                return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [{"requirement_id": "body", "met": reviews > 1, "evidence_status": "verified", "reason": "补充结尾"}],
                                   "revision_instructions": "补充结尾"}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(reviews, 2)
        self.assertEqual(len(delivery["versions"]), 2)
        self.assertEqual(delivery["artifact"]["version"], 2)
        self.assertEqual(delivery["status"], "complete")

    async def test_final_review_resolves_editor_questions_using_actual_evidence(self):
        async def provider(job, model, system, prompt, **kwargs):
            if "最终交付编辑" in system:
                return json.dumps({"title":"Article", "content":"Complete text", "missing_outputs":["Please check content"]}), False
            if "你是交付审查者" in system:
                self.assertIn("Please check content", prompt)
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["resolved_editor_questions"], ["Please check content"])
        self.assertEqual(len(delivery["versions"]), 2)
        self.assertEqual(len(delivery["reviews"]), 2)

    async def test_review_can_assign_exactly_one_confirmed_native_revision(self):
        member = {"id": "creator", "kind": "muse", "name": "Creator", "model": None, "role": "Respect brand colors"}
        self.job["group_team_snapshot"]["members"].append(member)
        self.job["group_recipient_member_ids"].append("creator")
        reviewed = 0
        async def provider(job, model, system, prompt, **kwargs):
            nonlocal reviewed
            if "你是项目统筹者" in system:
                return json.dumps(plan([{"id": "creative", "member_id": "creator", "instruction": "Create", "expected_output": "Actual result"}])), False
            if "你是交付审查者" in system:
                reviewed += 1
                return json.dumps({"goal_met": reviewed > 1, "missing_requirements": [] if reviewed > 1 else ["颜色不符合要求"],
                    "checks": [{"requirement_id": "body", "met": reviewed > 1, "evidence_status": "verified", "reason": "Color"}],
                    "runtime_revision": {"step_id": "creative", "instruction": "Change to requested colors"}}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        dispatched = []
        async def runtime(job, member, prompt, **kwargs):
            self.assertIn("Respect brand colors", prompt)
            dispatched.append(kwargs)
            return {"status": "complete", "task_id": str(uuid.uuid4()), "output": "Original" if len(dispatched) == 1 else "Fixed", "sources": []}
        with patch("backend.group_delivery_runtime.run_runtime_step", side_effect=runtime):
            delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(delivery["status"], "complete", delivery["limitations"])
        self.assertEqual(len(dispatched), 2)
        self.assertEqual(dispatched[1]["revision_of"], delivery["steps"][0]["task_id"])
        self.assertEqual(delivery["workflow"]["native_revision_count"], 1)
        self.assertEqual(len(delivery["versions"]), 2)
        self.assertEqual([s["id"] for s in coordination.active_steps(delivery)], ["native-revision"])
        from backend.harness.contracts import RuntimeRevision
        self.assertFalse(coordination.schedule_native_revision(delivery, RuntimeRevision(step_id="creative", instruction="Again")))

    async def test_reviewer_omission_and_invented_media_cannot_complete(self):
        async def provider(job, model, system, prompt, **kwargs):
            if "你是交付审查者" in system:
                return '{"checks":[{"requirement_id":"foreign","met":true,"reason":"ok"}]}', False
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(delivery["status"], "partial")
        self.assertTrue(delivery["content"])
        delivery["task_spec"]["requirements"].append({"id": "media-image", "kind": "image", "count": 1})
        delivery["media"] = [{"id": str(uuid.uuid4()), "sha256": "a" * 64, "kind": "image"}]
        self.assertFalse(coordination.actual_checks(self.job, delivery)[-1]["met"])

    async def test_unverified_evidence_retains_artifact_without_replaying_native(self):
        self.job["group_team_snapshot"]["members"].append(
            {"id": "creator", "kind": "muse", "name": "Creator", "model": None, "role": "Make the requested video"})
        self.job["group_recipient_member_ids"].append("creator")
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                return json.dumps(plan([{"id": "creative", "member_id": "creator", "instruction": "Create", "expected_output": "Actual result"}])), False
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": True, "missing_requirements": ["全片动作未核验"],
                    "checks": [{"requirement_id": "body", "met": True, "evidence_status": "unverified",
                                "reason": "三处抽帧符合要求，尚未验证全片连续动作。"}],
                    "revision_instructions": "再检查全片", "runtime_revision": {"step_id": "creative", "instruction": "Try again"}}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        runtime = AsyncMock(return_value={"status": "complete", "task_id": str(uuid.uuid4()), "output": "Actual result", "sources": []})
        with patch("backend.group_delivery_runtime.run_runtime_step", runtime):
            delivery = (await self.run_delivery(provider))["delivery"]
        runtime.assert_awaited_once()
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual(delivery["validation"]["status"], "needs_revision")
        self.assertFalse(delivery["reviews"][-1]["goal_met"])
        self.assertFalse(delivery["reviews"][-1]["checks"][0]["met"])
        self.assertEqual(len(delivery["versions"]), 1)
        self.assertTrue(delivery["content"])
        self.assertTrue(any("尚未验证" in item for item in delivery["limitations"]))

    def test_review_requires_coverage_and_preserves_honest_disclosure(self):
        from backend.harness.contracts import ReviewItem
        from pydantic import ValidationError
        for reason in ["尚未验证全片每一帧", "无法核实旁白", "Continuous motion has not been verified."]:
            with self.assertRaises(ValidationError):
                ReviewItem(requirement_id="motion", met=True, reason=reason)
        for status in ["limited", "unverified"]:
            self.assertFalse(ReviewItem(requirement_id="motion", met=True, reason="Sampled frames only", evidence_status=status).met)
        for reason in ["元数据确认时长6秒，无音频流", "No unverified items remain.", "报告如实说明全程匀速尚未验证"]:
            self.assertTrue(ReviewItem(requirement_id="report", met=True, reason=reason, evidence_status="verified").met)

    async def test_missing_review_coverage_requires_a_structured_repair(self):
        attempts = 0
        async def provider(job, model, system, prompt, **kwargs):
            nonlocal attempts
            raw, truncated = await self.provider(job, model, system, prompt, **kwargs)
            if "你是交付审查者" in system:
                attempts += 1
                if attempts == 1:
                    value = json.loads(raw)
                    for item in value["checks"]:
                        item.pop("evidence_status")
                    return json.dumps(value), False
                self.assertIn("evidence_status", prompt)
            return raw, truncated
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(attempts, 2)
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["format_diagnostics"][0]["stage"], "review")

    async def test_reused_video_needs_no_native_member_or_repeat_dispatch(self):
        self.job.update(group_output_format="auto", group_media_provider="muse")
        asset = {"id": str(uuid.uuid4()), "kind": "video", "sha256": "a" * 64}
        self.job["_task_context"] = {"reference_media": [asset]}
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                return json.dumps({**plan(requirements=[{"id": "video", "kind": "video", "count": 1, "description": "复用已有视频"}]),
                                   "reuse_media_ids": [asset["id"]]}), False
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [
                    {"requirement_id": "media-video", "met": True, "evidence_status": "verified", "reason": "已有视频已校验复用"}]}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        for with_muse in [False, True]:
            with self.subTest(with_muse=with_muse):
                if with_muse:
                    self.job["group_team_snapshot"]["members"].append(
                        {"id": "creator", "kind": "muse", "name": "Creator", "model": None, "role": "Create when requested"})
                    self.job["group_recipient_member_ids"].append("creator")
                with patch("backend.task_context.reuse_media", return_value=[asset]), \
                     patch("backend.delivery_harness.produce_media", AsyncMock()), \
                     patch.object(coordination, "actual_checks", return_value=[{"requirement_id": "media-video", "met": True, "reason": "Verified"}]), \
                     patch("backend.group_delivery_runtime.run_runtime_step", AsyncMock()) as runtime:
                    delivery = (await self.run_delivery(provider))["delivery"]
                runtime.assert_not_awaited()
                self.assertEqual(delivery["status"], "complete", delivery["limitations"])
                self.assertFalse(any(step["kind"] == "muse" for step in delivery["steps"]))

    async def test_original_goal_omission_cannot_pass_all_planned_checks(self):
        async def provider(job, model, system, prompt, **kwargs):
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": False, "missing_requirements": ["用户要求的对照案例未在计划中覆盖"],
                    "checks": [{"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "正文存在"}], "revision_instructions": ""}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual(delivery["validation"]["status"], "needs_revision")
        self.assertIn("用户要求的对照案例未在计划中覆盖", delivery["limitations"])

    def test_semantic_media_requirements_respect_explicit_controls(self):
        raw = json.dumps(plan(requirements=[{"id": "poster", "kind": "image", "count": 1, "description": "一张可下载海报"}]))
        for mode, expected in [("auto", 1), ("text", 0)]:
            contract = {"images": 0, "videos": 0, "provider": "gemini"}
            parsed = coordination.parse_plan(raw, coordination.roster(self.job), contract, mode)
            self.assertEqual(contract["images"], expected)
            self.assertEqual(contract["provider"], "gemini")
            self.assertEqual(any(item["kind"] == "image" for item in parsed["requirements"]), bool(expected))
        contract = {"images": 2, "videos": 0, "provider": "gemini"}
        coordination.parse_plan(raw, coordination.roster(self.job), contract, "auto")
        self.assertEqual(contract["images"], 2)

    async def test_format_repair_corrects_plan_and_incomplete_review_once(self):
        calls = {"plan": 0, "review": 0}
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                calls["plan"] += 1
                if calls["plan"] == 1:
                    invalid = plan()
                    invalid["steps"][0]["output_kind"] = "image"
                    return json.dumps(invalid), False
            if "你是交付审查者" in system:
                calls["review"] += 1
                if calls["review"] == 1:
                    return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [{"requirement_id": "invented", "met": True, "evidence_status": "verified", "reason": "ok"}]}), False
                self.assertIn('checks 必须覆盖且只包含', prompt)
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = (await self.run_delivery(provider))["delivery"]
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(calls, {"plan": 2, "review": 2})
        self.assertEqual([item["stage"] for item in delivery["format_diagnostics"]], ["plan", "review"])

    async def test_invalid_plan_never_dispatches(self):
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=(json.dumps(plan([
            {"id": "run", "member_id": "foreign", "instruction": "do", "expected_output": "x"}])), False))), \
             patch("backend.group_delivery_runtime.run_runtime_step", AsyncMock()) as runtime:
            result = await coordination.run(self.manager, self.job)
        runtime.assert_not_awaited()
        self.assertEqual(result["delivery"]["status"], "blocked")

    async def test_continue_reuses_completed_steps_and_rejects_duplicate_click(self):
        failures = 0
        async def provider(job, model, system, prompt, **kwargs):
            nonlocal failures
            if "核查初稿" in prompt and "你是 审阅者" in system:
                failures += 1
                if failures == 1:
                    raise RuntimeError("temporary failure")
            return await self.provider(job, model, system, prompt, **kwargs)
        await self.run_delivery(provider)
        self.job["status"] = "complete"
        delivery = self.job["result"]["delivery"]
        first_attempt = copy.deepcopy(delivery["steps"][0]["attempts"])
        request = coordination.ContinueRequest(expected_revision=delivery["workflow"]["revision"])
        room = {"team_snapshot": self.job["group_team_snapshot"]}
        with patch("backend.groups.store.get", return_value=room), patch.object(group_delivery, "model_output", side_effect=provider):
            await coordination.continue_run(self.manager, self.job["id"], self.job["group_room_id"], None, request, transport())
            with self.assertRaises(HTTPException) as duplicate:
                await coordination.continue_run(self.manager, self.job["id"], self.job["group_room_id"], None, request, transport())
            self.assertEqual(duplicate.exception.status_code, 409)
            await self.manager.tasks[self.job["id"]]
        delivery = self.job["result"]["delivery"]
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["steps"][0]["attempts"], first_attempt)
        self.assertEqual(len(delivery["steps"][1]["attempts"]), 2)

    async def test_context_tampering_blocks_continue(self):
        await self.run_delivery()
        path = coordination.input_store(self.manager).directory / (self.job["id"] + ".json")
        value = json.loads(path.read_text())
        value["prompt"] = "different input"
        path.write_text(json.dumps(value))
        with self.assertRaises(HTTPException) as error:
            coordination.load_inputs(self.manager, self.job)
        self.assertEqual(error.exception.status_code, 409)

    async def test_restart_preserves_completed_output_and_bounds_unobserved_time(self):
        await self.run_delivery()
        delivery = self.job["result"]["delivery"]
        delivery["status"] = "working"
        delivery["workflow"]["last_active_at"] = "2020-01-01T00:00:00+00:00"
        delivery["steps"][1]["status"] = "running"
        delivery["steps"][1]["attempts"][-1]["status"] = "running"
        self.job["status"] = "running"
        self.manager.save(self.job)
        restored = workbench.JobManager(Path(self.tmp.name))
        restored.recover()
        actual = restored.jobs[self.job["id"]]
        self.assertEqual(actual["status"], "interrupted")
        saved = actual["result"]["delivery"]
        self.assertEqual(saved["steps"][0]["status"], "complete")
        self.assertEqual(saved["steps"][0]["output"], "INITIAL_OUTPUT")
        self.assertEqual(saved["steps"][1]["attempts"][-1]["status"], "interrupted")
        self.assertEqual(saved["workflow"]["elapsed_seconds"], 1200)
        self.assertEqual(saved["artifact"], delivery["artifact"])

    async def test_restart_keeps_media_budget_and_does_not_reset_spent_time(self):
        await self.run_delivery()
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        cases = [
            ({"images": 9, "videos": 0}, 1600, recent, 2220, False),
            ({"images": 0, "videos": 1}, 2500, recent, 3600, False),
            ({"images": 9, "videos": 0}, 1600, "2020-01-01T00:00:00+00:00", 2220, True),
            ({"images": 9, "videos": 0}, 1600, None, 2220, True),
        ]
        for media, spent, last_active, budget, exhausted in cases:
            with self.subTest(media=media, last_active=last_active):
                job = copy.deepcopy(self.job)
                job["status"] = "running"
                delivery = job["result"]["delivery"]
                delivery["status"] = "working"
                delivery["task_spec"]["media"] = media
                delivery["workflow"].update(elapsed_seconds=spent, last_active_at=last_active)
                self.manager.save(job)
                restored = workbench.JobManager(Path(self.tmp.name))
                restored.recover()
                saved = restored.jobs[job["id"]]["result"]["delivery"]
                self.assertEqual(saved["workflow"]["budget_seconds"], budget)
                self.assertGreaterEqual(saved["workflow"]["elapsed_seconds"], spent)
                if exhausted:
                    self.assertEqual(saved["workflow"]["elapsed_seconds"], budget)
                else:
                    self.assertLess(saved["workflow"]["elapsed_seconds"], budget)
                self.assertEqual(saved["steps"][0]["output"], "INITIAL_OUTPUT")

    async def test_continue_scope_stale_revision_budget_and_roster_guards(self):
        await self.run_delivery()
        self.job["status"] = "complete"
        delivery = self.job["result"]["delivery"]
        delivery["status"] = "partial"
        request = coordination.ContinueRequest(expected_revision=delivery["workflow"]["revision"])
        room = {"team_snapshot": self.job["group_team_snapshot"]}
        cases = [("group_room_id", "different", 404), ("project_id", "different", 404),
                 ("status", "running", 409), ("group_team_snapshot", {"members": []}, 409)]
        with patch("backend.groups.store.get", return_value=room):
            for key, changed, status in cases:
                original = self.job[key]
                self.job[key] = changed
                with self.assertRaises(HTTPException) as error:
                    await coordination.continue_run(self.manager, self.job["id"], room_id=self.job["conversation_id"],
                                                    project_id=None, request=request, transport=transport())
                self.assertEqual(error.exception.status_code, status)
                self.job[key] = original
            for key, value in [("revision", request.expected_revision + 1), ("resume_count", 2), ("elapsed_seconds", 1200)]:
                original = delivery["workflow"][key]
                delivery["workflow"][key] = value
                with self.assertRaises(HTTPException) as error:
                    await coordination.continue_run(self.manager, self.job["id"], self.job["group_room_id"], None, request, transport())
                self.assertEqual(error.exception.status_code, 409)
                delivery["workflow"][key] = original
        self.assertFalse(self.manager.tasks)

    async def test_remote_continue_never_grants_native_permission(self):
        await self.run_delivery()
        self.job["status"] = "complete"
        delivery = self.job["result"]["delivery"]
        delivery["status"] = "partial"
        request = coordination.ContinueRequest(expected_revision=delivery["workflow"]["revision"])
        with patch("backend.groups.store.get", return_value={"team_snapshot": self.job["group_team_snapshot"]}), \
             patch.object(coordination, "run", AsyncMock(return_value=self.job["result"])) as resumed:
            await coordination.continue_run(self.manager, self.job["id"], self.job["group_room_id"], None, request, transport(remote=True))
            await self.manager.tasks[self.job["id"]]
        self.assertFalse(resumed.call_args.args[1]["group_native_allowed"])

    def test_targeted_members_and_runtime_uniqueness(self):
        self.job.update(group_broadcast=False, group_recipient_member_ids=["reader"], group_coordinator_model="outside")
        self.assertEqual([item["id"] for item in coordination.roster(self.job)], ["reader"])
        members = [{"id": "muse", "name": "Muse", "kind": "muse"}]
        steps = [{"id": str_id, "member_id": "muse", "instruction": "create", "expected_output": "file"} for str_id in ["a", "b"]]
        with self.assertRaises(ValueError):
            coordination.parse_plan(json.dumps(plan(steps)), members, {"images": 0, "videos": 0})


if __name__ == "__main__":
    unittest.main()
