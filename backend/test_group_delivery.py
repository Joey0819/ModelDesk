"""Actual delivery orchestration and provenance with controlled provider I/O."""
import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend import group_chat, group_delivery, super_platform, workbench
from backend.artifacts import artifact_markdown, selected_artifact
from backend.conversations import deliverables
from backend.platform_store import PlatformStore


class Owner:
    def __init__(self, job):
        self.jobs = {job["id"]: job}

    def emit(self, job, kind, **fields):
        self.jobs[job["id"]]["events"].append({"type": kind, **copy.deepcopy(fields)})


def current(**changes):
    members = [{"id": "writer", "name": "作者", "model": "a", "kind": "model"},
               {"id": "muse", "name": "素材 Agent", "kind": "muse"}]
    return {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()), "group_room_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()), "created_at": "2026-10-15T00:00:00+00:00", "events": [],
            "mode": "group", "models": ["a"], "rounds": 1, "prompt": "写出完整的文章，并由 Muse 制作配图",
            "group_team_snapshot": {"members": members}, "group_recipient_member_ids": ["writer", "muse"],
            "group_broadcast": True, "group_delivery_mode": "auto", "group_runtime_allowed": True,
            "group_native_allowed": True, **changes}


def discussion():
    return {"messages": [{"model": "a", "member_id": "writer", "content": "应该写一篇文章"}], "partial_failure": False}


