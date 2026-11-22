import asyncio
import copy
import hashlib
import json
import logging
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import feishu, feishu_sdk, groups, workbench
from backend.feishu_store import Binding, ConfigInput, FeishuStore
from backend.feishu_worker import SanitizedConnectionLog, normalize_event


class FakeSDK:
    def __init__(self):
        self.calls = []
        self.fail = False

    def reply(self, message, kind, content, dispatch):
        self.calls.append(("reply", message, kind, content, dispatch))
        if self.fail:
            self.fail = False
            raise TimeoutError("unconfirmed secret must never be displayed")
        return "om_reply_" + str(len(self.calls))

    def send(self, chat_id, kind, content, dispatch):
        self.calls.append(("send", chat_id, kind, content, dispatch))
        if self.fail:
            self.fail = False
            raise TimeoutError("unconfirmed secret must never be displayed")
        return "om_send_" + str(len(self.calls))

    def upload(self, entry, raw):
        self.calls.append(("upload", entry, raw))
        return "file_confirmed"

    def bot_info(self):
        return {"open_id": "ou_bot"}

    def chats(self, token=None):
        return {"items": [{"chat_id": "oc_one", "name": "测试群"}], "has_more": False, "page_token": None}


class FeishuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = FeishuStore(self.root / "feishu")
        self.jobs = workbench.JobManager(self.root / "jobs")
        self.room_id, self.project_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.binding = {"chat_id": "oc_one", "name": "授权群", "project_id": self.project_id, "room_id": self.room_id,
                        "allowed_sender_ids": ["ou_person"], "allow_native": False}
        self.addCleanup(patch.stopall)
        patch.object(groups.store, "get", return_value={"id": self.room_id, "project_id": self.project_id}).start()
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", app_secret="test-secret", bindings=[self.binding]))
        self.sdk = FakeSDK()
        self.controller = feishu.FeishuController(self.store, self.jobs, sdk_factory=lambda config: self.sdk)

    def event(self, **changes):
        return {"app_id": "cli_test", "sender_type": "user", "sender_id": "ou_person", "chat_id": "oc_one", "chat_type": "group",
                "message_id": "om_" + uuid.uuid4().hex, "message_type": "text", "text": "/modeldesk task 写报告",
                "create_time": str(int(time.time() * 1000)), **changes}

    def received(self, **changes):
        identifier = self.store.ingest(self.event(**changes))
        return next(row for row in self.store.events() if row["id"] == identifier)

    async def submit(self, room, request, project, **kwargs):
        record = next(row for row in self.store.events() if row["job_id"] == kwargs["job_id"])
        self.assertEqual(record["state"], "dispatching")  # persisted BEFORE any dispatch
        self.assertEqual(project, self.project_id)
        self.assertEqual(room, self.room_id)
        job = {"id": kwargs["job_id"], "mode": "group", "status": "running", "project_id": project, "group_room_id": room, "events": [],
               "conversation_id": room, "group_context_mode": request.context_mode if request else "fresh",
               "external_source": kwargs["external_source"], "created_at": workbench.now(), "autonomy": {"state": "running"}}
        self.jobs.jobs[job["id"]] = job
        self.jobs.wakeups[job["id"]] = asyncio.Event()
        return job

    async def test_ingress_dedupe_scope_limits_and_private_discovery(self):
        event = self.event()
        identifier = self.store.ingest(event)
        self.assertEqual(self.store.ingest(event), identifier)
        self.assertEqual(len(self.store.events()), 1)
        for changes in [{"sender_type": "app"}, {"sender_id": "ou_stranger"}, {"app_id": "cli_other"},
                        {"text": "ambient chat"}, {"message_type": "image"}, {"create_time": "0"},
                        {"create_time": str(int((time.time() + 300) * 1000))}]:
            self.assertIsNone(self.store.ingest(self.event(**changes)))
        self.assertIsNone(self.store.ingest(self.event(chat_id="oc_private", chat_type="p2p", text="private secret")))
        observed = self.store.observed("cli_test")
        self.assertIn("oc_private", [entry["chat_id"] for entry in observed])
        self.assertNotIn("private secret", json.dumps(observed))
        for _ in range(10):
            self.store.ingest(self.event())
        self.assertEqual(self.store.events()[-1]["state"], "rejected")

    async def test_task_and_explicit_continue_use_shared_submit_without_native_escalation(self):
        root = self.received()
        self.received(text=f"/modeldesk continue {root['job_id']} 改成英文")
        submit = AsyncMock(side_effect=self.submit)
        with patch.object(groups, "submit_message", submit):
            await self.controller.tick()
        self.assertEqual(submit.await_count, 2)
        first, second = submit.await_args_list
        self.assertEqual(first.args[1].prompt, "写报告")
        self.assertEqual(first.args[1].context_mode, "fresh")
        self.assertTrue(first.args[1].background_mode)
        self.assertEqual(second.args[1].context_mode, "continue")
        self.assertEqual(second.args[1].prompt, "改成英文")
        self.assertEqual(second.kwargs["external_source"]["parent_job_id"], root["job_id"])
        self.assertFalse(first.kwargs["native_allowed"])
        self.assertTrue(all(row["state"] == "monitoring" for row in self.store.events()))
        self.assertEqual({row["job_id"] for row in self.controller.public()["recent"]}, set(self.jobs.jobs))

    async def test_mobile_whitespace_preserves_prompt_and_ignored_message_has_private_diagnostic(self):
        self.assertIsNone(self.store.ingest(self.event(text='unrelated private message')))
        observed = self.store.observed('cli_test')[0]
        self.assertEqual(observed['last_outcome'], 'not_command')
        self.assertNotIn('unrelated private message', json.dumps(observed))
        self.received(text='/modeldesk\ntask\u00a0第一行\n第二行')
        with patch.object(groups, 'submit_message', AsyncMock(side_effect=self.submit)) as submit:
            await self.controller.tick()
        self.assertEqual(submit.await_args.args[1].prompt, '第一行\n第二行')
        self.assertEqual(self.store.observed('cli_test')[0]['last_outcome'], 'command_received')

    async def test_rejected_attachment_replies_once_without_starting_task(self):
        self.received(message_type='post', input_error='unsupported_content')
        with patch.object(groups, 'submit_message', AsyncMock()) as submit:
            await self.controller.tick()
            await self.controller.tick()
        submit.assert_not_awaited()
        self.assertEqual(len(self.sdk.calls), 1)
        self.assertIn('未派发任务', self.sdk.calls[0][3]['text'])
        self.assertEqual(self.store.events()[0]['notifications'], ['rejected'])

    async def test_natural_language_is_opt_in_scoped_and_keeps_explicit_continuation(self):
        self.assertIsNone(self.store.ingest(self.event(text='写150字清单')))
        self.store.save_config(ConfigInput(enabled=True, app_id='cli_test', bindings=[{**self.binding, 'natural_language_tasks': True}]))
        self.assertIsNone(self.store.ingest(self.event(sender_id='ou_stranger', text='写150字清单')))
        self.received(text='写150字清单\n交付Word、PDF和HTML')
        self.received(text='继续：保留要求，调整结尾')
        self.received(text='你好')
        self.received(text='/unknown delete all')
        with patch.object(groups, 'submit_message', AsyncMock(side_effect=self.submit)) as submit:
            await self.controller.tick()
        self.assertEqual(submit.await_count, 2)
        first, second = submit.await_args_list
        self.assertEqual(first.args[1].prompt, '写150字清单\n交付Word、PDF和HTML')
        self.assertEqual(first.args[1].context_mode, 'fresh')
        self.assertEqual(second.args[1].prompt, '保留要求，调整结尾')
        self.assertEqual(second.args[1].context_mode, 'continue')
        self.assertFalse(first.kwargs['native_allowed'])
        self.assertEqual({row['input_mode'] for row in self.store.events()}, {'natural_language'})
        self.assertEqual(self.store.events()[-1]['reason'], '已回传指令用法，没有创建任务')

    async def test_wrong_or_ambiguous_targets_stop_before_dispatch(self):
        root_a = self.received()
        root_b = self.received(text='/modeldesk task 新任务B')
        self.received(text='/modeldesk continue 改报告')
        self.received(text=f'/modeldesk continue {uuid.uuid4()} 改报告')
        self.received(text=f'/modeldesk continue {root_a["job_id"][:-1]} 改报告')
        self.received(text=f'/modeldesk continue {root_a["job_id"]}')
        self.received(text='/modeldesk continue 改报告', parent_id='om_unknown')
        self.received(text='/modeldesk continue 改报告', root_id=root_a['message_id'])
        self.received(text=f'/modeldesk continue {root_b["job_id"]} 改报告', parent_id=root_a['message_id'])
        with patch.object(groups, 'submit_message', AsyncMock(side_effect=self.submit)) as submit:
            await self.controller.tick()
        self.assertEqual(submit.await_count, 2)
        for call in self.sdk.calls[2:]:
            self.assertIn('/modeldesk status', call[3]['text'])
        self.assertIn('多个可选的任务', self.sdk.calls[2][3]['text'])
        self.assertIn('格式无效', self.sdk.calls[4][3]['text'])
        self.assertIn('后写上修改要求', self.sdk.calls[5][3]['text'])
        self.assertIn('无法对应', self.sdk.calls[-1][3]['text'])

    async def test_reply_to_result_remembers_the_full_chain_across_controller_restart(self):
        root = self.received()
        revision = self.received(text='/modeldesk continue 改报告')
        submit = AsyncMock(side_effect=self.submit)
        with patch.object(groups, 'submit_message', submit):
            await self.controller.tick()
            self.jobs.get(revision['job_id']).update(status='complete', result={'delivery': {'content': '新版报告'}},
                autonomy={'state': 'complete', 'notification_id': 'revision-complete'})
            with patch('backend.autonomy.completion_problem', return_value=None):
                await self.controller.tick()
            self.assertTrue(self.sdk.calls[2][3]['text'].startswith('任务已完成'))
            followup = self.received(text='/modeldesk continue 调整结尾', parent_id='om_reply_3', root_id=revision['message_id'])
            restarted = feishu.FeishuController(FeishuStore(self.store.directory), self.jobs, sdk_factory=lambda _: self.sdk)
            await restarted.tick()
        self.assertEqual(submit.await_count, 3)
        from backend.task_context import task_records
        self.assertEqual([job['id'] for job in task_records(self.jobs.jobs, self.jobs.get(followup['job_id']))],
                         [root['job_id'], revision['job_id']])

    async def test_continuation_rejects_foreign_tasks_and_old_unscoped_continuations(self):
        self.store.save_config(ConfigInput(enabled=True, app_id='cli_test', bindings=[
            {**self.binding, 'allowed_sender_ids': ['ou_person', 'ou_colleague']},
            {**self.binding, 'chat_id': 'oc_two'},
        ]))
        root = self.received()
        submit = AsyncMock(side_effect=self.submit)
        with patch.object(groups, 'submit_message', submit):
            await self.controller.tick()
        saved = self.jobs.jobs[root['job_id']]
        foreign_cases = [({}, {'app_id': 'cli_wrong'}), ({}, {'config_revision': 'stale'}),
                         ({'project_id': str(uuid.uuid4())}, {}), ({'group_room_id': str(uuid.uuid4())}, {}),
                         ({'conversation_id': str(uuid.uuid4())}, {}), ({'group_context_mode': 'continue'}, {})]
        submit.reset_mock()
        command = f'/modeldesk continue {root["job_id"]} 改报告'
        with patch.object(groups, 'submit_message', submit):
            for changed_job, changed_source in foreign_cases:
                self.jobs.jobs[root['job_id']] = {**saved, **changed_job, 'external_source': {**saved['external_source'], **changed_source}}
                self.received(text=command)
                await self.controller.tick()
            self.jobs.jobs[root['job_id']] = saved
            self.received(chat_id='oc_two', text=command)
            self.received(sender_id='ou_colleague', text=command)
            self.received(sender_id='ou_colleague', text='/modeldesk continue 改报告', parent_id='om_reply_1')
            await self.controller.tick()
            submit.assert_not_awaited()
            self.received(text=command)
            await self.controller.tick()
        self.assertEqual(submit.await_count, 1)  # Older fresh jobs already carry enough provenance.
        self.assertEqual(submit.await_args.kwargs['external_source']['parent_job_id'], root['job_id'])

    async def test_restart_reconciles_created_job_and_never_replays_unknown_dispatch(self):
        record = self.received()
        self.store.update(record, state="dispatching")
        submit = AsyncMock(side_effect=AssertionError("must not repeat uncertain dispatch"))
        with patch.object(groups, "submit_message", submit):
            await self.controller.tick()
        self.assertEqual(self.store.events()[0]["state"], "needs_attention")
        second = self.received()
        self.store.update(second, state="dispatching")
        await self.submit(self.room_id, None, self.project_id, job_id=second["job_id"],
                          external_source={"platform": "feishu", "app_id": "cli_test", "chat_id": "oc_one",
                                           "message_id": second["message_id"], "config_revision": second["config_revision"]})
        with patch.object(groups, "submit_message", submit):
            await self.controller.tick()
        self.assertEqual(self.store.events()[-1]["state"], "monitoring")
        submit.assert_not_awaited()

    async def test_one_bot_routes_two_chats_to_separate_groups_without_new_secret(self):
        second_project, second_room = str(uuid.uuid4()), str(uuid.uuid4())
        second = {**self.binding, 'chat_id': 'oc_two', 'project_id': second_project, 'room_id': second_room}
        self.store.save_config(ConfigInput(enabled=True, app_id='cli_test', bindings=[self.binding, second]))
        self.assertEqual(self.store.config()['app_secret'], 'test-secret')
        self.received()
        self.received(chat_id='oc_two', text='/modeldesk task 独立项目报告')
        destinations = []
        async def submit(room, request, project, **kwargs):
            destinations.append((room, project, request.prompt))
            job = {'id': kwargs['job_id'], 'status': 'running', 'project_id': project, 'group_room_id': room,
                   'external_source': kwargs['external_source'], 'events': [], 'autonomy': {'state': 'running'}}
            self.jobs.jobs[job['id']] = job
            return job
        with patch.object(groups, 'submit_message', AsyncMock(side_effect=submit)):
            await self.controller.tick()
        self.assertCountEqual(destinations, [(self.room_id, self.project_id, '写报告'),
                                            (second_room, second_project, '独立项目报告')])
        self.assertEqual({row['state'] for row in self.store.events()}, {'monitoring'})

    async def test_uncertain_ack_keeps_monitoring_and_completion_is_separate(self):
        record = self.received()
        self.sdk.fail = True
        with patch.object(groups, "submit_message", AsyncMock(side_effect=self.submit)):
            await self.controller.tick()
        current = self.store.events()[0]
        self.assertEqual(current["state"], "monitoring")
        self.assertEqual(len(self.jobs.jobs), 1)
        job = self.jobs.jobs[record["job_id"]]
        job.update(status="complete", autonomy={"state": "complete", "notification_id": "notification", "reason": "done"},
                   result={"delivery": {"content": "完整正文", "documents": []}})
        with patch("backend.autonomy.completion_problem", return_value=None):
            await self.controller.tick()
        self.assertEqual(self.store.events()[0]["state"], "delivered")
        self.assertEqual(len(self.sdk.calls), 3)  # uncertain ack + final summary + full body
        await self.controller.tick()
        self.assertEqual(len(self.sdk.calls), 3)

    async def test_config_change_halts_old_outbound_and_new_dispatch(self):
        record = self.received()
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", bindings=[{**self.binding, "name": "newbinding"}]))
        with patch.object(groups, "submit_message", AsyncMock()) as submit:
            await self.controller.tick()
        self.assertEqual(self.store.events()[0]["state"], "needs_attention")
        self.assertFalse(self.sdk.calls)
        submit.assert_not_awaited()
        with self.assertRaises(RuntimeError):
            await self.controller.reply_once(record, "old", "old result")
        self.assertFalse(self.sdk.calls)

    async def test_unbound_listener_discovery_does_not_authorize_and_native_requires_saved_grant(self):
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", bindings=[]))
        self.assertIsNone(self.store.ingest(self.event()))
        self.assertEqual(self.store.observed("cli_test")[0]["chat_id"], "oc_one")
        self.assertFalse(self.store.events())
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", bindings=[{**self.binding, "allow_native": True}]))
        self.received(text="/modeldesk task 执行已授权的工作")
        submit = AsyncMock(side_effect=self.submit)
        with patch.object(groups, "submit_message", submit):
            await self.controller.tick()
        self.assertTrue(submit.await_args.kwargs["native_allowed"])

    async def test_preflight_failure_reports_saved_but_not_returned_without_upload(self):
        self.received()
        with patch.object(groups, "submit_message", AsyncMock(side_effect=self.submit)):
            await self.controller.tick()
        record = self.store.events()[0]
        job = self.jobs.jobs[record["job_id"]]
        job.update(status="complete", autonomy={"state": "complete", "notification_id": "n", "reason": "done"},
                   result={"delivery": {"content": "saved", "documents": []}})
        with patch("backend.autonomy.completion_problem", return_value=None), patch("backend.feishu_assets.verified_artifacts",
                    side_effect=feishu_sdk.FeishuAPIError("超过本通道大小上限")):
            await self.controller.tick()
        self.assertEqual(self.store.events()[0]["state"], "needs_attention")
        self.assertIn("未回传", self.sdk.calls[-1][3]["text"])
        self.assertFalse(any(call[0] == "upload" for call in self.sdk.calls))
        self.received(text="/modeldesk status")
        await self.controller.tick()
        self.assertIn("飞书回传", self.sdk.calls[-1][3]["text"])
        self.assertIn("超过本通道大小上限", self.sdk.calls[-1][3]["text"])

    async def test_uncertain_outgoing_reservation_never_retries_after_restart(self):
        record = self.received()
        self.sdk.fail = True
        with self.assertRaises(TimeoutError):
            await self.controller.reply_once(record, "same", "first")
        other = feishu.FeishuController(FeishuStore(self.store.directory), self.jobs, sdk_factory=lambda _: self.sdk)
        with self.assertRaises(RuntimeError):
            await other.reply_once(record, "same", "second")
        self.assertEqual(len(self.sdk.calls), 1)

    async def test_unknown_bare_commands_and_unrelated_cancel_do_not_create_tasks(self):
        for command in ["/modeldesk", "/modeldesk delete all", "/modeldesk status", "/modeldesk cancel " + str(uuid.uuid4())]:
            self.received(text=command)
        with patch.object(groups, "submit_message", AsyncMock()) as submit:
            await self.controller.tick()
        submit.assert_not_awaited()
        self.assertFalse(self.jobs.jobs)
        self.assertEqual(len(self.sdk.calls), 4)
        self.assertTrue(all(row["job_id"] is None for row in self.controller.public()["recent"]))

    async def test_long_complete_text_and_verified_document_bytes_are_returned(self):
        from backend import document_delivery
        from backend.document_exports import render_document
        document_store = document_delivery.DocumentStore(self.root / "documents")
        record = self.received()
        with patch.object(groups, "submit_message", AsyncMock(side_effect=self.submit)):
            await self.controller.tick()
        job = self.jobs.jobs[record["job_id"]]
        content = "不应静默截断的完整正文。" * 900
        delivery = {"title": "报告", "status": "complete", "content": content, "media": []}
        job.update(status="complete", result={"delivery": delivery}, autonomy={"state": "complete", "notification_id": "n1", "reason": "done"})
        rendered = await render_document(job, "group:delivery", "html", "report")
        entry = document_store.save(job, "group:delivery", rendered)
        delivery["documents"] = [entry]
        with patch("backend.autonomy.completion_problem", return_value=None), patch.object(document_delivery, "store", document_store):
            await self.controller.tick()
        self.assertEqual(self.store.events()[0]["state"], "delivered")
        chunks = [call[3]["text"].split("\n", 1)[1] for call in self.sdk.calls if call[0] == "reply" and call[2] == "text" and call[3]["text"].startswith("完整正文 ")]
        self.assertEqual("".join(chunks), content)
        upload = next(call for call in self.sdk.calls if call[0] == "upload")
        self.assertEqual(hashlib.sha256(upload[2]).hexdigest(), entry["sha256"])
        self.assertTrue(any(call[0] == "reply" and call[2] == "file" for call in self.sdk.calls))
        for i, call in enumerate(self.sdk.calls, 1):
            if call[0] == "reply":
                self.assertEqual(self.store.task_for_reply({**record, "reply_message_id": f"om_reply_{i}"}), job["id"])

    async def test_verified_text_results_reply_once_to_original_chat(self):
        from backend.coordination import save_version

        for state, status, filename in [
            ("complete", "complete", "modeldesk-result.txt"),
            ("needs_attention", "partial", "modeldesk-partial-result.txt"),
            ("needs_attention", "blocked", None),
        ]:
            with self.subTest(status=status):
                self.sdk.calls.clear()
                record = self.received()
                with patch.object(groups, "submit_message", AsyncMock(side_effect=self.submit)):
                    await self.controller.tick()
                job = self.jobs.jobs[record["job_id"]]
                content = "保存的正文与成果。\n"
                delivery = {"title": "报告", "status": status, "content": content, "media": [],
                            "versions": [], "workflow": {"run_id": job["id"]}}
                save_version(delivery, "fixture output")
                if status == "blocked":
                    delivery["artifact"]["content_sha256"] = "0" * 64
                job.update(status="complete", result={"delivery": delivery},
                           autonomy={"state": state, "notification_id": str(uuid.uuid4()), "reason": "已保存阶段成果"})
                with patch("backend.autonomy.completion_problem", return_value=None):
                    await self.controller.tick()
                    calls = list(self.sdk.calls)
                    await self.controller.tick()
                self.assertEqual(self.sdk.calls, calls)
                outgoing = [call for call in calls if call[0] == "reply"]
                self.assertTrue(outgoing)
                self.assertEqual({call[1] for call in outgoing}, {record["message_id"]})
                uploads = [call for call in calls if call[0] == "upload"]
                self.assertEqual([call[1]["filename"] for call in uploads], [filename] if filename else [])
                self.assertEqual([call[2] for call in uploads], [content.encode()] if filename else [])
                self.assertEqual(any(call[2] == "file" for call in outgoing), bool(filename))
                self.assertEqual(any(call[3].get("text", "").startswith("完整正文") for call in outgoing), state == "complete")
                self.assertNotIn("localhost", str(calls))
                saved = next(row for row in self.store.events() if row["id"] == record["id"])
                self.assertEqual(saved["state"], "delivered" if state == "complete" else "attention")

    async def test_local_api_secret_redaction_and_read_only_credentials_test(self):
        api = FastAPI()
        api.include_router(feishu.router)
        with patch.object(feishu, "store", self.store), patch.object(feishu, "controller", self.controller):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8001") as client:
                response = await client.get("/api/feishu")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("test-secret", response.text)
                self.assertTrue(response.json()["config"]["has_app_secret"])
                self.assertEqual((await client.post("/api/feishu/test")).status_code, 403)
                response = await client.post("/api/feishu/test", headers={"Origin": "http://localhost:5173"})
                self.assertTrue(response.json()["ok"])
                self.assertFalse(self.sdk.calls)
                self.assertEqual((await client.get("/api/feishu", headers={"X-ModelDesk-Remote": "phone"})).status_code, 403)
                self.assertEqual((await client.get("/api/feishu", headers={"Origin": "https://evil.invalid"})).status_code, 403)
        self.assertEqual((self.store.directory / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.store.directory / "receipts.sqlite3").stat().st_mode & 0o777, 0o600)

    async def test_config_secret_preservation_rotation_scope_and_disabled_listener(self):
        revision = self.store.config()["revision"]
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", app_secret="", bindings=[self.binding]))
        self.assertEqual(self.store.config()["revision"], revision)
        self.assertEqual(self.store.config()["app_secret"], "test-secret")
        with self.assertRaises(HTTPException):
            self.store.save_config(ConfigInput(enabled=True, app_id="cli_other", bindings=[]))
        self.store.save_config(ConfigInput(enabled=False, app_id="cli_test", clear_secret=True, bindings=[]))
        self.assertFalse(self.store.public_config()["has_app_secret"])
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock()) as start:
            await self.controller.restart_worker()
        start.assert_not_awaited()
        self.assertEqual(self.controller.public()["status"]["state"], "not_configured")

    async def test_worker_normalization_checks_bot_mention_and_preserves_other_mentions(self):
        message = NS(message_id="om_x", chat_id="oc_one", chat_type="group", message_type="text", create_time=str(int(time.time() * 1000)),
                     content=json.dumps({"text": "@_user_1 /modeldesk task 请参考 @_user_2 的要求"}),
                     mentions=[NS(key="@_user_1", name="ModelDesk", id=NS(open_id="ou_bot")), NS(key="@_user_2", name="Alice", id=NS(open_id="ou_alice"))])
        data = NS(header=NS(app_id="cli_test"), event=NS(message=message, sender=NS(sender_type="user", sender_id=NS(open_id="ou_person"))))
        result = normalize_event(data, "cli_test", "ou_bot")
        self.assertEqual(result["text"], "/modeldesk task 请参考 @Alice 的要求")
        self.assertFalse(result["leading_user_mention"])
        self.assertIsNone(normalize_event(data, "cli_test", "ou_otherbot"))
        message.content = json.dumps({"text": "@_user_1 /modeldesk task @_user_2 的要求"})
        result = normalize_event(data, "cli_test", "ou_bot")
        self.assertEqual(result["text"], "/modeldesk task @Alice 的要求")
        self.assertTrue(result["leading_user_mention"])
        message.content = json.dumps({"text": "@_user_1 @Alice 请参考 @_user_2 的要求"})
        self.assertFalse(normalize_event(data, "cli_test", "ou_bot")["leading_user_mention"])

    async def test_mobile_rich_post_keeps_text_links_and_rejects_unsupported_assets(self):
        message = NS(message_id='om_mobile', chat_id='oc_one', chat_type='p2p', message_type='post',
                     create_time=str(int(time.time() * 1000)), mentions=[])
        data = NS(header=NS(app_id='cli_test'), event=NS(message=message, sender=NS(sender_type='user', sender_id=NS(open_id='ou_person'))))
        content = {'title': '', 'content': [[{'tag':'text','text':'/modeldesk\ntask 写报告'}],
                                           [{'tag':'a','text':'资料','href':'https://example.com/source'}]]}
        message.content = json.dumps(content)
        result = normalize_event(data, 'cli_test', 'ou_bot')
        self.assertEqual(result['text'], '/modeldesk\ntask 写报告\n资料 (https://example.com/source)')
        identifier = self.store.ingest(result)
        self.assertIsNotNone(identifier)
        self.assertEqual(self.store.events()[0]['command'], 'task 写报告\n资料 (https://example.com/source)')
        content['content'][1].append({'tag':'img','image_key':'unread_asset'})
        message.content, message.message_id = json.dumps(content), 'om_with_image'
        self.store.ingest(normalize_event(data, 'cli_test', 'ou_bot'))
        self.assertEqual(self.store.events()[-1]['state'], 'rejected')
        message.content = '{malformed'
        self.assertEqual(normalize_event(data, 'cli_test', 'ou_bot')['input_error'], 'invalid_content')
        with patch("backend.feishu_worker.emit") as emit:
            SanitizedConnectionLog().emit(logging.LogRecord("Lark", logging.INFO, "", 1, "connected to wss://secret-token", (), None))
        emit.assert_called_once_with("connected")

    async def test_disable_is_prompt_during_inflight_reply_and_blocks_next_chunk(self):
        self.received()
        with patch.object(groups, "submit_message", AsyncMock(side_effect=self.submit)):
            await self.controller.tick()
        record = self.store.events()[0]
        self.jobs.jobs[record["job_id"]].update(status="complete", autonomy={"state": "complete", "notification_id": "n", "reason": "done"},
                   result={"delivery": {"content": "must not send after disable", "documents": []}})
        started, release = threading.Event(), threading.Event()
        original = self.sdk.reply
        def blocked(*args):
            started.set()
            release.wait(3)
            return original(*args)
        with patch.object(self.sdk, "reply", side_effect=blocked), patch("backend.autonomy.completion_problem", return_value=None), \
                patch.object(feishu, "store", self.store), patch.object(feishu, "controller", self.controller):
            ticking = asyncio.create_task(self.controller.tick())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                response = await asyncio.wait_for(feishu.configure(ConfigInput(enabled=False, app_id="cli_test", bindings=[self.binding])), 1)
                self.assertFalse(response["config"]["enabled"])
            finally:
                release.set()
                await ticking
        self.assertEqual(len(self.sdk.calls), 2)  # initial ack + already-dispatched summary only
        self.assertEqual(self.store.events()[0]["state"], "needs_attention")
        self.assertIn("已停用", self.store.events()[0]["reason"])

    async def test_corrupt_optional_configuration_does_not_fail_service_startup(self):
        path = self.store.directory / "config.json"
        path.write_text('{"app_secret":"preserve-this-malformed-file",')
        try:
            await self.controller.start()
            self.assertIsNotNone(self.controller.task)
            public = self.controller.public()
            self.assertEqual(public["status"]["state"], "error")
            self.assertNotIn("preserve-this", json.dumps(public))
            self.assertIsNone(self.controller.worker)
            self.assertIn("preserve-this", path.read_text())
        finally:
            await self.controller.shutdown()

    async def test_all_groups_list_switch_direct_to_and_at_routing_preserves_inflight_revision(self):
        from backend import projects
        project2_id, room2_id = str(uuid.uuid4()), str(uuid.uuid4())
        rooms_data = [
            {
                "id": self.room_id,
                "project_id": self.project_id,
                "title": "产品规划群",
                "team_snapshot": {"members": [{"id": "a"}, {"id": "b"}]},
                "updated_at": "2026-11-21T10:00:00+00:00",
            },
            {
                "id": room2_id,
                "project_id": project2_id,
                "title": "AI 架构设计",
                "team_snapshot": {"members": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
                "updated_at": "2026-11-21T09:00:00+00:00",
            },
        ]
        registry = {
            self.project_id: {"id": self.project_id, "name": "核心产品"},
            project2_id: {"id": project2_id, "name": "研发平台"},
        }

        def fake_room_get(rid, pid=None):
            for item in rooms_data:
                if item["id"] == rid and item["project_id"] == pid:
                    return item
            raise HTTPException(404, "项目群不存在")

        destinations = []

        async def submit_multi(room, request, project, **kwargs):
            record = next(row for row in self.store.events() if row["job_id"] == kwargs["job_id"])
            self.assertEqual(record["state"], "dispatching")
            self.assertEqual(record["target_room_id"], room)
            self.assertEqual(record["target_project_id"], project)
            destinations.append((project, room, request.prompt, request.context_mode))
            job = {
                "id": kwargs["job_id"],
                "mode": "group",
                "status": "running",
                "project_id": project,
                "group_room_id": room,
                "conversation_id": room,
                "group_context_mode": request.context_mode,
                "external_source": kwargs["external_source"],
                "events": [],
                "created_at": workbench.now(),
                "autonomy": {"state": "running"},
            }
            self.jobs.jobs[job["id"]] = job
            self.jobs.wakeups[job["id"]] = asyncio.Event()
            return job

        with patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry), \
             patch.object(groups, "submit_message", AsyncMock(side_effect=submit_multi)) as submit_mock:
            # First verify allow_all_groups=False (on oc_locked) stays strictly scoped and never leaks Room 2.
            self.store.save_config(ConfigInput(
                enabled=True,
                app_id="cli_test",
                bindings=[
                    {**self.binding, "allow_all_groups": True, "natural_language_tasks": True},
                    {**self.binding, "chat_id": "oc_locked", "allow_all_groups": False, "natural_language_tasks": True},
                ],
            ))
            revision = self.store.config()["revision"]

            self.received(chat_id="oc_locked", text="/modeldesk groups")
            self.received(chat_id="oc_locked", text="/modeldesk use AI 架构设计")
            self.received(chat_id="oc_locked", text="/modeldesk to 2 紧急调研")
            self.received(chat_id="oc_locked", text="@AI 架构设计 不得派发")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            self.assertEqual(len(self.sdk.calls), 4)
            self.assertIn("产品规划群", self.sdk.calls[0][3]["text"])
            self.assertNotIn("AI 架构设计", self.sdk.calls[0][3]["text"])
            self.assertIn("未开启", self.sdk.calls[1][3]["text"])
            self.assertIn("未开启", self.sdk.calls[2][3]["text"])
            self.assertIn("未开启", self.sdk.calls[3][3]["text"])

            self.sdk.calls.clear()

            # /modeldesk groups on oc_one lists both rooms and marks active Room 1 with ★.
            self.received(text="/modeldesk groups")
            await self.controller.tick()
            groups_reply = self.sdk.calls[-1][3]["text"]
            self.assertIn("1. ★ 核心产品 · 产品规划群", groups_reply)
            self.assertIn("2. 研发平台 · AI 架构设计", groups_reply)
            self.assertIn("/modeldesk use", groups_reply)
            self.assertIn("/modeldesk to", groups_reply)

            # Start Task 1 in Room 1, then switch default room to Room 2 without rotating revision.
            task1 = self.received(text="/modeldesk task 写产品路线图")
            await self.controller.tick()
            self.assertEqual(destinations[-1], (self.project_id, self.room_id, "写产品路线图", "fresh"))

            self.received(text="/modeldesk use AI 架构设计")
            await self.controller.tick()
            self.assertEqual(self.store.config()["revision"], revision)
            self.assertIn("研发平台 · AI 架构设计", self.sdk.calls[-1][3]["text"])
            self.assertEqual(
                self.store.active_routes("cli_test", revision),
                {"oc_one": {"project_id": project2_id, "room_id": room2_id}},
            )

            # Start Task 2 (goes to Room 2 via active route).
            task2 = self.received(text="设计微服务边界")
            await self.controller.tick()
            self.assertEqual(destinations[-1], (project2_id, room2_id, "设计微服务边界", "fresh"))

            # Both Room 1 and Room 2 tasks complete and deliver cleanly under the same revision!
            self.jobs.jobs[task1["job_id"]].update(
                status="complete",
                autonomy={"state": "complete", "notification_id": "n-r1", "reason": "done1"},
                result={"delivery": {"content": "路线图定稿", "documents": []}},
            )
            self.jobs.jobs[task2["job_id"]].update(
                status="complete",
                autonomy={"state": "complete", "notification_id": "n-r2", "reason": "done2"},
                result={"delivery": {"content": "架构方案定稿", "documents": []}},
            )
            with patch("backend.autonomy.completion_problem", return_value=None):
                await self.controller.tick()
            by_id = {row["id"]: row for row in self.store.events()}
            self.assertEqual(by_id[task1["id"]]["state"], "delivered")
            self.assertEqual(by_id[task2["id"]]["state"], "delivered")

            # /modeldesk to 1 紧急调研 routes to Room 1; natural-language @<Room 2 title> routes to Room 2!
            # Genuine Feishu user mentions remain prompt text, and cross-room continue stays in Room 1.
            to_rec = self.received(text="/modeldesk to 1 紧急调研")
            at_rec = self.received(text="@AI 架构设计 输出大纲")
            self.received(text="@Alice 请同步进展", leading_user_mention=True)
            cont_rec = self.received(text=f"/modeldesk continue {task1['job_id']} 补充里程碑")
            self.received(text="/modeldesk status")
            await self.controller.tick()
            self.assertEqual(destinations[-4], (self.project_id, self.room_id, "紧急调研", "fresh"))
            self.assertEqual(destinations[-3], (project2_id, room2_id, "输出大纲", "fresh"))
            self.assertEqual(destinations[-2], (project2_id, room2_id, "@Alice 请同步进展", "fresh"))
            self.assertEqual(destinations[-1], (self.project_id, self.room_id, "补充里程碑", "continue"))
            by_id = {row["id"]: row for row in self.store.events()}
            self.assertEqual(by_id[to_rec["id"]]["target_room_id"], self.room_id)
            self.assertEqual(by_id[at_rec["id"]]["target_room_id"], room2_id)
            self.assertEqual(by_id[cont_rec["id"]]["target_room_id"], self.room_id)
            status_text = self.sdk.calls[-1][3]["text"]
            self.assertIn(task2["job_id"], status_text)
            self.assertIn("研发平台 · AI 架构设计", status_text)

    async def test_all_groups_natural_language_switch_and_to_disambiguation(self):
        from backend import projects
        project2_id, room2_id = str(uuid.uuid4()), str(uuid.uuid4())
        rooms_data = [
            {
                "id": self.room_id,
                "project_id": self.project_id,
                "title": "核心群",
                "team_snapshot": {"members": [{"id": "a"}]},
                "updated_at": "2026-11-21T10:00:00+00:00",
            },
            {
                "id": room2_id,
                "project_id": project2_id,
                "title": "核心群",
                "team_snapshot": {"members": [{"id": "a"}]},
                "updated_at": "2026-11-21T09:00:00+00:00",
            },
        ]
        registry = {
            self.project_id: {"id": self.project_id, "name": "项目甲"},
            project2_id: {"id": project2_id, "name": "项目乙"},
        }

        def fake_room_get(rid, pid=None):
            for item in rooms_data:
                if item["id"] == rid and item["project_id"] == pid:
                    return item
            raise HTTPException(404, "项目群不存在")

        destinations = []

        async def submit_multi(room, request, project, **kwargs):
            destinations.append((project, room, request.prompt))
            job = {
                "id": kwargs["job_id"],
                "mode": "group",
                "status": "running",
                "project_id": project,
                "group_room_id": room,
                "conversation_id": room,
                "group_context_mode": request.context_mode,
                "external_source": kwargs["external_source"],
                "events": [],
                "created_at": workbench.now(),
                "autonomy": {"state": "running"},
            }
            self.jobs.jobs[job["id"]] = job
            return job

        self.store.save_config(ConfigInput(
            enabled=True,
            app_id="cli_test",
            bindings=[{**self.binding, "allow_all_groups": True, "natural_language_tasks": True}],
        ))
        revision = self.store.config()["revision"]
        with patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry), \
             patch.object(groups, "submit_message", AsyncMock(side_effect=submit_multi)):
            self.received(text="群列表")
            self.received(text="切换群 核心群")  # ambiguous -> disambiguation reply
            self.received(text="切换群 项目乙/核心群")  # exact project/title -> switches
            bad_to = self.received(text="/modeldesk to 不存在群 任务")
            ambig_to = self.received(text="/modeldesk to 核心群 任务")
            bad_at = self.received(text="@不存在群 执行任务")
            ambig_at = self.received(text="@核心群 执行任务")
            partial_to = self.received(text="/modeldesk to 项目甲 错填的群名 执行任务")
            empty_to = self.received(text="/modeldesk to 2")
            quoted_to = self.received(text='/modeldesk to "项目甲/核心群" 写周报')
            await self.controller.tick()

        self.assertEqual(len(destinations), 1)
        self.assertEqual(destinations[0], (self.project_id, self.room_id, "写周报"))
        self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", revision, "ou_person")["room_id"], room2_id)
        by_id = {row["id"]: row for row in self.store.events()}
        self.assertEqual(by_id[bad_to["id"]]["state"], "delivered")
        self.assertEqual(by_id[ambig_to["id"]]["state"], "delivered")
        self.assertEqual(by_id[empty_to["id"]]["state"], "delivered")
        self.assertEqual(by_id[quoted_to["id"]]["state"], "monitoring")
        for rejected in (bad_to, ambig_to, bad_at, ambig_at, partial_to, empty_to):
            self.assertNotIn(rejected["job_id"], self.jobs.jobs)
        replies = [call[3]["text"] for call in self.sdk.calls if call[0] == "reply"]
        self.assertTrue(any("找到多个匹配" in text for text in replies))
        self.assertTrue(any("未找到匹配" in text for text in replies))

    async def test_all_groups_room_numbers_survive_reorder_and_every_room_is_listed(self):
        rooms = [
            {"id": str(uuid.uuid4()), "project_id": self.project_id,
             "project_name": "测试项目", "title": f"场景 {index:02}"}
            for index in range(49)
        ]
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", bindings=[
            {**self.binding, "allow_all_groups": True, "allowed_sender_ids": ["ou_person", "ou_colleague"]},
        ]))
        with patch.object(feishu, "list_all_rooms", return_value=rooms):
            for page in range(1, 6):
                self.received(text=f"/modeldesk groups {page}")
                await self.controller.tick()
            replies = [call[3]["text"] for call in self.sdk.calls]
            self.assertIn("共 49 个，第 1/5 页", replies[0])
            self.assertIn("第 5/5 页", replies[-1])
            for index, room in enumerate(rooms, 1):
                self.assertIn(f"{index}. 测试项目 · {room['title']}\n{room['id']}", "\n".join(replies))
            self.assertTrue(all(len(reply.encode()) < 4500 for reply in replies))

            self.received(text="/modeldesk use 49", sender_id="ou_colleague")
            self.received(text="/modeldesk groups 6")
            await self.controller.tick()
            self.assertIn("未在此会话为你显示", self.sdk.calls[-2][3]["text"])
            self.assertIn("第 1 至 5 页", self.sdk.calls[-1][3]["text"])

            target = rooms[-1]
            target["title"] = "改名后的最后一群"
            rooms.reverse()
            self.received(text="/modeldesk use 49")
            await self.controller.tick()
            self.assertIn(target["id"], self.sdk.calls[-1][3]["text"])
            rooms.remove(target)
            self.received(text="/modeldesk use 49")
            await self.controller.tick()
            self.assertIn("对应群已不可用", self.sdk.calls[-1][3]["text"])

    async def test_default_target_is_personal_and_queued_tasks_and_replies_keep_their_room(self):
        project2, room2 = str(uuid.uuid4()), str(uuid.uuid4())
        rooms = [
            {"id": self.room_id, "project_id": self.project_id, "project_name": "项目甲", "title": "日常工作"},
            {"id": room2, "project_id": project2, "project_name": "项目乙", "title": "研发评审"},
        ]
        binding = {**self.binding, "allow_all_groups": True, "natural_language_tasks": True,
                   "allowed_sender_ids": ["ou_person", "ou_colleague"]}
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test",
            bindings=[binding, {**binding, "chat_id": "oc_other"}]))

        async def submit(room, request, project, **kwargs):
            job_id = kwargs["job_id"]
            kwargs["admission_check"]()
            job = {"id": job_id, "mode": "group", "status": "running", "project_id": project,
                   "group_room_id": room, "conversation_id": room, "group_context_mode": request.context_mode,
                   "external_source": kwargs["external_source"], "created_at": workbench.now()}
            self.jobs.jobs[job_id] = job
            return job

        with patch.object(feishu, "list_all_rooms", return_value=rooms), \
             patch.object(groups, "submit_message", AsyncMock(side_effect=submit)) as submit_mock, \
             patch.object(self.jobs, "cancel", AsyncMock()) as cancel_mock:
            self.jobs.jobs.update({str(index): {"status": "running"} for index in range(4)})
            queued = self.received(text="处理已排队任务")
            self.received(text=f"/modeldesk use {room2.upper()}")
            colleague = self.received(text="处理同事任务", sender_id="ou_colleague")
            other_chat = self.received(text="处理另一会话", chat_id="oc_other")
            after_switch = self.received(text="处理切换后的任务")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            frozen = {row["id"]: row["target_room_id"] for row in self.store.events() if row["state"] == "received"}
            self.assertEqual(frozen, {queued["id"]: self.room_id, colleague["id"]: self.room_id,
                                     other_chat["id"]: self.room_id, after_switch["id"]: room2})

            self.jobs.jobs.clear()
            restarted = feishu.FeishuController(FeishuStore(self.store.directory), self.jobs, sdk_factory=lambda _: self.sdk)
            await restarted.tick()
            self.assertEqual([call.args[0] for call in submit_mock.await_args_list],
                             [self.room_id, self.room_id, self.room_id, room2])
            ack_index = next(index for index, call in enumerate(self.sdk.calls, 1) if call[1] == queued["message_id"])

            self.jobs.jobs[other_chat["job_id"]]["status"] = "cancelled"
            status = self.received(text="/modeldesk status", sender_id="ou_colleague")
            self.received(text=f"/modeldesk cancel {queued['job_id']}", sender_id="ou_colleague")
            self.received(text=f"/modeldesk cancel {after_switch['job_id']}")
            self.received(text="/modeldesk continue 改报告", parent_id=f"om_reply_{ack_index}", sender_id="ou_colleague")
            self.received(text="@不存在群 处理新任务", parent_id=f"om_reply_{ack_index}")
            self.received(text="修改已排队任务", parent_id=f"om_reply_{ack_index}")
            await restarted.tick()

        status_text = next(call[3]["text"] for call in self.sdk.calls if call[1] == status["message_id"])
        self.assertIn(colleague["job_id"], status_text)
        self.assertNotIn(after_switch["job_id"], status_text)
        self.assertNotIn(queued["job_id"], status_text)
        cancel_mock.assert_awaited_once_with(after_switch["job_id"])
        self.assertEqual(submit_mock.await_count, 5)
        last = submit_mock.await_args
        self.assertEqual((last.args[0], last.args[1].context_mode, last.args[1].prompt),
                         (self.room_id, "continue", "修改已排队任务"))
        self.assertEqual(last.kwargs["external_source"]["parent_job_id"], queued["job_id"])

    async def test_push_group_results_sends_desktop_output_once_and_keeps_chat_results_private(self):
        from backend import document_delivery
        from backend.document_exports import render_document
        document_store = document_delivery.DocumentStore(self.root / "documents")

        # Historical job created BEFORE binding update must never be pushed.
        historical_id = str(uuid.uuid4())
        self.jobs.jobs[historical_id] = {
            "id": historical_id,
            "mode": "group",
            "status": "complete",
            "project_id": self.project_id,
            "group_room_id": self.room_id,
            "conversation_id": self.room_id,
            "created_at": "2020-01-01T00:00:00+00:00",
            "autonomy": {"state": "complete", "notification_id": "hist-1", "reason": "old"},
            "result": {"delivery": {"status": "complete", "title": "旧报告", "content": "旧内容", "documents": [], "media": []}},
        }

        self.store.save_config(ConfigInput(
            enabled=True,
            app_id="cli_test",
            bindings=[{**self.binding, "push_group_results": True}],
        ))
        cutoff = self.store.config()["updated_at"]
        after_cutoff = "2099-01-01T00:00:00+00:00"
        self.assertGreater(after_cutoff, cutoff)

        # Same-chat Feishu-originated job must NOT be double-pushed via sdk.send.
        same_chat_id = str(uuid.uuid4())
        self.jobs.jobs[same_chat_id] = {
            "id": same_chat_id,
            "mode": "group",
            "status": "complete",
            "project_id": self.project_id,
            "group_room_id": self.room_id,
            "conversation_id": self.room_id,
            "created_at": after_cutoff,
            "external_source": {"platform": "feishu", "app_id": "cli_test", "chat_id": "oc_one"},
            "autonomy": {"state": "complete", "notification_id": "feishu-1", "reason": "done"},
            "result": {"delivery": {"status": "complete", "title": "飞书任务", "content": "飞书正文", "documents": [], "media": []}},
        }

        # A second authorized Feishu chat may target this same ModelDesk room.
        private_job_id = str(uuid.uuid4())
        self.jobs.jobs[private_job_id] = {
            **self.jobs.jobs[same_chat_id],
            "id": private_job_id,
            "external_source": {"platform": "feishu", "app_id": "cli_test", "chat_id": "oc_private"},
        }

        # Job in another room must NOT be pushed.
        other_room_id = str(uuid.uuid4())
        self.jobs.jobs[other_room_id] = {
            "id": other_room_id,
            "mode": "group",
            "status": "complete",
            "project_id": self.project_id,
            "group_room_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "created_at": after_cutoff,
            "autonomy": {"state": "complete", "notification_id": "other-1", "reason": "done"},
            "result": {"delivery": {"status": "complete", "title": "其他群", "content": "其他内容", "documents": [], "media": []}},
        }

        # Web-originated group job created AFTER binding update WITH a verified document MUST push once!
        web_job_id = str(uuid.uuid4())
        web_delivery = {"status": "complete", "title": "竞品分析", "content": "网页端生成的完整报告正文", "media": []}
        web_job = {
            "id": web_job_id,
            "mode": "group",
            "status": "complete",
            "project_id": self.project_id,
            "group_room_id": self.room_id,
            "conversation_id": self.room_id,
            "created_at": after_cutoff,
            "result": {"delivery": web_delivery},
        }
        rendered = await render_document(web_job, "group:delivery", "html", "report")
        entry = document_store.save(web_job, "group:delivery", rendered)
        web_delivery["documents"] = [entry]
        self.jobs.jobs[web_job_id] = web_job

        with patch("backend.autonomy.completion_problem", return_value=None), \
             patch.object(document_delivery, "store", document_store):
            await self.controller.tick()
            first_calls = list(self.sdk.calls)
            # Repeated ticks and a restarted controller MUST be 100% idempotent (zero duplicate sends).
            await self.controller.tick()
            restarted = feishu.FeishuController(FeishuStore(self.store.directory), self.jobs, sdk_factory=lambda _: self.sdk)
            await restarted.tick()

        self.assertEqual(self.sdk.calls, first_calls)
        send_calls = [call for call in first_calls if call[0] == "send"]
        upload_calls = [call for call in first_calls if call[0] == "upload"]
        self.assertEqual(len(send_calls), 2)
        self.assertEqual(send_calls[0][1], "oc_one")
        self.assertEqual(send_calls[0][2], "text")
        self.assertIn(web_job_id, send_calls[0][3]["text"])
        self.assertIn("竞品分析", send_calls[0][3]["text"])
        self.assertNotIn(historical_id, str(first_calls))
        self.assertNotIn(same_chat_id, str(first_calls))
        self.assertNotIn(private_job_id, str(first_calls))
        self.assertNotIn(other_room_id, str(first_calls))
        self.assertEqual(len(upload_calls), 1)
        self.assertEqual(send_calls[1][1], "oc_one")
        self.assertEqual(send_calls[1][2], "file")
        self.assertEqual(send_calls[1][3], {"file_key": "file_confirmed"})

        # Integrity check failure must NEVER upload or push unverified documents.
        bad_job_id = str(uuid.uuid4())
        self.jobs.jobs[bad_job_id] = {
            "id": bad_job_id,
            "mode": "group",
            "status": "complete",
            "external_source": {"platform": "routine", "run_id": "scheduled-run"},
            "project_id": self.project_id,
            "group_room_id": self.room_id,
            "conversation_id": self.room_id,
            "created_at": after_cutoff,
            "autonomy": {"state": "complete", "notification_id": "bad-1", "reason": "done"},
            "result": {"delivery": {"status": "complete", "title": "坏成果", "content": "未校验正文", "documents": [entry], "media": []}},
        }
        self.sdk.calls.clear()
        with patch("backend.autonomy.completion_problem", return_value="交付校验未通过"), \
             patch.object(document_delivery, "store", document_store):
            await self.controller.tick()
            await self.controller.tick()
        self.assertEqual(len(self.sdk.calls), 1)
        self.assertEqual(self.sdk.calls[0][0], "send")
        self.assertEqual(self.sdk.calls[0][2], "text")
        self.assertIn("校验未通过", self.sdk.calls[0][3]["text"])
        self.assertFalse(any(call[0] == "upload" for call in self.sdk.calls))

    async def test_room_binding_api_upsert_conflict_replace_delete_and_local_only(self):
        from backend import projects
        project2_id, room2_id = str(uuid.uuid4()), str(uuid.uuid4())
        rooms_data = [
            {
                "id": self.room_id,
                "project_id": self.project_id,
                "title": "群一",
                "team_snapshot": {"members": [{"id": "a"}]},
                "updated_at": "2026-11-21T10:00:00+00:00",
            },
            {
                "id": room2_id,
                "project_id": project2_id,
                "title": "群二",
                "team_snapshot": {"members": [{"id": "a"}, {"id": "b"}]},
                "updated_at": "2026-11-21T09:00:00+00:00",
            },
        ]
        registry = {
            self.project_id: {"id": self.project_id, "name": "项目一"},
            project2_id: {"id": project2_id, "name": "项目二"},
        }

        def fake_room_get(rid, pid=None):
            for item in rooms_data:
                if item["id"] == rid and item["project_id"] == pid:
                    return item
            raise HTTPException(404, "项目群不存在")

        # Seed owner_sender_ids + secret and an active chat route to verify preservation & cleanup.
        self.store.save_config(ConfigInput(
            enabled=False,
            app_id="cli_test",
            app_secret="test-secret",
            owner_sender_ids=["ou_owner"],
            bindings=[],
        ))
        api = FastAPI()
        api.include_router(feishu.router)
        local_headers = {"Origin": "http://localhost:5173"}

        with patch.object(feishu, "store", self.store), \
             patch.object(feishu, "controller", self.controller), \
             patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry), \
             patch.object(self.controller, "restart_worker", AsyncMock()) as restart_mock:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8001") as client:
                # Remote / cross-origin access is strictly rejected with 403.
                self.assertEqual((await client.get("/api/feishu/rooms", headers={"X-ModelDesk-Remote": "phone"})).status_code, 403)
                self.assertEqual((await client.put(f"/api/feishu/rooms/{self.room_id}/binding", json={"chat_id": "oc_one"})).status_code, 403)
                self.assertEqual((await client.put(
                    f"/api/feishu/rooms/{self.room_id}/binding",
                    headers={"Origin": "http://localhost:5173", "X-ModelDesk-Remote": "phone"},
                    json={"chat_id": "oc_one"},
                )).status_code, 403)
                self.assertEqual((await client.delete(
                    f"/api/feishu/rooms/{self.room_id}/binding/oc_one",
                    headers={"Origin": "https://evil.invalid"},
                )).status_code, 403)

                # GET /api/feishu/rooms lists all rooms without ever leaking app_secret.
                resp = await client.get("/api/feishu/rooms")
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn("test-secret", resp.text)
                body = resp.json()
                self.assertEqual(len(body["rooms"]), 2)
                self.assertEqual(body["active_routes"], {})
                self.assertTrue(body["config"]["has_app_secret"])

                # PUT binds oc_one to Room 1 with enable_connection=True, restarts worker, preserves secret & owners.
                resp = await client.put(
                    f"/api/feishu/rooms/{self.room_id}/binding",
                    headers=local_headers,
                    json={
                        "chat_id": "oc_one",
                        "name": "测试飞书群",
                        "project_id": self.project_id,
                        "allowed_sender_ids": ["ou_person"],
                        "allow_all_groups": True,
                        "push_group_results": True,
                        "enable_connection": True,
                    },
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertNotIn("test-secret", resp.text)
                self.assertTrue(resp.json()["config"]["enabled"])
                self.assertEqual(len(resp.json()["config"]["bindings"]), 1)
                self.assertTrue(resp.json()["config"]["bindings"][0]["allow_all_groups"])
                self.assertTrue(resp.json()["config"]["bindings"][0]["push_group_results"])
                self.assertEqual(self.store.config()["app_secret"], "test-secret")
                self.assertEqual(self.store.config()["owner_sender_ids"], ["ou_owner"])
                restart_mock.assert_awaited_once()

                # Record a chat route for oc_one to ensure rebinding clears stale routes.
                rev = self.store.config()["revision"]
                self.store.set_chat_route("cli_test", "oc_one", rev, "ou_person", project2_id, room2_id)
                self.assertIn("oc_one", (await client.get("/api/feishu/rooms")).json()["active_routes"])

                # Conflict: binding oc_one to Room 2 without replace_existing returns 409.
                conflict = await client.put(
                    f"/api/feishu/rooms/{room2_id}/binding",
                    headers=local_headers,
                    json={"chat_id": "oc_one", "project_id": project2_id, "name": "尝试抢绑"},
                )
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(self.store.config()["bindings"][0]["room_id"], self.room_id)

                # Succeeds with replace_existing=True, clears stale chat_routes, preserves secret & owners.
                replaced = await client.put(
                    f"/api/feishu/rooms/{room2_id}/binding",
                    headers=local_headers,
                    json={"chat_id": "oc_one", "project_id": project2_id, "name": "改绑群二", "replace_existing": True},
                )
                self.assertEqual(replaced.status_code, 200, replaced.text)
                self.assertEqual(replaced.json()["config"]["bindings"][0]["room_id"], room2_id)
                self.assertEqual(replaced.json()["active_routes"], {})
                self.assertEqual(self.store.config()["app_secret"], "test-secret")
                self.assertEqual(self.store.config()["owner_sender_ids"], ["ou_owner"])

                # DELETE for mismatched room returns 404; DELETE for bound room removes it cleanly.
                not_found = await client.delete(f"/api/feishu/rooms/{self.room_id}/binding/oc_one", headers=local_headers)
                self.assertEqual(not_found.status_code, 404)
                deleted = await client.delete(
                    f"/api/feishu/rooms/{room2_id}/binding/oc_one",
                    params={"project_id": project2_id},
                    headers=local_headers,
                )
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertNotIn("test-secret", deleted.text)
                self.assertEqual(deleted.json()["config"]["bindings"], [])
                self.assertEqual(self.store.config()["app_secret"], "test-secret")
                self.assertEqual(self.store.config()["owner_sender_ids"], ["ou_owner"])

                # Missing credentials returns 422 on room binding upsert.
                self.store.save_config(ConfigInput(enabled=False, app_id="cli_test", clear_secret=True, bindings=[]))
                unconfigured = await client.put(
                    f"/api/feishu/rooms/{self.room_id}/binding",
                    headers=local_headers,
                    json={"chat_id": "oc_one", "project_id": self.project_id},
                )
                self.assertEqual(unconfigured.status_code, 422)

        self.assertEqual((self.store.directory / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.store.directory / "receipts.sqlite3").stat().st_mode & 0o777, 0o600)

    async def test_smart_natural_language_discovery_fuzzy_switch_current_help_and_false_positive_guard(self):
        from backend import projects
        mst_project, dev_room = str(uuid.uuid4()), str(uuid.uuid4())
        rd_project, review_room = str(uuid.uuid4()), str(uuid.uuid4())
        rooms_data = [
            {
                "id": self.room_id,
                "project_id": self.project_id,
                "title": "article",
                "team_snapshot": {"members": [{"id": "m1", "name": "产品经理"}, {"id": "m2", "name": "总编辑"}]},
                "updated_at": "2026-11-20T10:00:00+00:00",
            },
            {
                "id": dev_room,
                "project_id": mst_project,
                "title": "project development项目群",
                "team_snapshot": {"members": [{"id": "a1", "name": "架构师"}, {"id": "a2", "name": "工程师"}]},
                "updated_at": "2026-11-22T12:00:00+00:00",
            },
            {
                "id": review_room,
                "project_id": rd_project,
                "title": "代码审查群聊",
                "team_snapshot": {"members": [{"id": "r1", "name": "评审员"}]},
                "updated_at": "2026-11-21T12:00:00+00:00",
            },
        ]
        registry = {
            self.project_id: {"id": self.project_id, "name": "2.21 实际交付验收"},
            mst_project: {"id": mst_project, "name": "MST"},
            rd_project: {"id": rd_project, "name": "研发中心"},
        }

        def fake_room_get(rid, pid=None):
            for item in rooms_data:
                if item["id"] == rid and item["project_id"] == pid:
                    return item
            raise HTTPException(404, "项目群不存在")

        destinations = []

        async def submit_multi(room, request, project, **kwargs):
            destinations.append((project, room, request.prompt, request.context_mode))
            job = {
                "id": kwargs["job_id"],
                "mode": "group",
                "status": "running",
                "project_id": project,
                "group_room_id": room,
                "conversation_id": room,
                "group_context_mode": request.context_mode,
                "external_source": kwargs["external_source"],
                "events": [],
                "created_at": workbench.now(),
                "autonomy": {"state": "running"},
            }
            self.jobs.jobs[job["id"]] = job
            return job

        self.store.save_config(ConfigInput(
            enabled=True,
            app_id="cli_test",
            bindings=[
                {**self.binding, "allow_all_groups": True, "natural_language_tasks": True},
                {**self.binding, "chat_id": "oc_locked", "allow_all_groups": False, "natural_language_tasks": True},
            ],
        ))
        revision = self.store.config()["revision"]

        with patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry), \
             patch.object(groups, "submit_message", AsyncMock(side_effect=submit_multi)) as submit_mock:
            # 1. Real-user question finds "project development项目群", reserves number 1, NEVER starts a task!
            find_rec = self.received(text="你能找到我的project development吗")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            self.assertEqual(find_rec["command"], "find project development")
            find_reply = self.sdk.calls[-1][3]["text"]
            self.assertIn("MST · project development项目群", find_reply)
            self.assertIn(dev_room, find_reply)
            self.assertIn("切换群 1", find_reply)
            self.assertEqual(self.store.shown_room_numbers(find_rec), {"1": dev_room})

            # 2. Conversational group search & no-match fallback with recent rooms
            self.received(text="看看有没有代码审查群聊")
            self.received(text="找群 不存在的关键词xyz")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            self.assertIn("研发中心 · 代码审查群聊", self.sdk.calls[-2][3]["text"])
            no_match_reply = self.sdk.calls[-1][3]["text"]
            self.assertIn("未找到包含「不存在的关键词xyz」的项目群", no_match_reply)
            self.assertIn("2.21 实际交付验收 · article", no_match_reply)
            self.assertIn("最近活跃的项目群", no_match_reply)

            # 3. Current group query & natural help query never create tasks
            self.received(text="当前群")
            self.received(text="现在在哪个群呢？")
            self.received(text="怎么用？")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            current_reply = self.sdk.calls[-3][3]["text"]
            self.assertIn("当前默认项目群：2.21 实际交付验收 · article", current_reply)
            self.assertIn("产品经理", current_reply)
            self.assertIn("找群 关键词", current_reply)
            self.assertIn("当前默认项目群：2.21 实际交付验收 · article", self.sdk.calls[-2][3]["text"])
            help_reply = self.sdk.calls[-1][3]["text"]
            self.assertIn("当前默认项目群：2.21 实际交付验收 · article", help_reply)
            self.assertIn("/modeldesk task", help_reply)
            self.assertIn("找群 关键词", help_reply)

            # 4. Fuzzy switch works for "切换群 project development", "切换到 代码审查", compact form, and reserved number "切换群 1"!
            self.received(text="切换群 project development")
            await self.controller.tick()
            self.assertEqual(self.store.config()["revision"], revision)
            self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", revision, "ou_person")["room_id"], dev_room)
            self.assertIn("MST · project development项目群", self.sdk.calls[-1][3]["text"])

            self.received(text="当前群")
            await self.controller.tick()
            self.assertIn("MST · project development项目群", self.sdk.calls[-1][3]["text"])
            self.assertIn("架构师", self.sdk.calls[-1][3]["text"])

            self.received(text="切换到 代码审查")
            await self.controller.tick()
            self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", revision, "ou_person")["room_id"], review_room)

            # Switching via the number reserved by "你能找到我的project development吗" works cleanly!
            self.received(text="切换群 1")
            await self.controller.tick()
            self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", revision, "ou_person")["room_id"], dev_room)

            # 5. Locked chat (allow_all_groups=False) never leaks other rooms on natural discovery or current
            self.received(chat_id="oc_locked", text="你能找到我的project development吗")
            self.received(chat_id="oc_locked", text="当前群")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            locked_find = self.sdk.calls[-2][3]["text"]
            locked_current = self.sdk.calls[-1][3]["text"]
            self.assertNotIn("project development", locked_find)
            self.assertNotIn("MST", locked_find)
            self.assertIn("2.21 实际交付验收 · article", locked_find)
            self.assertIn("2.21 实际交付验收 · article", locked_current)
            self.assertNotIn("MST", locked_current)

        # 6. Compact switch, strict /modeldesk to (allow_fuzzy=False), false-positive guard, and reply continuation guard
        self.store.save_config(ConfigInput(
            enabled=True,
            app_id="cli_test",
            bindings=[{**self.binding, "chat_id": "oc_two", "allow_all_groups": True, "natural_language_tasks": True}],
        ))
        rev2 = self.store.config()["revision"]
        with patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry), \
             patch.object(groups, "submit_message", AsyncMock(side_effect=submit_multi)) as submit_mock:
            # Compact no-space switch
            self.received(chat_id="oc_two", text="切换到project development")
            await self.controller.tick()
            self.assertEqual(self.store.get_chat_route("cli_test", "oc_two", rev2, "ou_person")["room_id"], dev_room)

            # parse_to_command keeps allow_fuzzy=False (strict matching for /modeldesk to)
            self.received(chat_id="oc_two", text="/modeldesk to project development 写方案")
            await self.controller.tick()
            submit_mock.assert_not_awaited()
            self.assertIn("未找到匹配", self.sdk.calls[-1][3]["text"])

            # False-positive guard: real task with "找一下" + task verb STILL executes as a task in dev_room!
            task_rec = self.received(chat_id="oc_two", text="帮我找一下竞品资料并写报告")
            await self.controller.tick()
            self.assertEqual(submit_mock.await_count, 1)
            self.assertEqual(destinations[-1], (mst_project, dev_room, "帮我找一下竞品资料并写报告", "fresh"))
            ack_reply_id = f"om_reply_{len(self.sdk.calls)}"
            # Replying to a task receipt with a discovery-like question continues the task instead of hijacking!
            self.received(chat_id="oc_two", text="你能找到我的project development吗", parent_id=ack_reply_id)
            await self.controller.tick()
            self.assertEqual(submit_mock.await_count, 2)
            self.assertEqual(destinations[-1], (mst_project, dev_room, "你能找到我的project development吗", "continue"))
            self.assertEqual(self.jobs.jobs[self.store.events()[-1]["job_id"]]["external_source"]["parent_job_id"], task_rec["job_id"])

    async def test_activate_room_endpoint_local_only_routes_notifications_open_chat_and_direct(self):
        from backend import projects
        mst_project, dev_room = str(uuid.uuid4()), str(uuid.uuid4())
        rooms_data = [
            {
                "id": self.room_id,
                "project_id": self.project_id,
                "title": "article",
                "team_snapshot": {"members": [{"id": "a"}]},
                "updated_at": "2026-11-20T10:00:00+00:00",
            },
            {
                "id": dev_room,
                "project_id": mst_project,
                "title": "project development项目群",
                "team_snapshot": {"members": [{"id": "b"}]},
                "updated_at": "2026-11-22T10:00:00+00:00",
            },
        ]
        registry = {
            self.project_id: {"id": self.project_id, "name": "2.21 实际交付验收"},
            mst_project: {"id": mst_project, "name": "MST"},
        }

        def fake_room_get(rid, pid=None):
            for item in rooms_data:
                if item["id"] == rid:
                    if item["project_id"] != pid:
                        raise HTTPException(403, "项目群不属于当前项目")
                    return item
            raise HTTPException(404, "项目群不存在")

        api = FastAPI()
        api.include_router(feishu.router)
        local_headers = {"Origin": "http://localhost:5173"}

        with patch.object(feishu, "store", self.store), \
             patch.object(feishu, "controller", self.controller), \
             patch.object(groups.store, "get", side_effect=fake_room_get), \
             patch.object(groups.store, "_rooms", return_value=rooms_data), \
             patch.object(projects.store, "read_registry", return_value=registry):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8001") as client:
                # 1. Local-only security: missing Origin, X-ModelDesk-Remote, and cross-origin are rejected with 403.
                activate_url = f"/api/feishu/rooms/{dev_room}/activate"
                self.assertEqual((await client.post(activate_url, json={"project_id": mst_project})).status_code, 403)
                self.assertEqual((await client.post(
                    activate_url,
                    headers={"Origin": "http://localhost:5173", "X-ModelDesk-Remote": "phone"},
                    json={"project_id": mst_project},
                )).status_code, 403)
                self.assertEqual((await client.post(
                    activate_url,
                    headers={"Origin": "https://evil.invalid"},
                    json={"project_id": mst_project},
                )).status_code, 403)

                # 2. Missing room (404), mismatched project (403), missing allow_all_groups binding (409).
                self.assertEqual((await client.post(
                    f"/api/feishu/rooms/{uuid.uuid4()}/activate",
                    headers=local_headers,
                    json={"project_id": mst_project},
                )).status_code, 404)
                self.assertEqual((await client.post(
                    activate_url,
                    headers=local_headers,
                    json={"project_id": self.project_id},
                )).status_code, 403)
                # Initial config only has a direct binding to self.room_id (allow_all_groups=False).
                self.assertEqual((await client.post(
                    activate_url,
                    headers=local_headers,
                    json={"project_id": mst_project},
                )).status_code, 409)

                # 3. Direct binding to self.room_id succeeds with already_direct=True and notifies when requested.
                direct_resp = await client.post(
                    f"/api/feishu/rooms/{self.room_id}/activate",
                    headers=local_headers,
                    json={"project_id": self.project_id, "notify_feishu": True},
                )
                self.assertEqual(direct_resp.status_code, 200, direct_resp.text)
                self.assertNotIn("test-secret", direct_resp.text)
                direct_body = direct_resp.json()
                self.assertTrue(direct_body["ok"])
                self.assertTrue(direct_body["already_direct"])
                self.assertEqual(direct_body["updated_chats"], ["oc_one"])
                self.assertTrue(direct_body["feishu_notified"])
                self.assertIsNone(direct_body["notify_error"])
                self.assertEqual(len(self.sdk.calls), 1)
                self.assertEqual(self.sdk.calls[0][0], "send")

                # 4. Allowlisted allow_all_groups=True binding updates all allowed senders WITHOUT rotating revision!
                self.sdk.calls.clear()
                self.store.save_config(ConfigInput(
                    enabled=True,
                    app_id="cli_test",
                    owner_sender_ids=["ou_owner"],
                    bindings=[
                        {
                            **self.binding,
                            "allowed_sender_ids": ["ou_person", "ou_colleague"],
                            "allow_all_groups": True,
                            "natural_language_tasks": True,
                        },
                        {
                            **self.binding,
                            "chat_id": "oc_open",
                            "allowed_sender_ids": [],
                            "allow_all_groups": True,
                            "natural_language_tasks": True,
                        },
                    ],
                ))
                rev = self.store.config()["revision"]
                inflight = self.received(text="保持在旧群的在途任务")
                # Seed an event from ou_known in oc_open so known_senders_for_chat sees ou_known.
                self.received(chat_id="oc_open", sender_id="ou_known", text="你好")

                # Activate dev_room on oc_one specifically with notify_feishu=True
                resp = await client.post(
                    activate_url,
                    headers=local_headers,
                    json={"project_id": mst_project, "chat_id": "oc_one", "notify_feishu": True},
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertNotIn("test-secret", resp.text)
                body = resp.json()
                self.assertTrue(body["ok"])
                self.assertFalse(body["already_direct"])
                self.assertEqual(body["room_id"], dev_room)
                self.assertEqual(body["project_id"], mst_project)
                self.assertEqual(body["room_label"], "MST · project development项目群")
                self.assertEqual(body["updated_chats"], ["oc_one"])
                self.assertTrue(body["feishu_notified"])
                self.assertIsNone(body["notify_error"])
                self.assertEqual(body["active_routes"], {"oc_one": {"project_id": mst_project, "room_id": dev_room}})
                # Config revision was NOT rotated; inflight event remains valid!
                self.assertEqual(self.store.config()["revision"], rev)
                self.assertTrue(self.store.current(inflight))
                # Both allowlisted senders updated, NO wildcard '*' written for restricted chat.
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", rev, "ou_person")["room_id"], dev_room)
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", rev, "ou_colleague")["room_id"], dev_room)
                self.assertIsNone(self.store.get_chat_route("cli_test", "oc_one", rev, "*"))
                self.assertEqual(len(self.sdk.calls), 1)
                self.assertIn("MST · project development项目群", self.sdk.calls[0][3]["text"])

                # 5. notify_feishu=False sends no outbound message
                self.sdk.calls.clear()
                quiet = await client.post(
                    activate_url,
                    headers=local_headers,
                    json={"project_id": mst_project, "chat_id": "oc_one", "notify_feishu": False},
                )
                self.assertEqual(quiet.status_code, 200, quiet.text)
                self.assertFalse(quiet.json()["feishu_notified"])
                self.assertIsNone(quiet.json()["notify_error"])
                self.assertFalse(self.sdk.calls)

                # 6. Offline Feishu error degrades gracefully (HTTP 200, route saved, sanitized notify_error, no secret leak)
                self.sdk.fail = True
                offline = await client.post(
                    f"/api/feishu/rooms/{self.room_id}/activate",
                    headers=local_headers,
                    json={"project_id": self.project_id, "chat_id": "oc_one", "notify_feishu": True},
                )
                self.assertEqual(offline.status_code, 200, offline.text)
                self.assertNotIn("unconfirmed secret", offline.text)
                self.assertNotIn("test-secret", offline.text)
                off_body = offline.json()
                self.assertTrue(off_body["ok"])
                self.assertFalse(off_body["feishu_notified"])
                self.assertTrue(off_body["notify_error"])
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_one", rev, "ou_person")["room_id"], self.room_id)

                # 7. Open chat (allowed_sender_ids=[]) sets known senders + owners + '*' fallback WITHOUT breaking personal isolation!
                self.sdk.calls.clear()
                open_resp = await client.post(
                    activate_url,
                    headers=local_headers,
                    json={"project_id": mst_project, "chat_id": "oc_open", "notify_feishu": False},
                )
                self.assertEqual(open_resp.status_code, 200, open_resp.text)
                self.assertEqual(open_resp.json()["active_routes"]["oc_open"], {"project_id": mst_project, "room_id": dev_room})
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_open", rev, "ou_known")["room_id"], dev_room)
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_open", rev, "ou_owner")["room_id"], dev_room)
                self.assertEqual(self.store.get_chat_route("cli_test", "oc_open", rev, "*")["room_id"], dev_room)
                # Brand new sender uses '*' fallback -> dev_room
                newcomer_rec = self.received(chat_id="oc_open", sender_id="ou_newcomer", text="新任务")
                self.assertEqual(self.controller.active_target(newcomer_rec), (mst_project, dev_room))
                # If ou_known personally switches to self.room_id, ou_newcomer STILL stays on dev_room!
                switch_rec = self.received(chat_id="oc_open", sender_id="ou_known", text=f"/modeldesk use {self.room_id}")
                await self.controller.accept(switch_rec)
                known_next = self.received(chat_id="oc_open", sender_id="ou_known", text="回到旧群任务")
                self.assertEqual(self.controller.active_target(known_next), (self.project_id, self.room_id))
                self.assertEqual(self.controller.active_target(newcomer_rec), (mst_project, dev_room))

                # 8. Disabled connection or missing secret returns 422
                self.store.save_config(ConfigInput(enabled=False, app_id="cli_test", bindings=[{**self.binding, "allow_all_groups": True}]))
                disabled_resp = await client.post(activate_url, headers=local_headers, json={"project_id": mst_project})
                self.assertEqual(disabled_resp.status_code, 422)
                self.store.save_config(ConfigInput(enabled=False, app_id="cli_test", clear_secret=True, bindings=[]))
                nosecret_resp = await client.post(activate_url, headers=local_headers, json={"project_id": mst_project})
                self.assertEqual(nosecret_resp.status_code, 422)

        self.assertEqual((self.store.directory / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.store.directory / "receipts.sqlite3").stat().st_mode & 0o777, 0o600)


class FeishuSDKTests(unittest.TestCase):
    def test_official_typed_sdk_requests_and_no_retry(self):
        from lark_oapi.api.im.v1 import ReplyMessageResponse, CreateFileResponse, ListChatResponse
        adapter = feishu_sdk.FeishuSDK({"app_id": "cli_fixture", "app_secret": "not-real"})
        client = Mock()
        adapter.client = client
        client.im.v1.message.reply.return_value = ReplyMessageResponse({"code": 0, "data": {"message_id": "om_actual"}})
        self.assertEqual(adapter.reply("om_input", "text", {"text": "正文"}, "dispatch-uuid"), "om_actual")
        request = client.im.v1.message.reply.call_args.args[0]
        self.assertEqual(request.path_params["message_id"] if hasattr(request, "path_params") else request.paths["message_id"], "om_input")
        self.assertEqual(request.body.uuid, "dispatch-uuid")
        self.assertEqual(json.loads(request.body.content), {"text": "正文"})
        client.im.v1.file.create.return_value = CreateFileResponse({"code": 0, "data": {"file_key": "file_actual"}})
        self.assertEqual(adapter.upload({"format": "docx", "filename": "report.docx"}, b"real-file-bytes"), "file_actual")
        self.assertEqual(client.im.v1.file.create.call_args.args[0].body.file_type, "stream")
        client.im.v1.chat.list.return_value = ListChatResponse({"code": 0, "data": {"items": [{"chat_id": "oc_1", "name": "Group"}], "has_more": False}})
        self.assertEqual(adapter.chats()["items"][0]["chat_id"], "oc_1")
        client.im.v1.message.reply.side_effect = TimeoutError("secret")
        with self.assertRaises(TimeoutError):
            adapter.reply("om_input", "text", {"text": "x"}, "same")
        self.assertEqual(client.im.v1.message.reply.call_count, 2)
        self.assertNotIn("secret", feishu_sdk.safe_error(TimeoutError("secret")))

    def test_send_uses_create_message_request_with_uuid_and_no_retry(self):
        from lark_oapi.api.im.v1 import CreateMessageResponse
        adapter = feishu_sdk.FeishuSDK({"app_id": "cli_fixture", "app_secret": "not-real"})
        client = Mock()
        adapter.client = client
        client.im.v1.message.create.return_value = CreateMessageResponse({"code": 0, "data": {"message_id": "om_sent_1"}})
        self.assertEqual(adapter.send("oc_chat", "text", {"text": "推送正文"}, "push-uuid-1"), "om_sent_1")
        request = client.im.v1.message.create.call_args.args[0]
        self.assertIn(("receive_id_type", "chat_id"), request.queries)
        self.assertEqual(request.body.receive_id, "oc_chat")
        self.assertEqual(request.body.msg_type, "text")
        self.assertEqual(json.loads(request.body.content), {"text": "推送正文"})
        self.assertEqual(request.body.uuid, "push-uuid-1")
        self.assertEqual(client.im.v1.message.create.call_count, 1)

        client.im.v1.message.create.return_value = CreateMessageResponse({"code": 0, "data": {}})
        with self.assertRaises(feishu_sdk.FeishuAPIError):
            adapter.send("oc_chat", "text", {"text": "x"}, "push-uuid-2")

        client.im.v1.message.create.side_effect = TimeoutError("secret-token")
        with self.assertRaises(TimeoutError):
            adapter.send("oc_chat", "text", {"text": "x"}, "push-uuid-3")
        self.assertEqual(client.im.v1.message.create.call_count, 3)


if __name__ == "__main__":
    unittest.main()
