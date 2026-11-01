"""Role-specific file quotas must survive planning and native handoff."""
import copy
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import coordination, delivery_harness


class MediaAllocationTests(unittest.TestCase):
    def parse(self, counts, total, *, kind="image", reused=0):
        members = [{"id": f"member-{i}", "name": f"Creator {i}",
                    "kind": "image" if kind == "image" else "muse", "model": f"image-{i}"}
                   for i in range(len(counts))]
        steps = [{"id": f"create-{i}", "member_id": member["id"], "output_kind": kind,
                  "instruction": f"Create part {i}", "expected_output": "Independent files",
                  **({"output_count": count} if count is not None else {})}
                 for i, (member, count) in enumerate(zip(members, counts))]
        refs = [{"id": f"saved-{i}", "kind": kind} for i in range(reused)]
        raw = {"title": "Deliver files", "requirements": [
            {"id": "files", "kind": kind, "count": total, "description": "All requested files"}],
            "steps": steps, "reuse_media_ids": [item["id"] for item in refs]}
        contract = {"images": 0, "videos": 0, "provider": "auto"}
        parsed = coordination.parse_plan(json.dumps(raw), members, contract, "auto", reference_media=refs)
        return parsed, contract, members

    def test_explicit_role_counts_are_preserved_for_images_and_videos(self):
        for kind in ["image", "video"]:
            with self.subTest(kind=kind):
                parsed, contract, _ = self.parse([2, 2], 4, kind=kind)
                self.assertEqual([step[kind + "_count"] for step in parsed["steps"]], [2, 2])
                self.assertEqual(contract[kind + "s"], 4)

    def test_only_unspecified_roles_receive_the_remainder(self):
        parsed, _, _ = self.parse([2, None], 5)
        self.assertEqual([step["image_count"] for step in parsed["steps"]], [2, 3])

    def test_legacy_count_free_plans_keep_their_existing_allocation(self):
        parsed, _, _ = self.parse([None, None], 4)
        self.assertEqual([step["image_count"] for step in parsed["steps"]], [3, 1])

    def test_reused_files_are_not_assigned_again(self):
        for kind in ["image", "video"]:
            with self.subTest(kind=kind):
                parsed, contract, _ = self.parse([2, 2], 5, kind=kind, reused=1)
                self.assertEqual([step[kind + "_count"] for step in parsed["steps"]], [2, 2])
                self.assertEqual(contract[kind + "s"], 5)

    def test_inconsistent_counts_cannot_lower_the_required_total(self):
        for counts in [[1, 1], [3, 2], [4, None]]:
            with self.subTest(counts=counts), self.assertRaisesRegex(ValueError, "output_count"):
                self.parse(counts, 4)

    def test_output_count_is_bounded_integer_media_only(self):
        for count in [0, 13, True, "2", 1.5]:
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.parse([count], 2)
        with self.assertRaisesRegex(ValueError, "仅用于"):
            self.parse([2], 2, kind="text")

    def test_rejected_plan_does_not_change_contract(self):
        members = [{"id": "artist", "name": "Artist", "kind": "image", "model": "chosen-model"}]
        raw = {"title": "Art", "requirements": [{"id": "art", "kind": "image", "count": 4, "description": "Four images"}],
               "steps": [{"id": "draw", "member_id": "artist", "instruction": "Draw", "expected_output": "Files",
                          "output_kind": "image", "output_count": 2}]}
        contract = {"images": 4, "videos": 0, "provider": "auto"}
        before = copy.deepcopy(contract)
        with self.assertRaisesRegex(ValueError, "output_count"):
            coordination.parse_plan(json.dumps(raw), members, contract, "auto")
        self.assertEqual(contract, before)

    def test_each_native_creator_receives_only_its_video_allocation(self):
        parsed, contract, members = self.parse([2, 1], 3, kind="video")
        delivery = {"steps": parsed["steps"], "limitations": []}
        members.insert(0, {"id": "unused", "name": "Unused Muse", "kind": "muse"})
        coordination.bind_native_media_steps(delivery, members, contract)
        self.assertEqual(len(delivery["steps"]), 2)
        for step, count in zip(delivery["steps"], [2, 1]):
            self.assertIn(f"{count} 个真实视频文件", step["instruction"])
            self.assertNotIn("3 个真实视频文件", step["instruction"])
        self.assertEqual(delivery["limitations"], [])

    def test_native_request_preserves_multiple_video_count(self):
        instruction = delivery_harness.native_media_instruction({"images": 2, "videos": 3, "provider": "muse"})
        self.assertIn("2 张真实生成的图片", instruction)
        self.assertIn("3 个真实视频文件", instruction)

    def test_legacy_native_step_gets_only_remaining_files(self):
        step = {"id": "creator", "member_id": "muse", "kind": "muse", "instruction": "Make media"}
        delivery = {"steps": [step], "limitations": []}
        coordination.bind_native_media_steps(delivery, [], {"images": 0, "videos": 2, "provider": "auto"})
        self.assertIn("2 个真实视频文件", step["instruction"])
        self.assertEqual(step["video_count"], 2)

    def test_native_image_roles_feed_their_files_to_video_role(self):
        members = [{"id": "artist", "name": "Artist", "kind": "muse"},
                   {"id": "filmmaker", "name": "Filmmaker", "kind": "muse"}]
        raw = {"title": "Film", "requirements": [
            {"id": "images", "kind": "image", "count": 2, "description": "Two images"},
            {"id": "videos", "kind": "video", "count": 2, "description": "Two videos"}],
            "steps": [{"id": kind, "member_id": member, "instruction": "Create", "expected_output": "Real files",
                       "output_kind": kind, "output_count": 2} for kind, member in [("image", "artist"), ("video", "filmmaker")]]}
        contract = {"images": 2, "videos": 2, "provider": "muse"}
        parsed = coordination.parse_plan(json.dumps(raw), members, contract, "auto")
        delivery = {"steps": parsed["steps"], "limitations": []}
        coordination.bind_native_media_steps(delivery, members, contract)
        self.assertEqual(parsed["steps"][1]["depends_on"], ["image"])
        self.assertNotIn("真实视频", parsed["steps"][0]["instruction"])
        self.assertNotIn("真实生成的图片", parsed["steps"][1]["instruction"])

    def test_auto_native_step_preserves_count_and_uses_selected_member(self):
        delivery = {"steps": [], "limitations": []}
        coordination.bind_native_media_steps(delivery, [{"id": "muse", "name": "Creator", "kind": "muse"}],
                                             {"images": 0, "videos": 2, "provider": "auto"})
        self.assertEqual(delivery["steps"][0]["video_count"], 2)
        self.assertEqual(delivery["steps"][0]["member_id"], "muse")

    def test_auto_muse_images_use_native_collection_without_changing_video_route(self):
        members = [{"id": "artist", "name": "Artist", "kind": "muse"},
                   {"id": "animator", "name": "Animator", "kind": "codex", "skill_ids": ["lemo-opuscar"]}]
        raw = {"title": "Art", "requirements": [
            {"id": "images", "kind": "image", "count": 2, "description": "Two images"},
            {"id": "video", "kind": "video", "count": 1, "description": "One video"}],
            "steps": [{"id": "draw", "member_id": "artist", "instruction": "Draw", "expected_output": "Images",
                       "output_kind": "image", "output_count": 2},
                      {"id": "animate", "member_id": "animator", "instruction": "Animate", "expected_output": "Video",
                       "output_kind": "video", "output_count": 1}]}
        contract = {"images": 2, "videos": 1, "provider": "auto"}
        parsed = coordination.parse_plan(json.dumps(raw), members, contract, "auto")
        self.assertEqual(contract["provider"], "muse")
        self.assertEqual(contract["video_provider"], "skill")
        self.assertEqual(parsed["steps"][1]["depends_on"], ["draw"])


    def test_native_reply_capacity_rejects_six_files_without_lowering_total(self):
        with self.assertRaisesRegex(ValueError, "最多回收 5"):
            self.parse([6], 6, kind="video")
        parsed, _, _ = self.parse([3, 3], 6, kind="video")
        self.assertEqual([step["video_count"] for step in parsed["steps"]], [3, 3])

    def test_combined_native_image_and_video_capacity_is_enforced(self):
        delivery = {"steps": [{"id": "draw", "kind": "muse", "member_id": "artist", "instruction": "Draw",
                               "output_kind": "image", "image_count": 4}], "limitations": []}
        with self.assertRaisesRegex(ValueError, "最多回收 5"):
            coordination.bind_native_media_steps(delivery, [], {"images": 4, "videos": 2, "provider": "muse"})

    def test_skill_video_count_cannot_exceed_actual_single_film_renderer(self):
        members = [{"id": "animator", "name": "Animator", "kind": "codex", "skill_ids": ["lemo-opuscar"]}]
        raw = {"title": "Two films", "requirements": [{"id": "films", "kind": "video", "count": 2, "description": "Two films"}],
               "steps": [{"id": "animate", "member_id": "animator", "instruction": "Animate", "expected_output": "Videos",
                          "output_kind": "video", "output_count": 2}]}
        contract = {"images": 0, "videos": 2, "provider": "auto"}
        before = copy.deepcopy(contract)
        with self.assertRaisesRegex(ValueError, "只能渲染 1"):
            coordination.parse_plan(json.dumps(raw), members, contract, "auto")
        self.assertEqual(contract, before)

    def test_native_video_reference_limit_is_checked_before_dispatch(self):
        members = [{"id": "artist", "name": "Artist", "kind": "image"}, {"id": "director", "name": "Director", "kind": "muse"}]
        raw = {"title": "Film", "requirements": [{"id": "pictures", "kind": "image", "count": 5, "description": "Five images"},
                {"id": "film", "kind": "video", "count": 1, "description": "One video"}], "steps": [
                {"id": "draw", "member_id": "artist", "instruction": "Draw", "expected_output": "Images", "output_kind": "image", "output_count": 5},
                {"id": "film", "member_id": "director", "instruction": "Film", "expected_output": "Video", "output_kind": "video", "output_count": 1}]}
        with self.assertRaisesRegex(ValueError, "最多接收 4"):
            coordination.parse_plan(json.dumps(raw), members, {"images": 5, "videos": 1, "provider": "auto"}, "auto")


class MixedCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_images_do_not_require_an_extra_native_video_when_skill_delivered_it(self):
        delivery = {"steps": [{"kind": "codex", "status": "complete", "skill_delivery": {"assets": []}}],
                    "media": [{"id": "image", "kind": "image", "provider": "muse", "sha256": "a"},
                              {"id": "film", "kind": "video", "provider": "skill", "sha256": "b"}], "limitations": []}
        with patch.object(delivery_harness, "media_progress"), \
             patch.object(delivery_harness, "collect_native_media", AsyncMock(return_value=([], []))):
            await delivery_harness.produce_media(None, {"group_runtime_allowed": True}, {}, delivery,
                                                {"images": 1, "videos": 1, "provider": "muse", "video_provider": "skill"})
        self.assertEqual({item["id"] for item in delivery["media_tasks"]}, {"skill-video", "native-image"})
        self.assertTrue(all(item["status"] == "complete" for item in delivery["media_tasks"]))
        self.assertEqual(delivery["limitations"], [])


from backend import test_coordination as coordination_fixtures
from backend.test_delivery_media import image_bytes
from backend import delivery_media


class NativeImageHandoffTests(unittest.IsolatedAsyncioTestCase):
    setUp = coordination_fixtures.CoordinationTests.setUp
    provider = coordination_fixtures.CoordinationTests.provider
    run_delivery = coordination_fixtures.CoordinationTests.run_delivery

    async def run_native_handoff(self, has_image):
        self.job.update(group_output_format="auto", group_media_provider="muse")
        for identifier in ["artist", "director"]:
            self.job["group_team_snapshot"]["members"].append({"id": identifier, "kind": "muse", "name": identifier, "role": "Create assigned media"})
            self.job["group_recipient_member_ids"].append(identifier)
        task_id = str(uuid.uuid4())
        store = delivery_media.DeliveryMediaStore(Path(self.tmp.name).resolve() / "media")
        asset = store.save_bytes(self.job, image_bytes(), kind="image", provider="muse", source_task_id=task_id)
        task = {"id": task_id, "parent_job_id": self.job["id"], "project_id": self.job["project_id"], "room_id": self.job["group_room_id"],
                "sent": True, "completion_evidence": "framed_assistant_reply", "source_kind": "native_muse", "response": "Done", "request_id": str(uuid.uuid4()),
                "_native_media": [asset] if has_image else []}
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                return json.dumps(coordination_fixtures.plan(steps=[
                    {"id": "draw", "member_id": "artist", "instruction": "Draw", "expected_output": "Image", "output_kind": "image", "output_count": 1},
                    {"id": "film", "member_id": "director", "instruction": "Animate the image", "expected_output": "Video", "output_kind": "video", "output_count": 1}],
                    requirements=[{"id": "picture", "kind": "image", "count": 1, "description": "One image"},
                                  {"id": "video", "kind": "video", "count": 1, "description": "One video"}])), False
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": False, "missing_requirements": ["No video in this mock"], "checks": [
                    {"requirement_id": identifier, "met": False, "evidence_status": "unverified", "reason": "Mock only"}
                    for identifier in ["media-image", "media-video"]]}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        sent = {}
        async def runtime(job, member, prompt, **kwargs):
            sent[member["id"]] = kwargs.get("reference_assets")
            return {"status": "complete", "task_id": task_id, "output": "Observed native reply", "sources": []}
        async def handoff(job, member, step, prompt, delivery, changed, **kwargs):
            return prompt
        with patch("backend.group_delivery_runtime.run_runtime_step", side_effect=runtime), \
             patch("backend.task_handoff.prepare_handoff", side_effect=handoff), \
             patch("backend.delivery_media.store", store), \
             patch("backend.muse_bridge.manager.get", return_value=task), \
             patch("backend.delivery_harness.produce_media", AsyncMock()):
            result = await self.run_delivery(provider)
        return result["delivery"], sent, asset

    async def test_verified_parent_image_reaches_downstream_native_executor(self):
        delivery, sent, asset = await self.run_native_handoff(True)
        self.assertEqual([item["id"] for item in sent["director"]], [asset["id"]])
        self.assertEqual(delivery["steps"][0]["media"][0]["sha256"], asset["sha256"])

    async def test_missing_actual_image_blocks_downstream_instead_of_passing_prose(self):
        delivery, sent, _ = await self.run_native_handoff(False)
        self.assertNotIn("director", sent)
        self.assertEqual(delivery["steps"][0]["status"], "error")
        self.assertEqual(delivery["steps"][1]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
