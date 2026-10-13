"""Durable harness execution using actual JobManager children and LangGraph."""
import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import attachments, super_platform, workbench
from backend.harness.manager import HarnessManager
from backend.harness.models import RunRequest, TeamRequest
from backend.harness.engines import request_for_step, output_for_job
from backend.harness import router as harness_api
from backend.platform_store import PlatformStore
from backend.projects import ProjectStore


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.jobs = workbench.JobManager(self.directory / "jobs")
        self.harness = HarnessManager(self.directory / "harness", self.jobs)

    async def asyncTearDown(self):
        await self.harness.shutdown()
        await self.jobs.shutdown()
        self.temp.cleanup()

    def request(self, **kwargs):
        return RunRequest(goal="完成示例任务", models=["test"], steps=[
            {"id": "draft", "title": "初稿", "mode": "chat"},
            {"id": "final", "title": "最终交付", "mode": "chat"}], **kwargs)

    async def settle(self, identifier):
        task = self.harness.tasks.get(identifier)
        if task:
            await task
        return self.harness.get(identifier)

    async def test_ordered_handoff_provenance_and_no_implicit_history(self):
        calls = []

        async def chat(job):
            calls.append(copy.deepcopy(job))
            return {"responses": [{"model": "test", "text": "步骤结果 HANDOFF_2846"}]}

        with patch.object(self.jobs, "chat", side_effect=chat):
            run = await self.harness.create(self.request())
            result = await self.settle(run["id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("HANDOFF_2846", calls[0]["prompt"])
        self.assertIn("HANDOFF_2846", calls[1]["prompt"])
        self.assertIn("UNTRUSTED_HARNESS_HANDOFF", calls[1]["prompt"])
        self.assertNotEqual(calls[0]["conversation_id"], calls[1]["conversation_id"])
        self.assertEqual(result["result"]["job_id"], calls[1]["id"])
        self.assertEqual([step["status"] for step in result["steps"]], ["complete", "complete"])
        self.assertEqual(calls[0]["harness_run_id"], result["id"])

    async def test_retry_only_failed_step_with_attempt_lineage(self):
        calls = []

        async def chat(job):
            calls.append(job["harness_step_id"])
            if len(calls) == 2:
                raise RuntimeError("temporary model failure")
            return {"responses": [{"model": "test", "text": "actual result"}]}

        with patch.object(self.jobs, "chat", side_effect=chat):
            run = await self.harness.create(self.request())
            failed = await self.settle(run["id"])
            first_id = failed["steps"][0]["attempts"][0]["job_id"]
            self.assertEqual(failed["status"], "error")
            await self.harness.resume(run["id"])
            final = await self.settle(run["id"])
        self.assertEqual(final["status"], "complete")
        self.assertEqual(calls, ["draft", "final", "final"])
        self.assertEqual(final["steps"][0]["attempts"][0]["job_id"], first_id)
        self.assertEqual([item["status"] for item in final["steps"][1]["attempts"]], ["error", "complete"])

    async def test_human_gate_survives_restart_and_requires_approval(self):
        request = self.request()
        request.steps[0].require_review = True
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "review this"}]})):
            run = await self.harness.create(request)
            waiting = await self.settle(run["id"])
            self.assertEqual(waiting["status"], "waiting_review")
            self.assertEqual(len(self.jobs.jobs), 1)
            recovered = HarnessManager(self.directory / "harness", self.jobs)
            recovered.recover()
            self.assertEqual(recovered.get(run["id"])["status"], "waiting_review")
            self.assertFalse(recovered.tasks)
            with self.assertRaises(HTTPException):
                await recovered.resume(run["id"])
            await recovered.approve(run["id"])
            await recovered.tasks[run["id"]]
            self.assertEqual(recovered.get(run["id"])["status"], "complete")
            self.assertEqual(recovered.get(run["id"])["steps"][0]["review_status"], "approved")

    async def test_cancel_stops_owned_child_and_no_next_step(self):
        started = asyncio.Event()

        async def chat(job):
            started.set()
            await asyncio.Event().wait()

        with patch.object(self.jobs, "chat", side_effect=chat):
            run = await self.harness.create(self.request())
            await started.wait()
            result = await self.harness.cancel(run["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.jobs.jobs), 1)
        self.assertEqual(next(iter(self.jobs.jobs.values()))["status"], "cancelled")
        self.assertEqual(result["steps"][1]["status"], "pending")
        self.assertFalse(self.harness.tasks)
        self.assertFalse(self.jobs.tasks)

    async def test_cancel_before_runner_starts_can_resume(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "actual"}]})):
            run = await self.harness.create(self.request())
            await self.harness.cancel(run["id"])
            self.assertNotIn(run["id"], self.harness.tasks)
            self.assertEqual(len(self.jobs.jobs), 0)
            await self.harness.resume(run["id"])
            result = await self.settle(run["id"])
        self.assertEqual(result["status"], "complete")

    async def test_concurrent_admission_and_resume_respect_capacity(self):
        barrier = asyncio.Event()

        async def chat(job):
            await barrier.wait()
            return {"responses": [{"model": "test", "text": "actual"}]}

        with patch.object(self.jobs, "chat", side_effect=chat):
            results = await asyncio.gather(*(self.harness.create(self.request()) for _ in range(5)), return_exceptions=True)
            accepted = [item for item in results if isinstance(item, dict)]
            denied = [item for item in results if isinstance(item, HTTPException)]
            self.assertEqual(len(accepted), 4)
            self.assertEqual([error.status_code for error in denied], [429])
            cancelled = accepted[0]
            await self.harness.cancel(cancelled["id"])
            replacement = await self.harness.create(self.request())
            with self.assertRaises(HTTPException) as error:
                await self.harness.resume(cancelled["id"])
            self.assertEqual(error.exception.status_code, 429)
            self.assertEqual(self.harness.get(cancelled["id"])["status"], "cancelled")
            self.assertIn(replacement["id"], self.harness.tasks)
            barrier.set()
            await asyncio.gather(*list(self.harness.tasks.values()))

    async def test_cancel_during_approval_never_restarts_run(self):
        request = self.request()
        request.steps[0].require_review = True
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "actual"}]})):
            run = await self.harness.create(request)
            await self.settle(run["id"])
            settling = asyncio.create_task(asyncio.sleep(0.05))
            self.harness.tasks[run["id"]] = settling
            approval = asyncio.create_task(self.harness.approve(run["id"]))
            await asyncio.sleep(0)
            await self.harness.cancel(run["id"])
            result = await asyncio.gather(approval, return_exceptions=True)
            self.assertIsInstance(result[0], (HTTPException, asyncio.CancelledError))
            self.assertEqual(self.harness.get(run["id"])["status"], "cancelled")
            self.assertEqual(len(self.jobs.jobs), 1)

    async def test_partial_success_does_not_advance(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [
                {"model": "test", "text": "partial actual output"}, {"model": "other", "error": "failed", "text": ""}]})):
            run = await self.harness.create(self.request())
            result = await self.settle(run["id"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["steps"][0]["output"]["partial"])
        self.assertEqual(len(self.jobs.jobs), 1)
        self.assertIsNone(result["result"])

    async def test_restart_reconciles_observed_completed_child_without_replay(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "saved answer"}]})):
            run = await self.harness.create(self.request())
            complete = await self.settle(run["id"])
        complete["status"] = "running"
        complete["steps"][1]["status"] = "running"
        complete["steps"][1]["output"] = None
        complete["steps"][1]["attempts"][-1]["status"] = "running"
        self.harness.store.save(complete)
        recovered = HarnessManager(self.directory / "harness", self.jobs)
        recovered.recover()
        self.assertEqual(recovered.get(run["id"])["status"], "complete")
        self.assertFalse(recovered.tasks)
        self.assertEqual(len(self.jobs.jobs), 2)

    async def test_recovery_never_skips_completed_step_review(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "saved answer"}]})):
            run = await self.harness.create(self.request())
            complete = await self.settle(run["id"])
        complete["status"] = "running"
        complete["steps"][-1]["config"]["require_review"] = True
        complete["steps"][-1]["review_status"] = None
        self.harness.store.save(complete)
        recovered = HarnessManager(self.directory / "harness", self.jobs)
        recovered.recover()
        loaded = recovered.get(run["id"])
        self.assertEqual(loaded["status"], "waiting_review")
        self.assertEqual(loaded["steps"][-1]["review_status"], "pending")
        self.assertFalse(recovered.tasks)

    async def test_malformed_run_does_not_prevent_good_history_recovery(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "actual"}]})):
            run = await self.harness.create(self.request())
            await self.settle(run["id"])
        bad_id = str(uuid.uuid4())
        (self.harness.store.directory / (bad_id + ".json")).write_text(json.dumps({"id": bad_id, "schema_version": 1, "steps": [], "request": {}}))
        recovered = HarnessManager(self.directory / "harness", self.jobs)
        recovered.recover()
        self.assertEqual(recovered.get(run["id"])["status"], "complete")
        self.assertNotIn(bad_id, recovered.runs)

    async def test_timeout_and_attempt_budget_are_enforced(self):
        async def chat(job):
            await asyncio.sleep(60)
        with patch.object(self.jobs, "chat", side_effect=chat):
            run = await self.harness.create(self.request(max_attempts=1))
            self.harness.get(run["id"])["request"]["step_timeout_seconds"] = 0.01
            failed = await self.settle(run["id"])
            self.assertEqual(failed["status"], "error")
            self.assertIn("时间上限", failed["error"])
            with self.assertRaises(HTTPException):
                await self.harness.resume(run["id"])
        self.assertFalse(self.jobs.tasks)

    async def test_moved_resource_and_resource_revision_block_later_step(self):
        resource_store = PlatformStore(self.directory / "resources.json")
        item = resource_store.put("memories", {"name": "selected", "content": "original"})

        async def chat(job):
            resource_store.put("memories", {"name": "selected", "content": "modified"}, item["id"])
            return {"responses": [{"model": "test", "text": "first complete"}]}

        with patch("backend.harness.manager.resources", resource_store), patch.object(super_platform, "store", resource_store), patch.object(self.jobs, "chat", side_effect=chat):
            run = await self.harness.create(self.request(memory_ids=[item["id"]]))
            failed = await self.settle(run["id"])
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["steps"][0]["status"], "complete")
            self.assertEqual(len(self.jobs.jobs), 1)
            with self.assertRaises(HTTPException):
                await self.harness.resume(run["id"])

    async def test_memory_failure_does_not_discard_completed_answer(self):
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "completed"}]})), patch.object(super_platform, "remember_result", side_effect=OSError("write failed")):
            job = self.jobs.create(workbench.JobRequest(mode="chat", prompt="test", models=["test"], remember=True))
            await self.jobs.tasks[job["id"]]
        result = self.jobs.get(job["id"])
        self.assertEqual(result["status"], "complete")
        self.assertIn("保存长期记忆失败", result["warnings"][0])
        self.assertEqual(result["events"][-1]["type"], "complete")

    async def test_only_final_step_saves_requested_memory(self):
        memory = {"id": str(uuid.uuid4()), "scope": "global"}
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "actual"}]})), patch.object(super_platform, "remember_result", return_value=memory) as remember:
            run = await self.harness.create(self.request(remember=True))
            complete = await self.settle(run["id"])
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(remember.call_count, 1)
        self.assertEqual(remember.call_args.args[0]["harness_step_id"], "final")

    async def test_resource_version_checked_at_actual_read_and_tools(self):
        resource_store = PlatformStore(self.directory / "resources.json")
        item = resource_store.put("knowledge", {"name": "selected", "content": "original"})
        with patch("backend.harness.manager.resources", resource_store), patch.object(super_platform, "store", resource_store):
            revisions = self.harness.capture_revisions({"knowledge_ids": [item["id"]]})
            resource_store.put("knowledge", {"name": "selected", "content": "new material"}, item["id"])
            with self.assertRaises(HTTPException) as prompt_error:
                super_platform.context_material({"prompt": "material", "mode": "chat", "knowledge_ids": [item["id"]], "harness_resource_revisions": revisions})
            self.assertEqual(prompt_error.exception.status_code, 409)
            with self.assertRaises(HTTPException) as tool_error:
                await super_platform.invoke_tool("knowledge_search", {"query": "material"}, {"knowledge_ids": [item["id"]], "resource_revisions": revisions})
            self.assertEqual(tool_error.exception.status_code, 409)

    async def test_http_scope_export_and_explicit_retry_contract(self):
        app = FastAPI()
        app.include_router(harness_api.router)
        with patch.object(harness_api, "manager", self.harness), patch.object(workbench, "model_catalog", new=AsyncMock(return_value=["test"])), patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "HTTP_ACTUAL_731"}]})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                created = await client.post("/api/harness/runs", json=self.request().model_dump(mode="json"))
                self.assertEqual(created.status_code, 202)
                identifier = created.json()["id"]
                await self.settle(identifier)
                listed = await client.get("/api/harness/runs")
                self.assertEqual([item["id"] for item in listed.json()], [identifier])
                scoped = await client.get("/api/harness/runs", params={"project_id": str(uuid.uuid4())})
                self.assertEqual(scoped.json(), [])
                exported = await client.get(f"/api/harness/runs/{identifier}/export")
                self.assertEqual(exported.status_code, 200)
                self.assertIn("HTTP_ACTUAL_731", exported.text)
                backup = await client.get(f"/api/harness/runs/{identifier}/export?format=json")
                self.assertEqual(backup.json()["result"]["job_id"], self.harness.get(identifier)["steps"][-1]["attempts"][-1]["job_id"])
                denied = await client.post(f"/api/harness/runs/{identifier}/resume", json={"acknowledge_replay": False})
                self.assertEqual(denied.status_code, 422)
                body = self.request().model_dump(mode="json")
                body["steps"][0]["mode"] = "sdk_script"
                denied = await client.post("/api/harness/runs", json=body)
                self.assertEqual(denied.status_code, 422)

    async def test_execution_rechecks_selected_memory_project(self):
        resource_store = PlatformStore(self.directory / "resources.json")
        project_a, project_b = str(uuid.uuid4()), str(uuid.uuid4())
        item = resource_store.put("memories", {"name": "selected", "content": "original", "project_id": project_a})
        resource_store.put("memories", {"name": "selected", "content": "other project", "project_id": project_b}, item["id"])
        with patch.object(super_platform, "store", resource_store), self.assertRaises(HTTPException) as error:
            super_platform.context_material({"prompt": "test", "mode": "chat", "project_id": project_a, "memory_ids": [item["id"]]})
        self.assertEqual(error.exception.status_code, 403)

    async def test_inputs_remain_frozen_after_project_asset_removal(self):
        projects = ProjectStore(self.directory / "projects")
        asset_store = attachments.AssetStore(self.directory / "assets")
        captured = []
        with patch("backend.projects.store", projects), patch.object(attachments, "store", asset_store), patch("backend.harness.manager.assets", asset_store):
            project = projects.register("harness project")
            asset = asset_store.add(project["id"], "brief.txt", b"FIXED_INPUT_7334")

            async def chat(job):
                captured.append(job["prompt"])
                if len(captured) == 1:
                    asset_store.remove(project["id"], asset["id"])
                return {"responses": [{"model": "test", "text": "retained input"}]}

            with patch.object(self.jobs, "chat", side_effect=chat):
                run = await self.harness.create(self.request(project_id=project["id"], attachment_ids=[asset["id"]]))
                result = await self.settle(run["id"])
            self.assertEqual(result["status"], "complete")
            self.assertTrue(all("FIXED_INPUT_7334" in value for value in captured))
            self.assertEqual(len(captured), 2)

    def team_request(self, project_id=None):
        return TeamRequest(name="项目协作组", project_id=project_id, members=[
            {"id": "writer", "name": "方案作者", "model": "alpha", "role": "输出可执行方案 ROLE_AUTHOR_83"},
            {"id": "critic", "name": "质量检查", "model": "beta", "role": "检查依据 ROLE_REVIEWER_29"}])

    async def test_team_http_scope_archive_and_missing_model_preflight(self):
        projects = ProjectStore(self.directory / "projects")
        app = FastAPI()
        app.include_router(harness_api.router)
        with patch("backend.projects.store", projects), patch.object(harness_api, "manager", self.harness), patch.object(workbench, "model_catalog", new=AsyncMock(return_value=["alpha"])):
            first, second = projects.register("first"), projects.register("second")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                body = self.team_request(first["id"]).model_dump(mode="json")
                created = await client.post("/api/harness/teams", json=body)
                self.assertEqual(created.status_code, 201, created.text)
                team = created.json()
                unassigned = await client.get("/api/harness/teams")
                self.assertEqual(unassigned.json(), [])
                listed = await client.get("/api/harness/teams", params={"project_id": first["id"]})
                self.assertEqual([item["id"] for item in listed.json()], [team["id"]])
                for method in [client.get, client.delete]:
                    denied = await method("/api/harness/teams/" + team["id"], params={"project_id": second["id"]})
                    self.assertEqual(denied.status_code, 403)
                denied = await client.put("/api/harness/teams/" + team["id"], json={**body, "project_id": second["id"]})
                self.assertEqual(denied.status_code, 403)
                run_body = {"goal": "team test", "team_id": team["id"], "project_id": first["id"], "steps": [{"id": "draft", "title": "draft", "mode": "chat"}]}
                denied = await client.post("/api/harness/runs", json=run_body)
                self.assertEqual(denied.status_code, 422)
                self.assertIn("beta", denied.text)
                self.assertFalse(self.harness.runs)
                self.assertFalse(self.jobs.jobs)
                archived = await client.delete("/api/harness/teams/" + team["id"], params={"project_id": first["id"]})
                self.assertTrue(archived.json()["archived"])
                denied = await client.post("/api/harness/runs", json=run_body)
                self.assertEqual(denied.status_code, 404)
                listed = await client.get("/api/harness/teams", params={"project_id": first["id"], "archived": "true"})
                self.assertEqual([item["id"] for item in listed.json()], [team["id"]])

    async def test_team_snapshot_survives_edit_archive_and_recovery(self):
        captured = []

        async def chat(job):
            captured.append(copy.deepcopy(job))
            return {"responses": [{"model": model, "text": "actual team output"} for model in job["models"]]}

        team = self.harness.teams.put(self.team_request())
        request = RunRequest(goal="team test", team_id=team["id"], steps=[
            {"id": "draft", "title": "初稿", "mode": "chat", "require_review": True},
            {"id": "final", "title": "交付", "mode": "chat"}])
        with patch.object(self.jobs, "chat", side_effect=chat):
            created = await self.harness.create(request)
            await self.settle(created["id"])
            changed = self.team_request()
            changed.members[0].role = "CHANGED_ROLE"
            changed.members[0].model = "new-model"
            self.harness.teams.put(changed, team["id"])
            self.harness.teams.archive(team["id"])
            recovered = HarnessManager(self.directory / "harness", self.jobs)
            recovered.recover()
            await recovered.approve(created["id"])
            await recovered.tasks[created["id"]]
            result = recovered.get(created["id"])
            await recovered.shutdown()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["team_snapshot"], team)
        self.assertEqual(len(captured), 2)
        for child in captured:
            self.assertEqual(child["models"], ["alpha", "beta"])
            self.assertIn("ROLE_AUTHOR_83", child["roles"]["alpha"])
            self.assertNotIn("CHANGED_ROLE", json.dumps(child))
            self.assertEqual(child["harness_team_snapshot"], team)

    async def test_temporary_team_and_native_member_dispatch(self):
        members = self.team_request().model_dump(mode="json")["members"]
        members.append({"id": "editor", "name": "交付编辑", "model": "gamma", "role": "形成最终成果"})
        request = RunRequest(goal="team test", members=members)
        resolved, snapshot = self.harness.teams.resolve(request)
        self.assertEqual(resolved.models, ["alpha", "beta", "gamma"])
        for mode in ["chat", "group", "council", "camel", "crewai", "camel_workforce", "crewai_flow"]:
            request = RunRequest(goal="team test", members=members, steps=[{"id": "one", "title": "one", "mode": mode}])
            body = request.model_dump(mode="json")
            step = {"id": "one", "config": body["steps"][0], "status": "pending"}
            child = request_for_step({"request": body, "team_snapshot": snapshot, "steps": [step]}, step)
            self.assertEqual(child.models, ["alpha", "beta"] if mode == "camel" else ["alpha", "beta", "gamma"])
            self.assertIn("ROLE_REVIEWER_29", child.roles["beta"])
            if child.workflow:
                self.assertEqual([agent.role for agent in child.workflow.agents], [member["name"] for member in members[:len(child.models)]])
                self.assertIn("ROLE_AUTHOR_83", child.workflow.agents[0].goal)

    async def test_team_invalid_members_and_workflow_models_are_rejected(self):
        body = self.team_request().model_dump(mode="json")
        body["members"][1]["model"] = "alpha"
        with self.assertRaises(ValueError):
            TeamRequest.model_validate(body)
        team = self.harness.teams.put(self.team_request())
        request = RunRequest(goal="test", team_id=team["id"], steps=[{"id": "one", "title": "one", "mode": "chat", "models": ["outside"]}])
        with self.assertRaises(HTTPException) as error:
            self.harness.teams.resolve(request)
        self.assertEqual(error.exception.status_code, 422)
        request = RunRequest(goal="test", models=["alpha", "beta"], steps=[{"id": "one", "title": "one", "mode": "crewai", "workflow": {
            "agents": [{"id": "author", "role": "Author", "goal": "write", "model": "outside"}],
            "tasks": [{"id": "write", "agent": "author", "description": "write", "expected_output": "draft"}]}}])
        with self.assertRaises(HTTPException) as error:
            await self.harness.create(request)
        self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(self.harness.runs)

    async def test_council_delivers_member_roles_and_retains_anonymous_review(self):
        job = {"id": "fixture", "models": ["alpha", "beta"], "chairman": "alpha", "prompt": "task",
               "roles": {"alpha": "ROLE_AUTHOR_83", "beta": "ROLE_REVIEWER_29"}}

        async def first(prompt, **kwargs):
            return [{"model": kwargs["models"][0], "response": "answer"}]

        rankings = ([{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
                     {"model": "beta", "ranking": "FINAL RANKING:\n1. Response B\n2. Response A", "parsed_ranking": ["Response B", "Response A"]}], {"Response A": "alpha", "Response B": "beta"})
        with patch.object(self.jobs, "emit"), patch.object(workbench, "stage1_collect_responses", new=AsyncMock(side_effect=first)) as phase1, patch.object(workbench, "stage2_collect_rankings", new=AsyncMock(return_value=rankings)) as phase2, patch.object(workbench, "stage3_synthesize_final", new=AsyncMock(return_value={"model": "alpha", "response": "final"})) as phase3:
            result = await self.jobs.council(job)
            self.assertFalse(result["partial_failure"])
            self.assertEqual(phase1.await_count, 2)
            self.assertIn("ROLE_AUTHOR_83", phase1.await_args_list[0].kwargs["system_prompt"])
            self.assertIn("ROLE_REVIEWER_29", phase1.await_args_list[1].kwargs["system_prompt"])
            self.assertNotIn("ROLE_AUTHOR_83", phase1.await_args_list[1].kwargs["system_prompt"])
            self.assertIsNone(phase2.await_args.kwargs["system_prompt"])
            self.assertIn("ROLE_AUTHOR_83", phase3.await_args.kwargs["system_prompt"])

    async def test_code_review_partial_engine_result_cannot_advance_harness(self):
        for raw in [{"partial_failure": True}, {"responses": [{"model": "alpha", "text": "partial", "incomplete": True}]}]:
            output, error = output_for_job({"id": "review", "mode": "review", "status": "complete", "result": {"findings": [], "summary": "reviewed", "raw": raw}})
            self.assertTrue(output["partial"])
            self.assertIsNotNone(error)

    async def test_corrupt_team_and_snapshot_preserved_but_not_loaded(self):
        team = self.harness.teams.put(self.team_request())
        path = self.harness.teams.atomic.directory / (team["id"] + ".json")
        path.write_text(json.dumps({**team, "updated_at": None}))
        self.assertEqual(self.harness.teams.list(), [])
        self.assertTrue(path.exists())
        with patch.object(self.jobs, "chat", new=AsyncMock(return_value={"responses": [{"model": "test", "text": "actual"}]})):
            created = await self.harness.create(self.request())
            await self.settle(created["id"])
        path = self.harness.store.directory / (created["id"] + ".json")
        stored = json.loads(path.read_text())
        stored["team_snapshot"] = team
        path.write_text(json.dumps(stored))
        self.assertEqual(self.harness.store.load(), [])
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
