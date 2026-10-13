"""Canonical history, real job execution and HTTP continuity without model costs."""
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
from backend.conversations import build_history, deliverables, encoded, quoted_history
from backend.workbench import JobManager, JobRequest


def completed(mode, result, **fields):
    return {"id": str(uuid.uuid4()), "conversation_id": "topic", "mode": mode, "created_at": "2026-10-12T00:00:00+00:00",
            "status": "complete", "prompt": "prior user task", "models": ["a", "b"], "result": result, **fields}


class HistoryTests(unittest.TestCase):
    def test_only_actual_complete_deliverables_are_reused(self):
        cases = [
            ("chat", {"responses": [{"model": "a", "text": "good"}, {"model": "b", "text": "error draft", "error": "failed"}, {"model": "c", "text": "truncated", "incomplete": True}]}, ["good"]),
            ("council", {"stage1": [{"response": "private intermediate"}], "stage2": [{"ranking": "ranking only"}], "stage3": {"model": "b", "response": "chair answer"}}, ["chair answer"]),
            ("camel", {"messages": [{"role": "reviewer", "content": "actual dialogue"}], "partial": False}, ["actual dialogue"]),
            ("group", {"messages": [{"model": "b", "content": "good group"}, {"model": "a", "content": "incomplete group", "incomplete": True}], "partial_failure": True}, ["good group"]),
            ("camel_workforce", {"answer": "native DONE result", "partial": False, "task_state": "DONE", "format_diagnostics": [{"raw": "discard trace"}]}, ["native DONE result"]),
            ("crewai", {"answer": "legacy crew answer", "tasks": ["intermediate"]}, ["legacy crew answer"]),
            ("crewai_flow", {"answer": "branch result", "partial": False, "tasks": [{"status": "skipped"}]}, ["branch result"]),
            ("sdk_script", {"answer": "explicit SDK answer", "source": "private source", "raw": "unknown arbitrary data"}, ["explicit SDK answer"]),
        ]
        for mode, result, expected in cases:
            with self.subTest(mode=mode):
                items, _ = deliverables(completed(mode, result))
                self.assertEqual([item["content"] for item in items], expected)
                for status in ["error", "cancelled", "interrupted", "running"]:
                    self.assertEqual(deliverables(completed(mode, result, status=status))[0], [])
                self.assertEqual(deliverables(completed(mode, {**result, "partial": True}))[0], [])
        self.assertEqual(deliverables(completed("camel_workforce", {"answer": "failed native output", "task_state": "FAILED", "partial": False}))[0], [])
        self.assertEqual(deliverables(completed("sdk_script", {"arbitrary": {"token": "must not reuse"}}))[0], [])
        for mode, result in [("chat", {"responses": None}), ("group", {"messages": 12}), ("council", {"stage3": []})]:
            self.assertEqual(deliverables(completed(mode, result))[0], [])
        review = completed("review", {"findings": [], "coverage": {"complete": False}, "snapshot_digest": "old-digest", "verification": "未执行测试", "unconfirmed": [{"claim": "never promote as evidence"}], "raw": "raw framework trace"})
        items, caveats = deliverables(review)
        self.assertEqual(json.loads(items[0]["content"])["unconfirmed_count"], 1)
        self.assertNotIn("never promote", encoded(items))
        self.assertIn("空发现不证明", str(caveats))

    def test_project_scope_stale_snapshot_turn_count_and_exact_budget(self):
        current = completed("council", None, id="new", created_at="2026-10-12T01:00:00+00:00", prompt="followup", project_id="same-project", snapshot_id="new-snapshot", context_turns=1)
        good = completed("review", {"findings": [], "snapshot_digest": "old-digest", "coverage": {"complete": False}}, id="old", project_id="same-project", snapshot_id="old-snapshot")
        failed = completed("council", {"stage3": {"response": "must not copy"}}, id="failed", status="error", project_id="same-project", created_at="2026-10-12T00:30:00+00:00")
        foreign = completed("chat", {"responses": [{"model": "a", "text": "foreign project secret"}]}, id="foreign", project_id="other-project")
        history = build_history({item["id"]: item for item in [good, failed, foreign, current]}, current)
        self.assertEqual(history["metadata"]["used_turns"], 1)
        self.assertTrue(history["metadata"]["references"][0]["stale_snapshot"])
        self.assertEqual({item["reason"] for item in history["metadata"]["omitted"]}, {"not_complete", "project_scope_changed"})
        self.assertNotIn("foreign project secret", quoted_history(history))
        self.assertNotIn("must not copy", quoted_history(history))
        self.assertIn("旧快照", quoted_history(history))
        long = completed("chat", {"responses": [{"model": "a", "text": "😀\\\"long evidence" * 4000}]}, id="large", project_id="same-project", prompt="x" * 12000)
        bounded = build_history({"large": long}, current, reserved_chars=8000)
        self.assertTrue(bounded["metadata"]["truncated"])
        self.assertLessEqual(len(encoded(bounded["turns"])), bounded["metadata"]["budget_chars"])
        self.assertIn("被截断", quoted_history(bounded))
        self.assertEqual(json.loads(encoded(bounded["turns"]))[0]["job_id"], "large")


class ConversationExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = JobManager(Path(self.temporary.name))
        self.manager.recover()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.temporary.cleanup()

    async def finish(self, request, manager=None):
        owner = manager or self.manager
        job = owner.create(request)
        await owner.tasks[job["id"]]
        return owner.get(job["id"])

    def configuration(self, mode):
        config = {"agents": [{"id": "writer", "role": "作者", "goal": "produce", "model": "a"}, {"id": "reader", "role": "审稿人", "goal": "check", "model": "b"}], "tasks": [], "process": "sequential"}
        if mode in {"crewai", "crewai_flow"}:
            config["tasks"] = [{"id": "draft", "agent": "writer", "description": "produce actual result", "expected_output": "answer", "context": []}]
        return config

    async def test_all_collaboration_http_followups_new_conversations_and_restart(self):
        api = FastAPI()
        api.include_router(workbench.router)
        captured, council_inputs, worker_inputs = [], [], []
        original_client = httpx.AsyncClient

        def transport(request):
            payload = json.loads(request.content)
            captured.append(payload)
            if payload.get("stream"):
                chunk = {"choices": [{"delta": {"content": "DELIVERABLE_chat"}, "finish_reason": "stop"}]}
                return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json={"choices": [{"message": {"content": "DELIVERABLE_group"}, "finish_reason": "stop"}]})

        async def first(prompt, **kwargs):
            council_inputs.append(prompt)
            return [{"model": "a", "response": "DoNotCopyIntermediate"}, {"model": "b", "response": "other draft"}]

        async def second(prompt, responses, **kwargs):
            return [{"model": "a", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]}], {"Response A": "a", "Response B": "b"}

        async def third(*args, **kwargs):
            return {"model": "a", "response": "DELIVERABLE_council"}

        worker_script = """import json,sys
from pathlib import Path
data=json.loads(Path(sys.argv[1]).read_text())
mode=data['mode']
result={'tasks':[], 'answer':'DELIVERABLE_'+mode, 'partial':False, 'task_state':'DONE', 'received_prompt':data['prompt']}
if mode=='camel':result['messages']=[{'role':'作者','model':'a','content':'DELIVERABLE_camel'}]
Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False))
"""

        def command(job, output):
            worker_inputs.append(job["prompt"])
            return [sys.executable, "-c", worker_script, job["_workflow_input"], str(output)]

        with patch.object(workbench, "manager", self.manager), patch.object(workbench, "model_catalog", new=AsyncMock(return_value=["a", "b"])), \
             patch.object(workbench.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs)), \
             patch.object(workbench, "stage1_collect_responses", side_effect=first), patch.object(workbench, "stage2_collect_rankings", side_effect=second), patch.object(workbench, "stage3_synthesize_final", side_effect=third), \
             patch.object(self.manager, "worker_command", side_effect=command):
            async with original_client(transport=httpx.ASGITransport(app=api), base_url="http://fixture") as client:
                for mode in ["chat", "group", "council", "camel", "crewai", "camel_workforce", "crewai_flow"]:
                    with self.subTest(mode=mode):
                        body = {"mode": mode, "prompt": "REMEMBER_" + mode, "models": ["a", "b"], "rounds": 1, "system_prompt": "CURRENT_USER_PREFERENCE_" + mode}
                        if mode.startswith("camel") or mode.startswith("crewai"):
                            body["workflow"] = self.configuration(mode)
                        response = await client.post("/api/workbench/jobs", json=body)
                        self.assertEqual(response.status_code, 202, response.text)
                        initial = response.json()
                        await self.manager.tasks[initial["id"]]
                        followup = await client.post("/api/workbench/jobs", json={**body, "prompt": "Continue", "conversation_id": initial["conversation_id"], "context_turns": 1})
                        self.assertEqual(followup.status_code, 202, followup.text)
                        continued = followup.json()
                        await self.manager.tasks[continued["id"]]
                        detail = (await client.get("/api/workbench/jobs/" + continued["id"])).json()
                        self.assertEqual(detail["status"], "complete", detail.get("error"))
                        self.assertEqual(detail["conversation_id"], initial["conversation_id"])
                        self.assertEqual(detail["context_history"]["used_turns"], 1)
                        self.assertEqual(detail["context_history"]["references"][0]["job_id"], initial["id"])
                        self.assertTrue(any(event.get("stage") == "conversational_context" for event in detail["events"]))
                        if mode == "chat":
                            sent = captured[-1]["messages"]
                        elif mode == "group":
                            sent = captured[-1]["messages"]
                        elif mode == "council":
                            sent = council_inputs[-1]
                            self.assertNotIn("DoNotCopyIntermediate", sent)
                        else:
                            sent = worker_inputs[-1]
                        self.assertIn("REMEMBER_" + mode, str(sent))
                        self.assertIn("DELIVERABLE_" + mode, str(sent))
                        self.assertEqual(str(sent).count("CURRENT_USER_PREFERENCE_" + mode), 1)
                        self.assertEqual(detail["prompt"], "Continue")
                        isolated = await client.post("/api/workbench/jobs", json={**body, "prompt": "New independent topic"})
                        fresh = isolated.json()
                        await self.manager.tasks[fresh["id"]]
                        self.assertNotEqual(fresh["conversation_id"], initial["conversation_id"])
                        self.assertEqual(self.manager.get(fresh["id"])["context_history"]["used_turns"], 0)
                        if mode == "chat" or mode == "group":
                            self.assertNotIn("REMEMBER_" + mode, str(captured[-1]["messages"]))
                        elif mode == "council":
                            self.assertNotIn("REMEMBER_" + mode, council_inputs[-1])
                        else:
                            self.assertNotIn("REMEMBER_" + mode, worker_inputs[-1])
                        self.assertFalse(any(key.startswith("_") for key in detail))
                saved_conversation = detail["conversation_id"]
                saved_job = detail["id"]
            recovered = JobManager(self.manager.directory)
            recovered.recover()
            with patch.object(workbench, "manager", recovered), patch.object(recovered, "worker_command", side_effect=command):
                resumed = await self.finish(JobRequest(mode="crewai_flow", prompt="Resume after service restart", models=["a", "b"], conversation_id=saved_conversation, workflow=self.configuration("crewai_flow")), recovered)
                self.assertEqual(resumed["status"], "complete")
                self.assertIn(saved_job, [entry["job_id"] for entry in resumed["context_history"]["references"]])
                self.assertIn("Continue", resumed["result"]["received_prompt"])
                self.assertEqual(recovered.get(saved_job)["context_history"], detail["context_history"])
            await recovered.shutdown()

    async def test_cross_engine_scope_and_permissions_remain_current(self):
        calls = []
        async def worker(job):
            calls.append(job)
            return {"answer": "complete workforce deliverable", "task_state": "DONE", "tasks": [], "partial": False}
        from backend import projects
        project_store = projects.ProjectStore(Path(self.temporary.name) / "project-state")
        project = project_store.register("Context fixture")["id"]
        snapshot = str(uuid.uuid4())
        with patch.object(projects, "store", project_store), patch.object(self.manager, "worker", side_effect=worker), patch("backend.super_platform.context_material", return_value=("", [], False)):
            initial = await self.finish(JobRequest(mode="camel_workforce", prompt="PRIVATE_PROJECT_TASK", models=["a", "b"], workflow=self.configuration("camel_workforce"), project_id=project, snapshot_id=snapshot, tools=["web_fetch"]))
            matching = await self.finish(JobRequest(mode="crewai", prompt="continue with no tools", models=["a", "b"], workflow=self.configuration("crewai"), conversation_id=initial["conversation_id"], project_id=project, snapshot_id=str(uuid.uuid4())))
            execution = calls[-1]
            self.assertIn("PRIVATE_PROJECT_TASK", execution["prompt"])
            self.assertEqual(execution["tools"], [])
            self.assertEqual(execution["workflow"]["agents"][0]["tools"], [])
            self.assertTrue(matching["context_history"]["references"][0]["stale_snapshot"])
            self.assertNotEqual(execution["_runtime"]["token"], calls[0]["_runtime"]["token"])
            detached = await self.finish(JobRequest(mode="camel_workforce", prompt="without explicit project", models=["a", "b"], workflow=self.configuration("camel_workforce"), conversation_id=initial["conversation_id"]))
            self.assertNotIn("PRIVATE_PROJECT_TASK", calls[-1]["prompt"])
            self.assertEqual(detached["context_history"]["used_turns"], 0)
            self.assertEqual(detached["context_history"]["omitted_count"], 2)
            self.assertNotIn("token", json.dumps(matching["context_history"]))

    async def test_review_route_continuity_is_cited_once_and_sdk_receives_private_context(self):
        from backend import projects
        root = Path(self.temporary.name) / "fixture-project"
        root.mkdir()
        (root / "sample.py").write_text("def example():\n    return 1\n")
        store = projects.ProjectStore(Path(self.temporary.name) / "project-state")
        project = store.register("synthetic review", str(root))
        snapshot = store.build_snapshot(project["id"], projects.SnapshotRequest(scope="selected", files=["sample.py"]))
        api = FastAPI()
        api.include_router(projects.router)
        api.include_router(workbench.router)
        captured = []
        original_client = httpx.AsyncClient
        def transport(request):
            payload = json.loads(request.content)
            captured.append(payload)
            chunk = {"choices": [{"delta": {"content": '{"findings":[],"notes":[]}'}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
        with patch.object(workbench, "manager", self.manager), patch.object(projects, "store", store), \
             patch.object(workbench, "model_catalog", new=AsyncMock(return_value=["a"])), \
             patch.object(workbench.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs)):
            async with original_client(transport=httpx.ASGITransport(app=api), base_url="http://fixture") as client:
                payload = {"snapshot_id": snapshot["id"], "models": ["a"], "engine": "chat", "prompt": "REVIEW_REQUEST_PROVENANCE"}
                initial_response = await client.post(f"/api/workbench/projects/{project['id']}/reviews", json=payload)
                self.assertEqual(initial_response.status_code, 202, initial_response.text)
                initial = initial_response.json()
                await self.manager.tasks[initial["id"]]
                response = await client.post(f"/api/workbench/projects/{project['id']}/reviews", json={**payload, "prompt": "Follow up", "conversation_id": initial["conversation_id"], "context_turns": 1})
                self.assertEqual(response.status_code, 202, response.text)
                following = response.json()
                await self.manager.tasks[following["id"]]
                state = self.manager.get(following["id"])
                self.assertEqual(state["status"], "complete", state.get("error"))
                self.assertEqual(state["context_history"]["used_turns"], 1)
                self.assertEqual(str(captured[-1]["messages"]).count("REVIEW_REQUEST_PROVENANCE"), 1)
                self.assertIn(snapshot["digest"], str(captured[-1]["messages"]))
                invalid = await client.post(f"/api/workbench/projects/{project['id']}/reviews", json={**payload, "conversation_id": "arbitrary-path"})
                self.assertEqual(invalid.status_code, 422)
        sdk_script = """import json,sys
from pathlib import Path
data=json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps({'answer':'SDK_ACTUAL_DELIVERABLE','received_context':data['context']}))
"""
        with patch.object(self.manager, "worker_command", side_effect=lambda job, output: [sys.executable, "-c", sdk_script, job["_sdk_input"], str(output)]):
            initial = await self.finish(JobRequest(mode="sdk_script", prompt="SDK prior question", models=["a"], sdk="camel", approved=True, source="RESULT={'answer':'synthetic'}"))
            resumed = await self.finish(JobRequest(mode="sdk_script", prompt="SDK follow up", models=["a"], sdk="camel", approved=True, source="RESULT={'answer':'synthetic'}", conversation_id=initial["conversation_id"]))
        self.assertEqual(resumed["status"], "complete")
        self.assertIn("SDK_ACTUAL_DELIVERABLE", resumed["result"]["received_context"])
        self.assertIn("SDK prior question", resumed["result"]["received_context"])
        self.assertNotIn("RESULT=", resumed["result"]["received_context"])

    async def test_followup_freezes_after_previous_turn_and_queued_cancel_isolated(self):
        entered, release = asyncio.Event(), asyncio.Event()
        received = []
        async def council(job):
            received.append(job["prompt"])
            if job["prompt"] == "first":
                entered.set()
                await release.wait()
            return {"stage3": {"model": "a", "response": "actual completed output"}}
        with patch.object(self.manager, "council", side_effect=council):
            initial = self.manager.create(JobRequest(mode="council", prompt="first", models=["a", "b"]))
            await entered.wait()
            followup = self.manager.create(JobRequest(mode="council", prompt="followup", models=["a", "b"], conversation_id=initial["conversation_id"]))
            queued = self.manager.create(JobRequest(mode="council", prompt="queued cancel", models=["a", "b"], conversation_id=initial["conversation_id"]))
            await asyncio.sleep(0)
            self.assertEqual(received, ["first"])
            self.assertEqual(self.manager.get(followup["id"])["status"], "queued")
            cancelled = await self.manager.cancel(queued["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIn(initial["id"], self.manager.tasks)
            release.set()
            await asyncio.gather(self.manager.tasks[initial["id"]], self.manager.tasks[followup["id"]])
        self.assertIn("actual completed output", received[-1])
        self.assertNotIn(queued["id"], self.manager.tasks)
        self.assertEqual(self.manager.get(followup["id"])["context_history"]["used_turns"], 1)


if __name__ == "__main__":
    unittest.main()
