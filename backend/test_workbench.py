"""Focused lifecycle tests; no external model calls or credentials required."""

import asyncio
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from backend import workbench
from backend.workbench import JobManager, JobRequest


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = JobManager(Path(self.temporary.name))
        self.manager.recover()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temporary.cleanup()

    async def complete(self, request):
        job = self.manager.create(request)
        await self.manager.tasks[job["id"]]
        return self.manager.get(job["id"])

    def transport(self, requests, failed_model=None):
        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload["model"] == failed_model:
                return httpx.Response(403, json={"error": "secret-access-token"})
            chunk = {"choices": [{"delta": {"content": "answer-" + payload["model"]}}]}
            return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})

        real_client = httpx.AsyncClient
        return patch.object(workbench.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))

    async def test_concurrent_chat_isolated_then_context_and_sse_replay(self):
        captured = []
        with self.transport(captured):
            first = self.manager.create(JobRequest(mode="chat", prompt="First question", models=["a", "b"]))
            other = self.manager.create(JobRequest(mode="chat", prompt="Other conversation", models=["c"]))
            await asyncio.gather(self.manager.tasks[first["id"]], self.manager.tasks[other["id"]])
            followup = await self.complete(JobRequest(mode="chat", prompt="Continue", models=["a", "b"], conversation_id=first["conversation_id"]))
        self.assertEqual(followup["status"], "complete")
        a = next(request for request in captured if request["model"] == "a" and len(request["messages"]) > 1)
        b = next(request for request in captured if request["model"] == "b" and len(request["messages"]) > 1)
        self.assertEqual(a["messages"][1]["content"], "answer-a")
        self.assertEqual(b["messages"][1]["content"], "answer-b")
        self.assertNotIn("Other conversation", str(a["messages"]))
        replay = [frame async for frame in self.manager.events(followup["id"], after=1)]
        events = [json.loads(frame.split("data: ", 1)[1]) for frame in replay]
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual([event["seq"] for event in events], list(range(2, len(followup["events"]) + 1)))

    async def test_partial_failure_explicit_without_upstream_secret(self):
        with self.transport([], failed_model="blocked"):
            job = await self.complete(JobRequest(mode="chat", prompt="hello", models=["good", "blocked"]))
        self.assertEqual(job["status"], "complete")
        blocked = next(item for item in job["result"]["responses"] if item["model"] == "blocked")
        self.assertIn("403", blocked["error"])
        self.assertNotIn("secret-access-token", json.dumps(job))
        with self.transport([], failed_model="blocked"):
            failed = await self.complete(JobRequest(mode="chat", prompt="hello", models=["blocked"]))
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["events"][-1]["type"], "error")

    async def test_cancel_reaps_owned_worker(self):
        with patch.object(self.manager, "worker_command", return_value=[sys.executable, "-u", "-c", "import time; print('worker started', flush=True); time.sleep(60)"]):
            job = self.manager.create(JobRequest(mode="camel", prompt="test", models=["a", "b"]))
            for _ in range(100):
                process = self.manager.processes.get(job["id"])
                if process:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(process)
            cancelled = await self.manager.cancel(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(process.returncode)
        self.assertNotIn(job["id"], self.manager.processes)
        self.assertNotIn(job["id"], self.manager.tasks)
        self.assertEqual([event["type"] for event in cancelled["events"]].count("cancelled"), 1)
        frames = [frame async for frame in self.manager.events(job["id"])]
        self.assertIn('"type": "cancelled"', frames[-1])

    async def test_long_utf8_framework_event_eof_and_cancelled_partial(self):
        content = "长文本 token 示例 " * 1300
        configuration = {"agents": [{"id": "researcher", "role": "研究员", "goal": "review", "model": "a"}, {"id": "reviewer", "role": "审稿人", "goal": "check", "model": "b"}], "tasks": [], "process": "sequential"}
        script = """import json,sys,time
from pathlib import Path
content = '长文本 token 示例 ' * 1300
message = {'role':'审稿人','model':'b','round':1,'content':content}
Path(sys.argv[1]).write_text(json.dumps({'models':['a','b'],'messages':[message],'partial':sys.argv[2]=='cancel'},ensure_ascii=False))
text = 'MODELDESK_EVENT ' + json.dumps({'type':'framework_message',**message},ensure_ascii=False)
print(text,end='\\n' if sys.argv[2]=='cancel' else '',flush=True)
if sys.argv[2]=='cancel': time.sleep(60)
"""
        def command(job, output):
            return [sys.executable, "-u", "-c", script, str(output), job["prompt"]]
        with patch.object(self.manager, "worker_command", side_effect=command):
            job = self.manager.create(JobRequest(mode="camel", prompt="cancel", models=["a", "b"], workflow=configuration))
            for _ in range(200):
                persisted = self.manager.get(job["id"])
                if any(event["type"] == "framework_message" for event in persisted["events"]):
                    break
                await asyncio.sleep(0.01)
            frame = next(event for event in persisted["events"] if event["type"] == "framework_message")
            self.assertEqual(frame["content"], content)
            cancelled = await self.manager.cancel(job["id"])
            self.assertEqual(cancelled["result"]["messages"][0]["content"], content)
            self.assertTrue(cancelled["result"]["partial"])
            self.assertFalse((self.manager.directory / (job["id"] + ".workflow-input")).exists())
            completed = await self.complete(JobRequest(mode="camel", prompt="eof", models=["a", "b"], workflow=configuration))
            self.assertEqual(completed["status"], "complete")
            eof_frame = next(event for event in completed["events"] if event["type"] == "framework_message")
            self.assertEqual(eof_frame["content"], content)

    async def test_recovery_marks_abandoned_job_interrupted(self):
        blocker = asyncio.Event()
        async def blocked(job):
            await blocker.wait()
        with patch.object(self.manager, "chat", side_effect=blocked):
            job = self.manager.create(JobRequest(mode="chat", prompt="test", models=["a"]))
            await asyncio.sleep(0)
            recovered = JobManager(self.manager.directory)
            recovered.recover()
            loaded = recovered.get(job["id"])
        self.assertEqual(loaded["status"], "interrupted")
        self.assertEqual(loaded["events"][-1]["type"], "error")
        self.assertIn("服务重启", loaded["error"])

    async def test_failed_native_worker_retains_partial_before_cleanup_and_export(self):
        configuration = {"agents": [{"id": "researcher", "role": "研究员", "goal": "review", "model": "a"}], "tasks": []}
        script = """import json,sys
from pathlib import Path
partial={'tasks':[{'id':'research','status':'failed','output':'无法核查原帖：未启用联网工具'}], 'messages':[], 'partial':True,
         'failure':{'task_id':'research','reason':'未启用联网工具'}}
Path(sys.argv[1]).write_text(json.dumps(partial,ensure_ascii=False))
print('RuntimeError: 未启用联网工具',flush=True)
sys.exit(1)
"""
        with patch.object(self.manager, "worker_command", side_effect=lambda job, output: [sys.executable, "-u", "-c", script, str(output)]):
            failed = await self.complete(JobRequest(mode="camel_workforce", prompt="核查原帖", models=["a"], workflow=configuration))
        self.assertEqual(failed["status"], "error")
        self.assertTrue(failed["result"]["partial"])
        self.assertEqual(failed["result"]["tasks"][0]["status"], "failed")
        self.assertIn("未启用联网工具", workbench.markdown_export(failed))
        self.assertFalse(any(event["type"] == "complete" for event in failed["events"]))
        self.assertEqual(failed["events"][-1]["type"], "error")
        loaded = JobManager(self.manager.directory)
        loaded.recover()
        self.assertEqual(loaded.get(failed["id"])["result"], failed["result"])
        self.assertFalse((self.manager.directory / (failed["id"] + ".output")).exists())
        self.assertFalse((self.manager.directory / (failed["id"] + ".workflow-input")).exists())

    def test_old_failed_workforce_exports_observed_native_failure_without_final_answer(self):
        task_id = "native-parent.1"
        job = {"id": str(uuid.uuid4()), "mode": "camel_workforce", "status": "error", "result": None,
               "events": [
                   {"type": "framework_task", "native_event": {"event_type": "task_created", "task_id": task_id, "description": "核查原帖"}},
                   {"type": "framework_task", "native_event": {"event_type": "task_failed", "task_id": task_id, "worker_id": "native-worker", "error_message": "本次没有联网工具，未核实"}},
                   {"type": "error", "message": "原生任务未完成"}]}
        recovered = workbench.export_result(job)
        self.assertTrue(recovered["partial"])
        self.assertEqual(recovered["tasks"][0]["description"], "核查原帖")
        self.assertEqual(recovered["tasks"][0]["status"], "failed")
        self.assertEqual(recovered["failure"]["reason"], "本次没有联网工具，未核实")
        self.assertNotIn("answer", recovered)
        self.assertIsNone(job["result"])

    async def test_truncated_stream_does_not_report_complete_answer(self):
        real_client = httpx.AsyncClient
        partial = {"choices": [{"delta": {"content": "unfinished"}, "finish_reason": None}]}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="data: " + json.dumps(partial) + "\n\n"))
        with patch.object(workbench.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            job = await self.complete(JobRequest(mode="chat", prompt="hello", models=["a"]))
        self.assertEqual(job["status"], "error")
        self.assertIn("提前中断", job["error"])
        self.assertEqual(job["events"][-1]["type"], "error")

    async def test_switching_models_preserves_other_model_attribution(self):
        captured = []
        with self.transport(captured):
            original = await self.complete(JobRequest(mode="chat", prompt="first", models=["a"]))
            await self.complete(JobRequest(mode="chat", prompt="second", models=["b"], conversation_id=original["conversation_id"]))
        context = next(request for request in captured if request["model"] == "b")["messages"]
        self.assertEqual(context[1]["role"], "user")
        self.assertIn("[a]", context[1]["content"])
        self.assertIn("answer-a", context[1]["content"])
        self.assertIn("引用背景", context[1]["content"])

    async def test_selected_council_models_are_per_job(self):
        first = AsyncMock(return_value=[{"model": "member-a", "response": "A"}, {"model": "member-b", "response": "B"}])
        second = AsyncMock(return_value=([{"model": "member-a", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B"}], {"Response A": "member-a", "Response B": "member-b"}))
        third = AsyncMock(return_value={"model": "chair", "response": "final"})
        with patch.object(workbench, "stage1_collect_responses", first), patch.object(workbench, "stage2_collect_rankings", second), patch.object(workbench, "stage3_synthesize_final", third):
            job = await self.complete(JobRequest(mode="council", prompt="test", models=["member-a", "member-b"], chairman="chair"))
        self.assertEqual(job["status"], "complete")
        self.assertEqual(first.await_args.kwargs["models"], ["member-a", "member-b"])
        self.assertEqual(third.await_args.kwargs["chairman"], "chair")
        self.assertEqual(job["result"]["metadata"]["failed_reviewers"], ["member-b"])
        self.assertTrue(job["result"]["metadata"]["partial_failure"])
        self.assertEqual([event["stage"] for event in job["events"] if event["type"] == "stage"], ["responses", "rankings", "synthesis"])

    def test_input_ids_and_worker_arguments_are_safe(self):
        with self.assertRaises(ValueError):
            JobRequest(mode="chat", prompt="test", models=["a"], conversation_id="../../private")
        with self.assertRaises(ValueError):
            JobRequest(mode="chat", prompt="test", models=["a", "a"])
        request = JobRequest(mode="camel", prompt="--help", models=["gpt-6.1-sol", "gemini-3-flash"])
        job = {"mode": request.mode, "prompt": request.prompt, "models": request.models, "rounds": request.rounds}
        command = self.manager.worker_command(job, self.manager.directory / "unique.output")
        self.assertEqual(command[-2:], ["--", "--help"])
        self.assertIn(str(self.manager.directory / "unique.output"), command)

    async def test_export_http_attachments_validation_and_partial_results(self):
        job_id = str(uuid.uuid4())
        job = {"id": job_id, "conversation_id": str(uuid.uuid4()), "mode": "chat", "prompt": "测试问题", "models": ["model-a"], "chairman": "model-a", "rounds": 2, "status": "cancelled", "created_at": "2026-10-11T03:00:00+00:00", "updated_at": "2026-10-11T03:00:01+00:00", "result": None, "error": None, "events": [{"type": "delta", "job_id": job_id, "seq": 1, "model": "model-a", "text": "已保存的部分"}, {"type": "delta", "job_id": job_id, "seq": 2, "model": "model-a", "text": "回答"}, {"type": "cancelled", "job_id": job_id, "seq": 3}]}
        self.manager.jobs[job_id] = job
        app = FastAPI()
        app.include_router(workbench.router)
        with patch.object(workbench, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                markdown = await client.get(f"/api/workbench/jobs/{job_id}/export?format=md")
                self.assertEqual(markdown.status_code, 200)
                self.assertIn("已保存的部分回答", markdown.text)
                self.assertIn("未完整完成", markdown.text)
                self.assertEqual(markdown.headers["content-disposition"], f'attachment; filename="ModelDesk-chat-{job_id}.md"')
                self.assertTrue(markdown.headers["content-type"].startswith("text/markdown"))
                exported_json = await client.get(f"/api/workbench/jobs/{job_id}/export?format=json")
                self.assertEqual(exported_json.json(), job)
                self.assertIn('.json"', exported_json.headers["content-disposition"])
                missing = await client.get(f"/api/workbench/jobs/{uuid.uuid4()}/export?format=md")
                invalid = await client.get(f"/api/workbench/jobs/{job_id}/export?format=../../private")
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(invalid.status_code, 422)
        self.assertIsNone(job["result"])

    def test_markdown_export_preserves_framework_stages_tasks_and_partial_error(self):
        base = {"id": str(uuid.uuid4()), "prompt": "test", "models": ["a", "b"], "rounds": 2, "created_at": "2026-10-11T03:00:00+00:00", "status": "complete", "error": None, "events": []}
        council = workbench.markdown_export({**base, "mode": "council", "result": {"stage1": [{"model": "a", "response": "initial-answer"}], "stage2": [{"model": "b", "ranking": "peer-review"}], "stage3": {"model": "a", "response": "chair-final"}, "metadata": {"label_to_model": {"Response A": "a"}, "aggregate_rankings": [{"model": "a", "average_rank": 1, "rankings_count": 1}], "failed_models": ["b"]}}})
        for text in ["initial-answer", "peer-review", "chair-final", "Response A", "平均名次", "未返回回答：b"]:
            self.assertIn(text, council)
        camel = workbench.markdown_export({**base, "mode": "camel", "result": {"messages": [{"role": "reviewer", "content": "reviewer-question"}, {"role": "researcher", "content": "researcher-reply"}]}})
        self.assertIn("## 审稿人", camel)
        self.assertIn("reviewer-question", camel)
        self.assertIn("researcher-reply", camel)
        formatted = workbench.markdown_export({**base, "mode": "camel_workforce", "status": "error", "result": {
            "tasks": [], "answer": "", "partial": True,
            "format_diagnostics": [{"phase": "research", "raw": "已查到的原始证据", "finish_reasons": ["tool_calls"]}]}})
        self.assertIn("原生结果格式与收尾", formatted)
        self.assertIn("已查到的原始证据", formatted)
        self.assertIn("未完整完成", formatted)
        crew = workbench.markdown_export({**base, "mode": "crewai", "result": {"tasks": ["draft-task", "review-task"], "answer": "final-answer"}})
        for text in ["draft-task", "review-task", "final-answer"]:
            self.assertIn(text, crew)
        partial = workbench.markdown_export({**base, "mode": "chat", "status": "error", "error": "任务失败", "result": {"responses": [{"model": "a", "text": "retained-text", "error": "incomplete-error"}]}})
        for text in ["retained-text", "incomplete-error", "任务失败", "未完整完成"]:
            self.assertIn(text, partial)


if __name__ == "__main__":
    unittest.main()
