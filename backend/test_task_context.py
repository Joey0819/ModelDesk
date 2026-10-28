import copy
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from fastapi import HTTPException

from backend import coordination, delivery_media, group_delivery, role_images, task_context, task_handoff, workbench


def turn(index, prompt, **extra):
    return dict(id=str(uuid.uuid4()), created_at=f"2026-10-28T01:{index:02d}:00Z", mode="group",
                project_id=None, conversation_id="room", group_room_id="room", status="complete",
                prompt=prompt, **extra)


class TaskMemoryTests(unittest.TestCase):
    def test_long_outputs_failures_and_turn_limit_do_not_evict_requests(self):
        initial = turn(0, "原始要求：七章，20条实践，不编造经历", group_context_mode="fresh",
            result={"delivery": {"title": "文章", "status": "complete", "content": "完整底稿" * 4000, "media": []}})
        changes = [turn(i, "保留全部正文，只补图片 " + str(i)) for i in range(1, 15)]
        changes[2]["status"] = "error"
        current = turn(20, "改为蓝色封面", context_turns=1)
        foreign = turn(19, "OTHER_ROOM_SECRET"); foreign["group_room_id"] = "another"
        jobs = {j["id"]: j for j in [initial, *changes, foreign, current]}
        context = task_context.build_task_context(jobs, current)
        self.assertEqual(len(context["requests"]), 16)
        self.assertEqual(context["task_id"], initial["id"])
        self.assertEqual(context["artifact"]["content"], initial["result"]["delivery"]["content"])
        self.assertNotIn("OTHER_ROOM_SECRET", task_context.quote_task_context(context))
        self.assertIn("只补图片 3", task_context.quote_task_context(context))
        self.assertNotIn("content", task_context.context_summary(context)["artifact"])
        with tempfile.TemporaryDirectory() as tmp:
            manager = workbench.JobManager(Path(tmp))
            for job in jobs.values():
                job["events"] = []
                manager.jobs[job["id"]] = job
                manager.save(job)
            recovered = workbench.JobManager(Path(tmp)); recovered.recover()
            self.assertEqual(context, task_context.build_task_context(recovered.jobs, current))

    def test_new_task_boundary_scope_and_current_correction(self):
        old = turn(0, "OLD_TOPIC")
        root = turn(1, "NEW_TOPIC 90秒", group_context_mode="fresh")
        changed = turn(2, "改成60秒")
        current = turn(3, "继续")
        jobs = {j["id"]: j for j in [old, root, changed]}
        context = task_context.build_task_context(jobs, current)
        self.assertEqual([item["prompt"] for item in context["requests"]], ["NEW_TOPIC 90秒", "改成60秒", "继续"])
        current["group_context_mode"] = "fresh"
        self.assertEqual(len(task_context.build_task_context(jobs, current)["requests"]), 1)
        current["group_context_mode"] = "continue"; current["project_id"] = str(uuid.uuid4())
        self.assertEqual(len(task_context.build_task_context(jobs, current)["requests"]), 1)

    def test_oversize_requirements_fail_explicitly_instead_of_forgetting(self):
        job = turn(1, "原始要求" * 16000)
        with self.assertRaisesRegex(HTTPException, "60000"):
            task_context.build_task_context({}, job)

    def test_partial_edit_retains_last_complete_baseline(self):
        old = turn(0, 'WRITE', result={'delivery': {'status': 'complete', 'content': 'FULL_BODY'}})
        failed = turn(1, 'EDIT', result={'delivery': {'status': 'partial', 'content': 'INCOMPLETE_EDIT'}})
        context = task_context.build_task_context({old['id']: old, failed['id']: failed}, turn(2, 'CONTINUE'))
        self.assertEqual(context['artifact']['content'], 'INCOMPLETE_EDIT')
        self.assertEqual(context['baseline_artifact']['content'], 'FULL_BODY')
        self.assertNotIn('content', task_context.context_summary(context)['baseline_artifact'])

    def test_media_reuse_is_exact_scoped_and_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = delivery_media.DeliveryMediaStore(Path(tmp).resolve() / "media")
            raw = io.BytesIO(); Image.new("RGB", (8, 8), "blue").save(raw, format="PNG")
            old, new = turn(0, "cover"), turn(1, "more pictures")
            source = store.save_bytes(old, raw.getvalue(), kind="image", provider="fixture", slot_id="cover")
            context = {"reference_media": [source]}; delivery = {"media": []}
            with patch.object(delivery_media, "store", store):
                copied = task_context.reuse_media(new, [source["id"]], context, {old["id"]: old}, delivery)
                self.assertEqual(copied[0]["sha256"], source["sha256"])
                self.assertEqual(copied[0]["job_id"], new["id"])
                self.assertEqual(task_context.reuse_media(new, [source["id"]], context, {old["id"]: old}, delivery), copied)
                with self.assertRaises(ValueError):
                    task_context.reuse_media(new, ["unknown"], context, {old["id"]: old}, delivery)
                old["group_room_id"] = "foreign"
                with self.assertRaises(ValueError):
                    task_context.reuse_media(new, [source["id"]], context, {old["id"]: old}, delivery)


class HandoffAndBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_preserves_exact_user_changes_and_freezes_dispatch(self):
        member = {"id": "m", "name": "Muse", "role": "导演"}
        step = {"kind": "muse", "instruction": "实际生成60秒视频", "expected_output": "MP4"}
        job = {"_task_context": {"sha256": "context-hash", "requests": [{"prompt": "原始主题是纸船"}, {"prompt": "改成60秒无声"}]}}
        delivery = {"model": "ruler", "task_spec": {"goal": "继续"}}
        provider = AsyncMock(return_value=("已确认脚本与真实素材", False))
        with patch.object(group_delivery, "model_output", provider):
            result = await task_handoff.prepare_handoff(job, member, step, "very long background " * 2000, delivery, lambda: None)
            self.assertIn("原始主题是纸船", result); self.assertIn("改成60秒无声", result)
            self.assertLessEqual(len(result), 22000)
            self.assertTrue(step["handoff"]["background_compacted"])
            step["dispatch_prompt"] = result
            self.assertEqual(await task_handoff.prepare_handoff(job, member, step, "different", delivery, lambda: None), result)
            self.assertEqual(provider.await_count, 1)

    async def test_seven_distinct_slots_retry_only_missing_images(self):
        member = {"id": "artist", "name": "绘图者", "model": "gpt-image-2.5"}
        step = {"id": "chapters", "image_count": 7}
        delivery = {"model": "ruler", "media": [], "media_tasks": []}
        raw = json.dumps({"images": [{"caption": f"第{i}章", "prompt": f"chapter-{i} 独立画面"} for i in range(1, 8)]})
        calls = []
        async def generate(job, prompt, **options):
            calls.append(options["slot_id"])
            if len(calls) == 3:
                raise RuntimeError("暂时失败")
            return {"id": options["slot_id"], "kind": "image", "sha256": options["slot_id"], "provider": "fixture", **options}
        def read(job, identifier):
            return next(item for item in delivery["media"] if item["id"] == identifier), b"bytes"
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=(raw, False))) as briefing, \
             patch.object(role_images, "generate_image", side_effect=generate), patch.object(role_images.store, "read", side_effect=read):
            with self.assertRaises(RuntimeError):
                await role_images.execute_images({}, member, step, "每章一图", delivery, lambda: None)
            self.assertEqual(len(delivery["media"]), 2)
            await role_images.execute_images({}, member, step, "每章一图", delivery, lambda: None)
            self.assertEqual(len(delivery["media"]), 7)
            self.assertEqual(calls.count("role-chapters-1"), 1)
            self.assertEqual(calls.count("role-chapters-2"), 1)
            self.assertEqual(calls.count("role-chapters-3"), 2)
            self.assertEqual(briefing.await_count, 1)
            self.assertEqual(len({item["caption"] for item in delivery["media"]}), 7)

    def test_default_count_never_rewrites_seven_chapters_and_cover_reuse(self):
        members = [{"id": "artist", "name": "绘图者", "kind": "image", "model": "gpt-image-2.5"}]
        raw = {"title": "七章文章", "requirements": [{"id": "chapters", "kind": "image", "count": 7, "description": "每章一图"},
            {"id": "cover", "kind": "image", "count": 1, "description": "保留封面"}],
            "reuse_media_ids": ["cover"], "steps": [{"id": "draw", "member_id": "artist", "instruction": "绘制七章", "expected_output": "七张图片", "output_kind": "image"}]}
        contract = {"images": 2, "videos": 0, "provider": "auto"}
        parsed = coordination.parse_plan(json.dumps(raw), members, contract, "auto", "每章一张图，保留封面", [{"id": "cover", "kind": "image"}])
        self.assertEqual(contract["images"], 8)
        self.assertEqual(parsed["steps"][0]["image_count"], 7)
        before = copy.deepcopy(contract)
        raw["requirements"][0]["count"] = 20
        with self.assertRaises(ValueError):
            coordination.parse_plan(json.dumps(raw), members, contract, "auto", "继续", [{"id": "cover", "kind": "image"}])
        self.assertEqual(before, contract)
