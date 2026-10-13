import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI, HTTPException
from backend import workbench
from backend.artifacts import artifact_markdown, selected_artifact


class ArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_native_deliverable_http_download_keeps_failure_and_unknown_ids_honest(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = workbench.JobManager(Path(folder))
            identifier = 'df9d3857-3d01-4c24-9e64-b9343e217f9a'
            manager.jobs[identifier] = {"id": identifier, "mode": "camel_workforce", "status": "complete", "result": {"tasks": [{"id": "draft", "status": "complete", "output": "Old draft"}, {"id": "review", "status": "complete", "output": "Reviewed final article"}], "answer": "Whole transcript", "partial": False}, "events": []}
            app = FastAPI(); app.include_router(workbench.router)
            with patch.object(workbench, 'manager', manager):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    route = f'/api/workbench/jobs/{identifier}/export'
                    reply = await client.get(route, params={"format": "md", "artifact_id": "task:review"})
                    self.assertEqual(reply.status_code, 200)
                    self.assertIn('Reviewed final article', reply.text)
                    self.assertNotIn('Old draft', reply.text)
                    self.assertNotIn('Whole transcript', reply.text)
                    self.assertIn('attachment;', reply.headers['content-disposition'])
                    self.assertEqual((await client.get(route, params={"format": "md", "artifact_id": "task:../../credentials"})).status_code, 404)
                    self.assertEqual((await client.get(route, params={"format": "json", "artifact_id": "task:review"})).status_code, 422)
                    manager.jobs[identifier]['status'] = 'error'
                    manager.jobs[identifier]['result']['partial'] = True
                    partial = await client.get(route, params={"format": "md", "artifact_id": "task:review"})
                    self.assertIn('部分输出', partial.text)
    def test_downloads_select_actual_provider_discussion_and_historical_failure_outputs(self):
        base = {"id": "job", "status": "complete", "events": []}
        chat = {**base, "mode": "chat", "result": {"responses": [{"model": "openai/gpt", "text": "GPT content"}, {"model": "gemini", "text": "Gemini content"}]}}
        self.assertIn('GPT content', artifact_markdown(chat, 'chat:openai/gpt'))
        self.assertNotIn('Gemini content', artifact_markdown(chat, 'chat:openai/gpt'))
        group = {**base, "mode": "group", "result": {"messages": [{"model": "a", "content": "old"}, {"model": "a", "content": "latest"}, {"model": "b", "content": "other"}]}}
        self.assertEqual(selected_artifact(group, 'group:1')['content'], 'latest')
        with self.assertRaises(HTTPException): selected_artifact(group, 'group:0')
        historical = {**base, "mode": "camel_workforce", "status": "error", "result": None, "events": [{"seq": 1, "native_event": {"event_type": "task_failed", "task_id": "child", "error_message": "Source not verified"}}]}
        self.assertIn('Source not verified', artifact_markdown(historical, 'failure:child'))
        self.assertIn('部分输出', artifact_markdown(historical, 'failure:child'))

    def test_html_is_downloaded_as_quoted_source_and_review_keeps_evidence_boundary(self):
        html = {"id": "job", "mode": "crewai", "status": "complete", "result": {"answer": '<script>alert(1)</script>```'}}
        self.assertIn('````\n<script>', artifact_markdown(html, 'workflow:answer'))
        review = {"id": "job", "mode": "review", "status": "complete", "result": {"findings": [{"title": "Empty list", "file": "stats.py", "line_start": 4, "line_end": 6, "body": "Division by zero", "evidence": "sum(xs)/len(xs)"}], "coverage": {"disclaimer": "Only selected snapshot reviewed"}, "unconfirmed": [{"model": "a", "reason": "No observed evidence", "claim": "Needs manual confirmation"}]}}
        exported = artifact_markdown(review, 'review:findings')
        self.assertIn('stats.py:4–6', exported)
        self.assertIn('Needs manual confirmation', exported)
        self.assertIn('sum(xs)/len(xs)', exported)
        self.assertIn('Only selected snapshot reviewed', exported)
        self.assertIn('No observed evidence', exported)

    def test_same_model_group_members_have_independent_latest_artifacts_and_failure_state(self):
        job = {"id": "job", "mode": "group", "status": "complete", "result": {"messages": [
            {"member_id": "writer", "model": "shared", "role": "作者", "content": "writer first"},
            {"member_id": "reviewer", "model": "shared", "role": "审稿人", "content": "reviewer final"},
            {"member_id": "writer", "model": "shared", "role": "作者", "content": "writer final"},
            {"member_id": "reviewer", "model": "shared", "role": "审稿人", "content": "", "error": "unavailable"},
        ]}}
        self.assertEqual(selected_artifact(job, 'group:1')['content'], 'reviewer final')
        self.assertTrue(selected_artifact(job, 'group:1')['partial'])
        self.assertEqual(selected_artifact(job, 'group:2')['content'], 'writer final')
        self.assertFalse(selected_artifact(job, 'group:2')['partial'])
        with self.assertRaises(HTTPException):
            selected_artifact(job, 'group:0')
