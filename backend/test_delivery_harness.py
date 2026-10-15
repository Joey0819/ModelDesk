"""Completion gates for real media, with no external model calls."""
import asyncio
import json
import unittest
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import delivery_harness, group_delivery, media_providers
from backend.test_group_delivery import Owner, current, discussion, final_packet


class MediaContractTests(unittest.TestCase):
    def test_common_chinese_and_english_requests_preserve_required_image_counts(self):
        for prompt, count in [("写一篇文章，附两张图片。", 2), ("给文章添加三张图片。", 3),
                              ("Write an illustrated article with 3 images.", 3),
                              ("Create an article with three pictures.", 3),
                              ("Generate 1 image for the article.", 1)]:
            with self.subTest(prompt=prompt):
                contract = delivery_harness.media_contract({}, prompt)
                self.assertEqual(contract["images"], count)
                self.assertEqual(contract["videos"], 0)

    def test_prompt_counts_negation_and_explicit_user_choice(self):
        contract = delivery_harness.media_contract
        self.assertEqual(contract({}, "写图文并茂的文章，三张配图，不调用 Muse"), {"images": 3, "videos": 0, "provider": "auto"})
        self.assertEqual(contract({}, "不要图片，不需要视频，只写文字"), {"images": 0, "videos": 0, "provider": "auto"})
        self.assertEqual(contract({}, "调用 Gemini 生成图片，写图文文章" )["provider"], "gemini")
        self.assertEqual(contract({"group_output_format": "text"}, "生成图片和制作视频")["images"], 0)
        self.assertEqual(contract({"group_output_format": "illustrated", "group_image_count": 1, "group_media_provider": "openai"}, "用 Muse 生成三张配图"), {"images": 1, "videos": 0, "provider": "openai"})

    def test_no_silent_video_provider_switch(self):
        self.assertEqual(delivery_harness.native_media_instruction({"images": 0, "videos": 1, "provider": "openai"}), "")
        self.assertIn("真实视频", delivery_harness.native_media_instruction({"images": 0, "videos": 1, "provider": "auto"}))

    def test_original_image_counts_survive_art_direction_adjectives(self):
        for prompt, count in [("图文并茂，生成一张原创插图，并制作3秒视频", 1), ("写图文文章，生成三张配套原创图片", 3), ("Write an illustrated article with three original images", 3)]:
            with self.subTest(prompt=prompt):
                self.assertEqual(delivery_harness.media_contract({}, prompt)["images"], count)

    def test_mixed_brief_routes_each_medium_to_its_requested_provider(self):
        contract = delivery_harness.media_contract({}, "写图文文章，调用 Gemini 生成一张插图，调用 Muse 制作视频")
        self.assertEqual(contract["provider"], "gemini")
        self.assertEqual(contract["video_provider"], "muse")
        self.assertIn("真实视频", delivery_harness.native_media_instruction(contract))
        reverse = delivery_harness.media_contract({}, "图文并茂，用 Muse 生成图片，用 Gemini 制作视频")
        self.assertEqual(reverse["provider"], "muse")
        self.assertEqual(reverse["video_provider"], "gemini")
        self.assertNotIn("真实视频", delivery_harness.native_media_instruction(reverse))
        for prompt in ["用 Gemini 生成一张图片并用 Muse 制作视频。", "视频用Muse，图片用Gemini", "Use Gemini for one image and Muse to create a video."]:
            with self.subTest(prompt=prompt):
                contract = delivery_harness.media_contract({}, prompt)
                self.assertGreater(contract["images"], 0)
                self.assertGreater(contract["videos"], 0)
                self.assertEqual(contract["provider"], "gemini")
                self.assertEqual(contract["video_provider"], "muse")
        self.assertEqual(delivery_harness.media_contract({}, "用ChatGPT生图并让Muse生成视频")["provider"], "openai")
        self.assertEqual(delivery_harness.media_contract({}, "图文并茂，生成一张图片并用Muse制作视频")["provider"], "auto")
        self.assertEqual(delivery_harness.media_contract({}, "图片用Gemini")["provider"], "gemini")
        self.assertEqual(delivery_harness.media_contract({}, "视频用Muse")["video_provider"], "muse")

    def test_bind_only_registered_media_preserving_complete_body(self):
        image = {"id": "real", "kind": "image", "slot_id": "image-1", "url": "/api/media/jobs/owned/assets/real/content", "caption": "真正的图"}
        result = delivery_harness.bind_media("# 标题\n\n完整开头\n\n{{media:image-1}}\n\n![伪造的图](https://fake.example/a.png)\n\n完整结尾", [image])
        self.assertIn("![真正的图](/api/media/jobs/owned/assets/real/content)", result)
        self.assertNotIn("https://fake", result)
        self.assertNotIn("{{media", result)
        self.assertTrue(result.endswith("完整结尾"))


class MediaDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setup_job(self, **changes):
        return current(group_team_snapshot={"members": [{"id": "writer", "name": "作者", "model": "a"}]},
                       group_recipient_member_ids=["writer"], prompt="写一篇完整图文文章", group_output_format="illustrated", group_image_count=2, **changes)

    async def test_actual_assets_bound_and_exported_by_final_delivery(self):
        job = self.setup_job()
        briefs = json.dumps({"images": [{"prompt": "原创封面", "caption": "封面"}, {"prompt": "原创流程", "caption": "流程"}]})
        assets = [{"id": str(index), "kind": "image", "slot_id": f"image-{index}", "url": f"/api/media/jobs/{job['id']}/assets/{index}/content", "sha256": str(index) * 64, "provider": "gemini", "model": "image-model"} for index in (1, 2)]
        with patch.object(group_delivery, "model_output", AsyncMock(side_effect=[(briefs, False), (final_packet("# 标题\n\n完整正文\n\n{{media:image-1}}\n\n完整结尾"), False)])), patch.object(media_providers, "generate_image", AsyncMock(side_effect=assets)) as generator:
            result = await group_delivery.run_delivery(Owner(job), job, discussion())
        delivery = result["delivery"]
        self.assertEqual(generator.await_count, 2)
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["media"], assets)
        self.assertTrue(all(task["status"] == "complete" for task in delivery["media_tasks"]))
        self.assertTrue(all(asset["url"] in delivery["content"] for asset in assets))
        self.assertEqual(len(delivery["content_sha256"]), 64)

    async def test_duplicate_image_bytes_cannot_satisfy_two_required_images(self):
        job = self.setup_job()
        assets = [{"id": str(index), "kind": "image", "slot_id": f"image-{index}",
                   "url": f"/api/media/jobs/{job['id']}/assets/{index}/content", "sha256": "f" * 64,
                   "provider": "gemini", "model": "image-model"} for index in (1, 2)]
        with patch.object(group_delivery, "model_output", AsyncMock(side_effect=[("bad-plan", False), (final_packet("两张图片已完成，正文完整。"), False)])), \
                patch.object(media_providers, "generate_image", AsyncMock(side_effect=assets)):
            result = await group_delivery.run_delivery(Owner(job), job, discussion())
        delivery = result["delivery"]
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual(len(delivery["media"]), 1)
        self.assertEqual(len({(asset["kind"], asset["sha256"]) for asset in delivery["media"]}), 1)
        self.assertEqual(delivery["media_requirements"]["images"], 2)
        self.assertTrue(result["partial_failure"])
        self.assertTrue(any("实际取得 1" in note for note in delivery["limitations"]))

    async def test_text_claim_cannot_satisfy_failed_image_generation(self):
        job = self.setup_job()
        with patch.object(group_delivery, "model_output", AsyncMock(side_effect=[("bad-plan", False), (final_packet("完整文章：两张图片已生成。"), False)])), patch.object(media_providers, "generate_image", AsyncMock(side_effect=media_providers.MediaProviderError("该模型未提供生图权限"))):
            result = await group_delivery.run_delivery(Owner(job), job, discussion())
        self.assertEqual(result["delivery"]["status"], "partial")
        self.assertEqual(result["delivery"]["media"], [])
        self.assertTrue(result["partial_failure"])
        self.assertTrue(any("实际取得 0" in item for item in result["delivery"]["limitations"]))
        self.assertIn("生图权限", result["delivery"]["media_tasks"][0]["error"])

    async def test_cancel_preserves_existing_assets_and_cancels_pending_tasks(self):
        job = self.setup_job()
        delivery = {"model": "a", "steps": [], "limitations": [], "media": []}
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=("bad-plan", False))), patch.object(media_providers, "generate_image", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await delivery_harness.produce_media(Owner(job), job, discussion(), delivery, {"images": 2, "videos": 0, "provider": "auto"})
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual([item["status"] for item in delivery["media_tasks"]], ["cancelled", "cancelled"])

    async def test_native_download_cancellation_preserves_first_saved_asset(self):
        job = self.setup_job()
        member = {"id": "native", "name": "Muse", "kind": "muse"}
        job["group_team_snapshot"]["members"].append(member)
        job["group_recipient_member_ids"].append(member["id"])
        job.update(group_output_format="video", prompt="请制作一个真实视频。")
        plan = json.dumps({"title": "视频交付", "tasks": []})
        child = {"id": "actual-child", "parent_job_id": job["id"], "project_id": job["project_id"],
                 "room_id": job["group_room_id"], "member_id": member["id"], "member": member,
                 "status": "complete", "source_kind": "native_muse", "sent": True, "uncertain": False,
                 "completion_evidence": "framed_assistant_reply", "response": "真实媒体已返回。",
                 "_native_resources": [{"url": "https://media.example/first.png", "label": "首张实际配图"},
                                       {"url": "https://media.example/video.mp4", "label": "实际视频"}]}
        first = {"id": "saved-first", "kind": "image", "provider": "muse", "model": "Muse native",
                 "slot_id": "muse-1", "sha256": "a" * 64, "url": "/api/media/first"}
        runtime = AsyncMock(return_value={"status": "complete", "task_id": child["id"], "engine": "muse",
                                          "output": "真实媒体已返回。", "sources": []})
        importer = AsyncMock(side_effect=[first, asyncio.CancelledError()])
        owner = Owner(job)
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=(plan, False))), \
                patch("backend.group_delivery_runtime.run_runtime_step", runtime), \
                patch("backend.muse_bridge.manager.get", return_value=child), \
                patch("backend.delivery_media.import_url", importer):
            with self.assertRaises(asyncio.CancelledError):
                await group_delivery.run_delivery(owner, job, discussion())
        delivery = owner.jobs[job["id"]]["result"]["delivery"]
        self.assertEqual(importer.await_count, 2)
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual(delivery["media"], [first])
        self.assertTrue(any(event.get("delivery", {}).get("media") == [first] for event in job["events"]))
        self.assertFalse(any(task["status"] in {"running", "pending"} for task in delivery["media_tasks"]))

    async def test_final_model_cancellation_finishes_delivery_as_partial(self):
        job = self.setup_job()
        job.update(group_output_format="text", prompt="写出完整的文字文章。")
        owner = Owner(job)
        with patch.object(group_delivery, "model_output", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await group_delivery.run_delivery(owner, job, discussion())
        delivery = owner.jobs[job["id"]]["result"]["delivery"]
        self.assertEqual(delivery["status"], "partial")
        self.assertTrue(delivery["limitations"])
        self.assertTrue(any(event.get("delivery", {}).get("status") == "partial" for event in job["events"]))

    async def test_muse_resources_require_current_parent_and_observed_reply(self):
        job = self.setup_job()
        step = {"kind": "muse", "status": "complete", "task_id": "child"}
        task = {"id": "child", "parent_job_id": "other", "room_id": job["group_room_id"], "sent": True,
                "completion_evidence": "framed_assistant_reply", "_native_resources": [{"url": "https://media.example/real.png"}]}
        with patch("backend.muse_bridge.manager.get", return_value=task), patch("backend.delivery_media.import_url", AsyncMock()) as importer:
            assets, _ = await delivery_harness.collect_native_media(job, [step])
        self.assertEqual(assets, [])
        importer.assert_not_awaited()

    async def test_owned_native_file_is_rechecked_and_incrementally_collected(self):
        from backend import delivery_media
        from backend.test_delivery_media import image_bytes
        job = self.setup_job()
        child = str(uuid.uuid4())
        step = {"kind": "muse", "status": "complete", "task_id": child}
        task = {"id": child, "parent_job_id": job["id"], "room_id": job["group_room_id"], "sent": True,
                "completion_evidence": "framed_assistant_reply", "response": "当前原生附件"}
        with tempfile.TemporaryDirectory() as directory:
            store = delivery_media.DeliveryMediaStore(Path(directory).resolve() / "media")
            asset = store.save_bytes(job, image_bytes(), kind="image", provider="muse", source_task_id=child)
            task["_native_media"] = [asset]
            received = []
            with patch.object(delivery_media, "store", store), patch("backend.muse_bridge.manager.get", return_value=task):
                assets, errors = await delivery_harness.collect_native_media(job, [step], on_asset=received.append)
                self.assertEqual(assets, [asset])
                self.assertEqual(received, [asset])
                self.assertEqual(errors, [])
                (store.directory / job["id"] / asset["id"] / asset["name"]).write_bytes(b"tampered")
                assets, errors = await delivery_harness.collect_native_media(job, [step])
                self.assertEqual(assets, [])
                self.assertTrue(errors)

    async def test_requested_video_provider_stays_explicit_and_partial(self):
        job = self.setup_job()
        delivery = {"model": "a", "steps": [], "limitations": []}
        with patch.object(delivery_harness, "collect_native_media", AsyncMock()) as native, patch.object(media_providers, "generate_image", AsyncMock()) as generator:
            await delivery_harness.produce_media(Owner(job), job, discussion(), delivery, {"images": 0, "videos": 1, "provider": "gemini"})
        native.assert_not_awaited()
        generator.assert_not_awaited()
        self.assertTrue(any("尚未接入视频" in item for item in delivery["limitations"]))

    async def test_rejected_extra_link_does_not_hide_complete_owned_native_media(self):
        job = self.setup_job()
        delivery = {"model": "a", "steps": [], "limitations": []}
        asset = {"id": "native-video", "kind": "video", "provider": "muse", "sha256": "a" * 64}
        async def collect(job, steps, on_asset=None):
            on_asset(asset)
            return [asset], ["一个非媒体链接未通过媒体校验"]
        with patch.object(delivery_harness, "collect_native_media", collect):
            await delivery_harness.produce_media(Owner(job), job, discussion(), delivery, {"images": 0, "videos": 1, "provider": "muse"})
        self.assertEqual(delivery["media"], [asset])
        self.assertEqual(delivery["limitations"], [])
        self.assertEqual(delivery["media_tasks"][0]["status"], "complete")
        self.assertTrue(delivery["media_warnings"])

    async def test_distinct_image_and_video_providers_form_one_delivery(self):
        job = self.setup_job()
        delivery = {"model": "a", "steps": [], "limitations": []}
        video = {"id": "video", "kind": "video", "provider": "muse", "sha256": "a" * 64}
        image = {"id": "image", "kind": "image", "provider": "gemini", "sha256": "b" * 64}
        async def collect(job, steps, on_asset=None):
            on_asset(video)
            return [video], []
        plan = json.dumps({"images": [{"prompt": "原创说明图", "caption": "插图"}]})
        with patch.object(delivery_harness, "collect_native_media", collect), patch.object(group_delivery, "model_output", AsyncMock(return_value=(plan, False))), patch.object(media_providers, "generate_image", AsyncMock(return_value=image)) as generate:
            await delivery_harness.produce_media(Owner(job), job, discussion(), delivery, {"images": 1, "videos": 1, "provider": "gemini", "video_provider": "muse"})
        self.assertEqual(delivery["media"], [video, image])
        self.assertEqual(generate.await_args.kwargs["provider"], "gemini")
        self.assertTrue(all(task["status"] == "complete" for task in delivery["media_tasks"]))
        self.assertEqual(delivery["limitations"], [])


if __name__ == "__main__":
    unittest.main()
