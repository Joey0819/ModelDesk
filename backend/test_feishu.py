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
        self.received()
        self.received(text="/modeldesk continue 改成英文")
        submit = AsyncMock(side_effect=self.submit)
        with patch.object(groups, "submit_message", submit):
            await self.controller.tick()
        self.assertEqual(submit.await_count, 2)
        first, second = submit.await_args_list
        self.assertEqual(first.args[1].prompt, "写报告")
        self.assertEqual(first.args[1].context_mode, "fresh")
        self.assertTrue(first.args[1].background_mode)
        self.assertEqual(second.args[1].context_mode, "continue")
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
        self.assertIsNone(normalize_event(data, "cli_test", "ou_otherbot"))

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


if __name__ == "__main__":
    unittest.main()
