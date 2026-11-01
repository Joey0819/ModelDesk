"""A review-only answer must not erase the original draft at the next handoff."""
import copy
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from backend import coordination, delivery_media, group_delivery
from backend import test_coordination as fixtures


class DependencyLineageTests(unittest.TestCase):
    def test_diamond_is_deduplicated_ordered_and_excludes_siblings(self):
        nodes = [
            {"id": "unrelated", "status": "complete", "output": "UNRELATED"},
            {"id": "draft", "status": "complete"},
            {"id": "review", "status": "complete", "depends_on": ["draft"]},
            {"id": "layout", "status": "complete", "depends_on": ["draft"]},
            {"id": "create", "status": "pending", "depends_on": ["review", "layout"]},
        ]
        expected = ["draft", "review", "layout"]
        self.assertEqual([item["id"] for item in coordination.dependency_lineage(nodes[-1], nodes)], expected)
        self.assertEqual([item["id"] for item in coordination.dependency_lineage(nodes[-1], list(reversed(nodes)))], expected)

    def test_incomplete_ancestor_cannot_be_disguised_by_complete_review(self):
        nodes = [{"id": "draft", "status": "error"},
                 {"id": "review", "status": "complete", "depends_on": ["draft"]}]
        with self.assertRaisesRegex(ValueError, "前置成果尚未完成"):
            coordination.dependency_lineage({"depends_on": ["review"]}, nodes)


class HandoffContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CoordinationTests.setUp

    def prepare_job(self):
        self.job["group_team_snapshot"]["members"].append(
            {"id": "creator", "kind": "muse", "name": "导演", "role": "按定稿完成作品"})
        self.job["group_recipient_member_ids"].append("creator")
        self.job["_task_context"] = {"task_id": "task-original", "sha256": "memory-snapshot",
            "requests": [{"prompt": "写出完整故事"}, {"prompt": "保留结尾"}]}
        self.draft = "FULL_DRAFT_START\n" + "正文" * 3000 + "\nFINAL_SCENE_SENTINEL"
        self.seen = []

    async def provider(self, job, model, system, prompt, **kwargs):
        self.seen.append((system, prompt))
        if "你是项目统筹者" in system:
            return json.dumps(fixtures.plan([
                {"id": "draft", "member_id": "writer", "instruction": "WRITE_DRAFT", "expected_output": "完整故事"},
                {"id": "unrelated", "member_id": "writer", "instruction": "SIDE_BRANCH", "expected_output": "独立备选"},
                {"id": "review", "member_id": "reader", "instruction": "REVIEW_DRAFT", "expected_output": "审核意见", "depends_on": ["draft"]},
                {"id": "create", "member_id": "creator", "instruction": "CREATE_FROM_REVIEW", "expected_output": "完整作品", "depends_on": ["review"]},
            ], [{"id": "body", "kind": "text", "description": "GLOBAL_REQUIREMENT_ENDING", "count": 1}])), False
        if "最终交付编辑" in system:
            self.assertIn("FINAL_SCENE_SENTINEL", prompt)
            return json.dumps({"title": "完整作品", "content": "FINAL_ASSEMBLED_BODY", "missing_outputs": []}), False
        if "你是交付审查者" in system:
            self.assertIn("FINAL_SCENE_SENTINEL", prompt)
            return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [
                {"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "正文完整"}]}), False
        if "你是 审阅者" in system:
            self.assertIn("FINAL_SCENE_SENTINEL", prompt)
            return "APPROVED: retain the original ending and correct the title.", False
        if "SIDE_BRANCH" in prompt:
            return "UNRELATED_PRIVATE_BRANCH", False
        return self.draft, False

    async def test_three_role_chain_retains_original_and_full_final_evidence(self):
        self.prepare_job()
        runtime = AsyncMock(return_value={"status": "complete", "task_id": "native-child", "output": "Created final work", "sources": []})
        with patch.object(group_delivery, "model_output", side_effect=self.provider), \
             patch("backend.group_delivery_runtime.run_runtime_step", runtime), \
             patch("backend.delivery_harness.produce_media", new=AsyncMock()):
            delivery = (await coordination.run(self.manager, copy.deepcopy(self.job)))["delivery"]
        self.assertEqual(delivery["status"], "complete", delivery["limitations"])
        runtime.assert_awaited_once()
        sent = runtime.await_args.args[2]
        self.assertIn(json.dumps(self.draft, ensure_ascii=False), sent)
        self.assertIn("APPROVED", sent)
        self.assertIn("GLOBAL_REQUIREMENT_ENDING", sent)
        self.assertNotIn("UNRELATED_PRIVATE_BRANCH", sent)
        for system, prompt in self.seen:
            self.assertIn("保留结尾", prompt, system)
        native = delivery["steps"][-1]
        receipt = native["input_context"]
        self.assertEqual(receipt["context_sha256"], "memory-snapshot")
        self.assertEqual(receipt["user_request_count"], 2)
        self.assertEqual(receipt["direct_dependency_ids"], ["review"])
        self.assertEqual([(s["step_id"], s["direct"]) for s in receipt["sources"]], [("draft", False), ("review", True)])
        self.assertEqual(receipt["sources"][0]["output_sha256"], hashlib.sha256(self.draft.encode()).hexdigest())
        self.assertEqual(receipt["prompt_sha256"], hashlib.sha256(sent.encode()).hexdigest())
        self.assertEqual(native["handoff"]["sources"], receipt["sources"])
        self.assertTrue(all(step.get("input_context") for step in delivery["steps"]))
        saved = json.loads((self.manager.directory / (self.job["id"] + ".json")).read_text())
        self.assertEqual(saved["result"]["delivery"]["steps"][-1]["input_context"], receipt)

    async def test_resume_keeps_exact_dispatch_and_does_not_repeat_predecessors(self):
        self.prepare_job()
        sent = []
        async def runtime(job, member, prompt, **options):
            sent.append(prompt)
            if len(sent) == 1:
                raise RuntimeError("Temporary child status unavailable")
            return {"status": "complete", "task_id": "same-native-child", "output": "Created final work", "sources": []}
        with patch.object(group_delivery, "model_output", side_effect=self.provider), \
             patch("backend.group_delivery_runtime.run_runtime_step", side_effect=runtime), \
             patch("backend.delivery_harness.produce_media", new=AsyncMock()):
            delivery = (await coordination.run(self.manager, copy.deepcopy(self.job)))["delivery"]
            self.assertEqual(delivery["steps"][-1]["status"], "error")
            before = copy.deepcopy(delivery["steps"][-1]["input_context"])
            # The continue endpoint resets only unfinished steps to pending;
            # the child adapter reattaches by the persisted exact dispatch.
            delivery["steps"][-1]["status"] = "pending"
            delivery = (await coordination.run(self.manager, copy.deepcopy(self.job), continuing=True))["delivery"]
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(delivery["steps"][-1]["input_context"], before)
        self.assertEqual([len(step["attempts"]) for step in delivery["steps"]], [1, 1, 1, 2])
        self.assertEqual(delivery["status"], "complete")

    async def test_prepared_task_memory_is_not_appended_twice(self):
        from backend.task_context import quote_task_context
        self.prepare_job()
        memory = quote_task_context(self.job["_task_context"])
        self.job["prompt"] += "\n" + memory
        runtime = AsyncMock(return_value={"status": "complete", "task_id": "child", "output": "Done", "sources": []})
        with patch.object(group_delivery, "model_output", side_effect=self.provider), \
             patch("backend.group_delivery_runtime.run_runtime_step", runtime), \
             patch("backend.delivery_harness.produce_media", new=AsyncMock()):
            await coordination.run(self.manager, copy.deepcopy(self.job))
        # The goal in this fixture contains the packet too; each background
        # block must nevertheless contain it only once, not twice.
        planned = next(prompt for system, prompt in self.seen if "你是项目统筹者" in system)
        background = planned.split("已选背景资料：\n", 1)[1]
        self.assertEqual(background.count(memory), 1)

    def test_native_revision_keeps_original_instruction_tail(self):
        from backend.harness.contracts import RuntimeRevision
        instruction = "x" * 3000 + "ORIGINAL_REQUIRED_ENDING"
        delivery = {"steps": [{"id": "create", "member_id": "creator", "member_name": "导演", "kind": "muse",
            "status": "complete", "task_id": "child", "instruction": instruction, "expected_output": "完整作品"}],
            "workflow": {}}
        self.assertTrue(coordination.schedule_native_revision(delivery, RuntimeRevision(step_id="create", instruction="Correct title")))
        self.assertIn(instruction, delivery["steps"][-1]["instruction"])


class UpstreamImageTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CoordinationTests.setUp

    async def image_chain(self, *, tools=False, corrupt=False):
        self.job["group_output_format"] = "auto"
        self.job["prompt"] += "\nCURRENT_PROJECT_STYLE: navy cover and no text"
        self.job["group_team_snapshot"]["members"].append(
            {"id": "artist", "kind": "image", "name": "绘图", "model": "gpt-image-2.5", "role": "绘制封面"})
        self.job["group_recipient_member_ids"].append("artist")
        if tools:
            self.job.update(tools=["fixture"], _runtime={"token": "fixture"})
        store = delivery_media.DeliveryMediaStore(Path(self.tmp.name).resolve() / "media")
        png = io.BytesIO()
        Image.new("RGB", (16, 16), "navy").save(png, format="PNG")
        captured, assets = [], []
        async def execute_images(job, member, step, prompt, delivery, changed):
            self.assertIn("CURRENT_PROJECT_STYLE", prompt)
            asset = store.save_bytes(job, png.getvalue(), kind="image", provider="fixture", slot_id="cover")
            assets.append(asset)
            manifest = {**asset, "sha256": "bad-digest"} if corrupt else asset
            step.update(status="complete", output=json.dumps({"images": [manifest]}), media=[manifest])
            delivery["media"].append(manifest)
            changed()
        def assert_pixels(parts):
            images = [part for part in parts if part.get("type") == "image_url"]
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            captured.append(images)
        async def tool_response(job, model, messages):
            assert_pixels(messages[-1]["content"])
            return {"text": "Image review from actual pixels", "incomplete": False}
        self.manager.tool_response = AsyncMock(side_effect=tool_response)
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                return json.dumps(fixtures.plan([
                    {"id": "draw", "member_id": "artist", "instruction": "Draw cover", "expected_output": "PNG", "output_kind": "image", "output_count": 1},
                    {"id": "review", "member_id": "reader", "instruction": "Review actual cover", "expected_output": "Visual review", "depends_on": ["draw"]},
                    {"id": "layout", "member_id": "writer", "instruction": "Apply visual review", "expected_output": "Article", "depends_on": ["review"]},
                ], [{"id": "picture", "kind": "image", "description": "One cover", "count": 1}])), False
            if "最终交付编辑" in system:
                return json.dumps({"title": "Cover", "content": "Actual cover delivered", "missing_outputs": []}), False
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": not corrupt, "missing_requirements": [], "checks": [
                    {"requirement_id": "media-image", "met": not corrupt, "evidence_status": "verified", "reason": "Actual file"}]}), False
            assert_pixels(kwargs["image_parts"])
            self.assertIn("随请求提供的前置图片画面", prompt)
            return "Image review from actual pixels", False
        with patch.object(group_delivery, "model_output", side_effect=provider), \
             patch("backend.role_images.execute_images", side_effect=execute_images), \
             patch("backend.delivery_harness.produce_media", new=AsyncMock()), \
             patch.object(delivery_media, "store", store), \
             patch("backend.super_platform.runtime_context", return_value={}):
            delivery = (await coordination.run(self.manager, copy.deepcopy(self.job)))["delivery"]
        return delivery, captured, assets

    async def test_downstream_models_receive_verified_ancestor_pixels(self):
        delivery, captured, assets = await self.image_chain()
        self.assertEqual(delivery["status"], "complete", delivery["limitations"])
        self.assertEqual(len(captured), 2)
        for step in delivery["steps"][1:]:
            self.assertEqual(step["input_context"]["image_input_asset_ids"], [assets[0]["id"]])
            self.assertEqual(step["input_context"]["image_input_count"], 1)
            self.assertEqual(step["input_context"]["native_reference_asset_ids"], [])
        self.assertEqual(delivery["steps"][0]["input_context"]["image_input_asset_ids"], [])

    async def test_tool_enabled_model_receives_same_verified_pixels(self):
        delivery, captured, _ = await self.image_chain(tools=True)
        self.assertEqual(delivery["status"], "complete", delivery["limitations"])
        self.assertEqual(len(captured), 2)
        self.assertEqual(self.manager.tool_response.await_count, 2)

    async def test_bad_image_hash_blocks_model_and_descendant(self):
        delivery, captured, _ = await self.image_chain(corrupt=True)
        self.assertEqual(captured, [])
        self.assertEqual(delivery["steps"][1]["status"], "error")
        self.assertIn("未通过文件与画面校验", delivery["steps"][1]["error"])
        self.assertEqual(delivery["steps"][2]["status"], "blocked")

    async def test_combined_image_limit_fails_before_media_read(self):
        with patch("backend.attachment_io.image_content_parts", return_value=[{"type": "image_url"}] * 8), \
             patch("backend.harness.media_evidence.media_evidence", new=AsyncMock()) as inspect:
            with self.assertRaisesRegex(ValueError, "超过 8 张"):
                await coordination.model_step_images({}, [{"id": "ninth", "kind": "image"}])
            inspect.assert_not_awaited()