def final_packet(content, missing=()):
    return json.dumps({"title": "完整文章", "content": content, "missing_outputs": list(missing)}, ensure_ascii=False)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_group_flow_has_separate_final_body_with_no_runtime_required(self):
        job = current(group_team_snapshot={"members": [{"id": "writer", "name": "作者", "model": "a"}]},
                      group_recipient_member_ids=["writer"], prompt="写出完整的纯文字文章")
        owner = Owner(job)
        calls = []
        client_type = httpx.AsyncClient

        def handle(request):
            payload = json.loads(request.content)
            calls.append(payload)
            body = final_packet("# 完整文章\n\n开篇、正文与收尾的实际内容。") if payload["max_tokens"] == 8192 else "建议采用初稿，下一步写正文。"
            return httpx.Response(200, json={"choices": [{"message": {"content": body}, "finish_reason": "stop"}]})

        with patch.object(group_chat.httpx, "AsyncClient", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)):
            result = await group_chat.run_group(owner, job)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["delivery"]["status"], "complete")
        self.assertIn("# 完整文章", result["delivery"]["content"])
        self.assertNotIn("建议采用", result["delivery"]["content"])
        self.assertEqual(owner.jobs[job["id"]]["result"]["delivery"], result["delivery"])
        self.assertTrue(any(event["type"] == "delivery" for event in job["events"]))
        job.update(status="complete", result=result)
        self.assertIn("# 完整文章", artifact_markdown(job, "group:delivery"))
        self.assertEqual([item["content"] for item in deliverables(job)[0]], [result["delivery"]["content"]])
        self.assertIn("完整文章", "\n".join(group_chat.markdown_group(result)))

    async def test_plan_dispatch_collect_and_final_are_tied_to_actual_child(self):
        job = current(system_prompt="不要编造来源，正文以中文写作")
        owner = Owner(job)
        plan = json.dumps({"title": "有配图的文章", "tasks": [{"member_id": "muse", "instruction": "生成配图并返回实际资源"}]})
        provider = AsyncMock(side_effect=[(plan, False), (final_packet("# 完整正文\n\n已引用实际素材结果。"), False)])
        runtime = AsyncMock(return_value={"status": "complete", "task_id": "actual-child", "engine": "muse",
                                           "output": "REAL_MUSE_RESPONSE", "output_sha256": "f" * 64, "truncated": False})
        with patch.object(group_delivery, "model_output", provider), patch("backend.group_delivery_runtime.run_runtime_step", runtime):
            result = await group_delivery.run_delivery(owner, job, discussion())
        runtime.assert_awaited_once()
        self.assertEqual(runtime.await_args.args[1]["id"], "muse")
        self.assertTrue(runtime.await_args.kwargs["native_allowed"])
        self.assertIn("用户本次目标", runtime.await_args.args[2])
        self.assertIn("REAL_MUSE_RESPONSE", provider.await_args.args[3])
        self.assertIn("不要编造来源", provider.await_args.args[2])
        self.assertEqual(result["delivery"]["sources"][0]["task_id"], "actual-child")
        self.assertEqual(result["delivery"]["status"], "complete")

    async def test_malformed_or_truncated_plan_never_dispatches_but_preserves_body(self):
        for plan, truncated in [("invalid", False), ('{"title":"text","tasks":[]}', True),
                                 ('{"title":"text","tasks":[{"member_id":"foreign","instruction":"run"}]}', False)]:
            with self.subTest(plan=plan):
                job = current()
                with patch.object(group_delivery, "model_output", AsyncMock(side_effect=[(plan, truncated), (final_packet("实际可用正文"), False)])), \
                     patch("backend.group_delivery_runtime.run_runtime_step", AsyncMock()) as runtime:
                    result = await group_delivery.run_delivery(Owner(job), job, discussion())
                runtime.assert_not_awaited()
                self.assertEqual(result["delivery"]["status"], "partial")
                self.assertEqual(result["delivery"]["content"], "实际可用正文")
                self.assertTrue(result["partial_failure"])

    async def test_targeted_model_does_not_expand_to_other_agents_or_coordinator(self):
        job = current(group_broadcast=False, group_recipient_member_ids=["writer"], group_coordinator_model="other")
        provider = AsyncMock(return_value=(final_packet("指定成员的完整正文"), False))
        with patch.object(group_delivery, "model_output", provider), patch("backend.group_delivery_runtime.run_runtime_step", AsyncMock()) as runtime:
            await group_delivery.run_delivery(Owner(job), job, discussion())
        provider.assert_awaited_once()
        self.assertEqual(provider.await_args.args[1], "a")
        runtime.assert_not_awaited()

    async def test_blocked_child_and_truncated_editor_never_become_complete(self):
        job = current(group_native_allowed=False)
        plan = json.dumps({"title": "成果", "tasks": [{"member_id": "muse", "instruction": "生成媒体"}]})
        runtime = AsyncMock(return_value={"status": "blocked", "engine": "muse", "output": "", "error": "需要在 Mac 操作"})
        with patch.object(group_delivery, "model_output", AsyncMock(side_effect=[(plan, False), (final_packet("已有正文，媒体未生成。"), True)])), \
             patch("backend.group_delivery_runtime.run_runtime_step", runtime):
            result = await group_delivery.run_delivery(Owner(job), job, discussion())
        self.assertFalse(runtime.await_args.kwargs["native_allowed"])
        self.assertEqual(result["delivery"]["status"], "partial")
        self.assertIn("需要在 Mac", str(result["delivery"]["limitations"]))
        self.assertTrue(result["delivery"]["incomplete"])
        job.update(status="complete", result=result)
        self.assertTrue(selected_artifact(job, "group:delivery")["partial"])
        self.assertEqual(deliverables(job)[0], [])

    async def test_missing_requested_media_retains_body_without_claiming_completion(self):
        job = current(group_team_snapshot={"members": [{"id": "writer", "name": "作者", "model": "a"}]},
                      group_recipient_member_ids=["writer"])
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=(final_packet("# 已完成正文\n\n文章内容。", ["群内没有 Muse 成员，未取得要求的配图"]), False))):
            result = await group_delivery.run_delivery(Owner(job), job, discussion())
        self.assertEqual(result["delivery"]["status"], "partial")
        self.assertIn("文章内容", result["delivery"]["content"])
        self.assertIn("未取得", str(result["delivery"]["limitations"]))

    async def test_cancel_marks_parent_steps_and_preserves_actual_evidence(self):
        job = current()
        plan = json.dumps({"title": "成果", "tasks": [{"member_id": "muse", "instruction": "生成媒体"}]})
        with patch.object(group_delivery, "model_output", AsyncMock(return_value=(plan, False))), \
             patch("backend.group_delivery_runtime.run_runtime_step", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await group_delivery.run_delivery(Owner(job), job, discussion())
        self.assertEqual(job["result"]["delivery"]["status"], "partial")
        self.assertEqual(job["result"]["delivery"]["steps"][0]["status"], "cancelled")

    async def test_invalid_final_content_is_not_stringified_as_success(self):
        client_type = httpx.AsyncClient
        for raw in [None, {"invented": "text"}, [], " "]:
            def handle(request):
                return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
            with patch.object(group_delivery.httpx, "AsyncClient", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)):
                with self.assertRaises(RuntimeError):
                    await group_delivery.model_output(current(), "a", "system", "prompt")

    def test_final_memory_precedes_long_discussion_and_restart_does_not_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            result = {"messages": [{"content": "old discussion " * 5000}],
                      "delivery": {"status": "complete", "title": "正文", "content": "CANONICAL_FINAL", "sources": []}}
            job = current(remember=True, result=result, status="complete")
            store = PlatformStore(Path(folder) / "resources.json")
            with patch.object(super_platform, "store", store):
                saved = super_platform.remember_result(job)
            self.assertIn("CANONICAL_FINAL", saved["content"])
            self.assertNotIn("old discussion", saved["content"])
            job["status"] = "running"
            job["result"]["delivery"].update(status="working", steps=[{"status": "running", "task_id": "owned-child"}])
            manager = workbench.JobManager(Path(folder) / "jobs")
            manager.save(job)
            manager.recover()
            recovered = manager.get(job["id"])
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["result"]["delivery"]["steps"][0]["status"], "cancelled")
            self.assertFalse(manager.tasks)

    def test_long_discussion_cannot_crowd_out_actual_execution_evidence(self):
        messages = {"messages": [{"content": "x" * 40000, "model": "a"} for _ in range(30)]}
        quoted = group_delivery.evidence(messages)
        self.assertLessEqual(sum(len(item["content"]) for item in quoted), 18000)
        steps = [{"member_id": str(index), "output": "y" * 16000,
                  "changed_files": [{"path": "z" * 1000, "status": "added"} for _ in range(3000)],
                  "commands_observed": 20, "commands": [{"command": "cmd", "exit_code": 0, "aggregated_output": f"ACTUAL-{command}"} for command in range(20)]}
                 for index in range(4)]
        packet = group_delivery.execution_evidence(steps)
        self.assertEqual(len(packet), 4)
        self.assertTrue(all(item["commands"][-1]["aggregated_output"] == "ACTUAL-19" for item in packet))
        self.assertTrue(all(item["commands_omitted"] == 16 for item in packet))
        self.assertLess(len(json.dumps({"execution": packet, "discussion": quoted})), 80000)


if __name__ == "__main__":
    unittest.main()
