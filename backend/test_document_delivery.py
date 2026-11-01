import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi import FastAPI, HTTPException
import httpx

from backend import document_delivery, workbench
from backend.document_exports import render_document


class DocumentDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = document_delivery.DocumentStore(Path(self.temp.name).resolve() / "documents")
        self.job = {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()), "mode": "group", "status": "complete",
                    "result": {"delivery": {"title": "自动交付", "status": "complete", "content": "# 中文报告\n\n交付完整正文。",
                       "media": [], "task_spec": {"documents": {"formats": ["docx", "html"], "layout": "report"}}}}}
        self.addCleanup(patch.stopall)
        patch.object(document_delivery, "store", self.store).start()

    def test_explicit_format_and_page_only_override_planner(self):
        plan = {"document_formats": ["pdf"], "document_layout": "article"}
        self.assertEqual(document_delivery.document_spec({}, plan)["formats"], ["pdf"])
        self.assertEqual(document_delivery.document_spec({"group_document_formats": []}, plan)["formats"], [])
        spec = document_delivery.document_spec({"group_document_formats": ["docx"], "group_document_layout": "compact"}, plan)
        self.assertEqual(spec, {"formats": ["docx"], "layout": "compact", "source": "user"})

    def test_inherited_formats_and_layout_survive_no_content_and_explicit_reset(self):
        job = {"_task_context": {"document_preferences": {"formats": ["docx", "pdf"], "layout": "compact", "source_job_id": str(uuid.uuid4())}}}
        spec = document_delivery.document_spec(job, {"document_formats": [], "document_layout": None})
        self.assertEqual(spec["formats"], ["docx", "pdf"])
        self.assertEqual(spec["layout"], "compact")
        self.assertEqual(document_delivery.document_spec(job, {"document_formats": [], "reset_document_formats": True})["formats"], [])
        self.assertEqual(document_delivery.document_spec({**job, "group_document_formats": []}, {"document_formats": ["pdf"]})["formats"], [])
        self.assertEqual(document_delivery.document_spec(job, {"document_formats": ["html"], "document_layout": "article"})["layout"], "article")

    async def test_real_documents_persist_and_download_after_manager_restart(self):
        delivery = self.job["result"]["delivery"]
        checks = await document_delivery.prepare_delivery_documents(self.job, delivery)
        self.assertTrue(all(item["met"] for item in checks))
        self.assertEqual(len(delivery["documents"]), 2)
        receipt = delivery["documents"][0]
        entry, raw = self.store.read(self.job, receipt["id"])
        self.assertEqual(entry["content_sha256"], hashlib.sha256(delivery["content"].encode()).hexdigest())
        self.assertIn("交付完整正文", "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs))
        manager = workbench.JobManager(Path(self.temp.name) / "jobs")
        manager.jobs[self.job["id"]] = copy.deepcopy(self.job)
        app = FastAPI(); app.include_router(workbench.router)
        with patch.object(workbench, "manager", manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(receipt["url"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, raw)
                self.assertIn("attachment", response.headers["content-disposition"])
                self.assertEqual((await client.get(receipt["url"].split("?")[0])).status_code, 404)
                unknown = receipt["url"].replace(self.job["id"], str(uuid.uuid4()))
                self.assertEqual((await client.get(unknown)).status_code, 404)

    async def test_selected_export_returns_saved_download_url_and_keeps_scope(self):
        manager = workbench.JobManager(Path(self.temp.name) / "jobs")
        manager.jobs[self.job["id"]] = self.job
        app = FastAPI(); app.include_router(workbench.router)
        with patch.object(workbench, "manager", manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                url = f'/api/workbench/jobs/{self.job["id"]}/export'
                reply = await client.get(url, params={"format": "docx", "layout": "compact", "artifact_id": "group:delivery"})
                self.assertEqual(reply.status_code, 200)
                link = reply.headers["x-modeldesk-document-url"]
                self.assertIn(self.job["project_id"], link)
                self.assertEqual((await client.get(link)).content, reply.content)
                self.assertEqual((await client.get(url, params={"format": "pdf"})).status_code, 422)
                self.assertEqual((await client.get(url, params={"format": "docx", "artifact_id": "group:delivery", "layout": "../../private"})).status_code, 422)

    async def test_failed_one_format_preserves_success_and_failure_does_not_become_complete(self):
        delivery = self.job["result"]["delivery"]
        async def render(job, artifact, format, layout):
            if format == "html":
                raise HTTPException(503, "排版引擎不可用")
            return await render_document(job, artifact, format, layout)
        with patch("backend.document_exports.render_document", side_effect=render):
            checks = await document_delivery.prepare_delivery_documents(self.job, delivery)
        self.assertEqual([item["met"] for item in checks], [True, False])
        self.assertEqual(delivery["documents"][0]["format"], "docx")
        self.assertEqual(delivery["document_errors"][0]["format"], "html")

    async def test_changed_content_and_tampered_file_cannot_pass_checks(self):
        delivery = self.job["result"]["delivery"]
        await document_delivery.prepare_delivery_documents(self.job, delivery)
        delivery["content"] += " 已改稿。"
        self.assertFalse(any(check["met"] for check in document_delivery.document_checks(self.job, delivery)))
        receipt = delivery["documents"][0]
        file = Path(self.temp.name) / "documents" / self.job["id"] / receipt["id"] / "content"
        file.write_bytes(b"bad replacement")
        with self.assertRaises(HTTPException) as error:
            self.store.read(self.job, receipt["id"])
        self.assertEqual(error.exception.status_code, 409)

    async def test_symlink_and_cross_project_download_are_refused(self):
        delivery = self.job["result"]["delivery"]
        await document_delivery.prepare_delivery_documents(self.job, delivery)
        receipt = delivery["documents"][0]
        other = {**self.job, "project_id": str(uuid.uuid4())}
        with self.assertRaises(HTTPException): self.store.read(other, receipt["id"])
        file = Path(self.temp.name) / "documents" / self.job["id"] / receipt["id"] / "content"
        file.unlink(); file.symlink_to(Path(self.temp.name) / "outside")
        with self.assertRaises(HTTPException): self.store.read(self.job, receipt["id"])

    async def test_wrong_layout_or_other_artifact_cannot_satisfy_requested_documents(self):
        delivery = self.job["result"]["delivery"]
        await document_delivery.prepare_delivery_documents(self.job, delivery)
        delivery["task_spec"]["documents"]["layout"] = "compact"
        self.assertFalse(any(check["met"] for check in document_delivery.document_checks(self.job, delivery)))
        delivery["task_spec"]["documents"]["layout"] = "report"
        receipt = delivery["documents"][0]
        manifest_path = self.store.files.directory / self.job["id"] / receipt["id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["artifact_id"] = "group:0"
        manifest_path.write_text(json.dumps(manifest))
        self.assertFalse(document_delivery.document_checks(self.job, delivery)[0]["met"])

    async def test_actual_harness_document_failure_is_partial_in_saved_task_and_successful_file(self):
        from backend import coordination, group_delivery
        room = str(uuid.uuid4())
        job = {"id": str(uuid.uuid4()), "project_id": None, "conversation_id": room, "group_room_id": room,
               "mode": "group", "models": ["writer-model", "reviewer-model"], "prompt": "完成协作文章，交付 Word 和 HTML", "status": "running", "events": [],
               "group_harness_version": 1, "group_delivery_mode": "auto", "group_broadcast": True,
               "group_runtime_allowed": True, "group_native_allowed": True, "group_output_format": "text",
               "group_document_formats": ["docx", "html"], "group_document_layout": "report",
               "group_recipient_member_ids": ["writer", "reviewer"], "group_team_snapshot": {"members": [
                   {"id": "writer", "kind": "model", "name": "作者", "model": "writer-model", "role": "写作"},
                   {"id": "reviewer", "kind": "model", "name": "审稿", "model": "reviewer-model", "role": "核查"}]}}
        manager = workbench.JobManager(Path(self.temp.name) / "harness-jobs")
        manager.jobs[job["id"]] = job
        manager.wakeups[job["id"]] = asyncio.Event()
        calls = []
        async def provider(job, model, system, prompt, **kwargs):
            if "你是项目统筹者" in system:
                return json.dumps({"title": "正式交付", "requirements": [{"id": "body", "kind": "text", "description": "完整文章"}],
                                   "steps": [{"id": "draft", "member_id": "writer", "instruction": "写出正文", "expected_output": "完整正文", "depends_on": []}]}), False
            if "最终交付编辑" in system:
                return json.dumps({"title": "文章", "content": "# 实际完成正文\n\n文章内容已经完成。", "missing_outputs": []}), False
            if "你是交付审查者" in system:
                return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [{"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "正文完整"}], "revision_instructions": ""}), False
            calls.append("execute")
            return "完整初稿", False
        async def render(snapshot, artifact, format, layout):
            if format == "html":
                raise HTTPException(503, "HTML 验收模拟故障")
            return await render_document(snapshot, artifact, format, layout)
        with patch.object(group_delivery, "model_output", side_effect=provider), patch("backend.document_exports.render_document", side_effect=render):
            result = await coordination.run(manager, copy.deepcopy(job))
        self.assertEqual(calls, ["execute"])
        self.assertTrue(result["partial_failure"])
        delivery = result["delivery"]
        self.assertEqual(delivery["status"], "partial")
        self.assertEqual(delivery["validation"]["status"], "needs_revision")
        self.assertFalse(next(item["met"] for item in delivery["validation"]["mechanical_checks"] if item["requirement_id"] == "document-html"))
        persisted = json.loads((manager.directory / (job["id"] + ".json")).read_text())
        self.assertEqual(persisted["result"]["delivery"]["status"], "partial")
        entry, raw = self.store.read(job, delivery["documents"][0]["id"])
        self.assertTrue(entry["partial"])
        self.assertIn("部分输出", "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs))
        self.assertTrue(coordination.document_retry_ready(job, delivery))
        for mutate in [
            lambda value: value.update(content=value['content'] + ' unseen edit'),
            lambda value: value['reviews'][-1].update(artifact_version=0),
            lambda value: value['reviews'][-1]['checks'][0].update(met=False),
            lambda value: value['steps'][0].update(status='error'),
            lambda value: value['limitations'].append('其他尚未解决的问题'),
            lambda value: value['validation']['mechanical_checks'].append({'requirement_id': 'body', 'met': False, 'reason': '正文未完成'}),
        ]:
            changed = copy.deepcopy(delivery)
            mutate(changed)
            self.assertFalse(coordination.document_retry_ready(job, changed))
        with patch.object(coordination, 'actual_checks', return_value=[{'requirement_id': 'image', 'met': False}]):
            self.assertFalse(coordination.document_retry_ready(job, delivery))

        previous_content = delivery['content']
        previous_steps = copy.deepcopy(delivery['steps'])
        previous_reviews = copy.deepcopy(delivery['reviews'])
        previous_versions = copy.deepcopy(delivery['versions'])
        # A second packaging failure stays partial without spending another
        # model/Agent call or pretending that the first failure disappeared.
        no_model = AsyncMock(side_effect=AssertionError('Model must not run for packaging-only continuation'))
        no_runtime = AsyncMock(side_effect=AssertionError('Agent must not run for packaging-only continuation'))
        with patch.object(group_delivery, 'model_output', no_model), patch('backend.group_delivery_runtime.run_runtime_step', no_runtime), patch('backend.document_exports.render_document', side_effect=render):
            second_failure = await coordination.run(manager, copy.deepcopy(job), continuing=True)
        self.assertTrue(second_failure['partial_failure'])
        self.assertEqual(second_failure['delivery']['status'], 'partial')
        self.assertEqual(second_failure['delivery']['workflow']['document_retry_count'], 1)
        no_model.assert_not_awaited()
        no_runtime.assert_not_awaited()
        self.assertTrue(coordination.document_retry_ready(job, second_failure['delivery']))

        # The renderer recovers: only real documents are regenerated, from the
        # same reviewed artifact. No worker, editor or reviewer is called.
        with patch.object(group_delivery, 'model_output', no_model), patch('backend.group_delivery_runtime.run_runtime_step', no_runtime):
            recovered = await coordination.run(manager, copy.deepcopy(job), continuing=True)
        no_model.assert_not_awaited()
        no_runtime.assert_not_awaited()
        final = recovered['delivery']
        self.assertFalse(recovered['partial_failure'])
        self.assertEqual(final['status'], 'complete')
        self.assertEqual(final['validation']['status'], 'passed')
        self.assertEqual(final['workflow']['document_retry_count'], 2)
        self.assertEqual(final['content'], previous_content)
        self.assertEqual(final['steps'], previous_steps)
        self.assertEqual(final['reviews'], previous_reviews)
        self.assertEqual(final['versions'], previous_versions)
        self.assertEqual(final['limitations'], [])
        self.assertEqual(len(final['validation']['mechanical_checks']), 3)
        self.assertTrue(all(check['met'] for check in final['validation']['mechanical_checks']))
        entry, raw = self.store.read(job, next(item['id'] for item in final['documents'] if item['format'] == 'docx'))
        self.assertFalse(entry['partial'])
        self.assertNotIn('部分输出', '\n'.join(p.text for p in Document(io.BytesIO(raw)).paragraphs))
