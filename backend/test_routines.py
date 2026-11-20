"""Routine HTTP authorization, durable handoff and existing delivery integration."""
import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from backend import autonomy, document_delivery, group_delivery, groups, projects, remote_access, routines, todo_board, workbench
from backend.harness.manager import manager as harness
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore
from backend.routine_store import RoutineStore


class RoutineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
        self.state = RoutineStore(self.root / "routines.sqlite3", clock=lambda: self.now)
        self.runner = routines.RoutineWorker(self.state)
        self.jobs = workbench.JobManager(self.root / "jobs")
        self.rooms = groups.RoomStore(self.root / "rooms")
        self.projects = projects.ProjectStore(self.root / "projects")
        self.teams = TeamStore(self.root / "teams")
        self.documents = document_delivery.DocumentStore(self.root / "documents")
        patches = ExitStack()
        self.addCleanup(patches.close)
        for module, field, value in (
            (workbench, "manager", self.jobs), (groups, "store", self.rooms),
            (projects, "store", self.projects), (harness, "teams", self.teams),
            (routines, "store", self.state), (routines, "worker", self.runner),
            (todo_board, "BOARD_FILE", self.root / "board.json"), (todo_board, "_cache", None),
            (todo_board, "_now_iso", lambda: self.now.isoformat()),
            (document_delivery, "store", self.documents),
        ):
            patches.enter_context(patch.object(module, field, value))
        self.catalog = patches.enter_context(patch.object(workbench, "model_catalog", AsyncMock(return_value=["writer-model", "reader-model"])))
        self.project_id = self.projects.register("Routine test project")["id"]
        self.other_project = self.projects.register("Other routine project", path=str(self.root))["id"]
        self.members = [
            {"id": "writer", "name": "作者", "model": "writer-model", "role": "整理资料"},
            {"id": "reader", "name": "审稿人", "model": "reader-model", "role": "检验证据"},
        ]
        self.room = self.make_room(self.project_id)
        self.path = f"/api/groups/{self.room['id']}/routines"
        self.params = {"project_id": self.project_id}
        self.origin = {"Origin": "http://localhost:5173"}
        self.spec = {"name": "每日报告", "goal": "整理实验室进度", "process": "先整理记录，再写摘要",
                     "checks": "核对每项进度的来源", "document_formats": ["html"], "trigger": {"kind": "manual"}}
        self.api = FastAPI()
        for router in (groups.router, routines.router, todo_board.router, workbench.router):
            self.api.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.api), base_url="http://127.0.0.1:8001")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.jobs.shutdown)

    def make_room(self, project_id):
        team = self.teams.put(TeamRequest(name="协作组", project_id=project_id, members=self.members))
        return self.rooms.create(groups.RoomRequest(title="实验群", team_id=team["id"], project_id=project_id), self.teams)

    async def create(self, **changes):
        response = await self.client.post(self.path, params=self.params, headers=self.origin,
                                          json={**self.spec, "client_request_id": str(uuid4()), **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def activate(self, rule, *, enabled=True):
        response = await self.client.post(f"{self.path}/{rule['id']}/enabled", params=self.params, headers=self.origin,
                                          json={"enabled": enabled, "expected_revision": rule["revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def history(self, rule):
        response = await self.client.get(self.path, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        return next(row["recent_runs"] for row in response.json()["routines"] if row["id"] == rule["id"])

    async def provider(self, job, model, system, prompt, **kwargs):
        if "你是项目统筹者" in system:
            return json.dumps({"title": "实验室进度", "document_formats": ["docx"] if "交付 Word" in job["prompt"] else [],
                               "requirements": [{"id": "body", "description": "有可核验来源", "kind": "text"}],
                               "steps": [{"id": "write", "member_id": "writer", "instruction": "整理报告",
                                          "expected_output": "带来源的初稿", "output_kind": "text", "depends_on": []}]}), False
        if "最终交付编辑" in system:
            return json.dumps({"title": "实验室进度", "content": "# 当天进度\n\n依据观测记录完成整理。", "missing_outputs": []}), False
        if "你是交付审查者" in system:
            return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [
                {"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "观测记录可核对"}],
                "revision_instructions": ""}), False
        return "整理完成，数据见当天观测记录。", False

    async def test_manual_retry_creates_one_verified_export_and_normal_board_result(self):
        rule = await self.create(goal="整理实验室进度并交付 Word")
        self.assertFalse(rule["enabled"])
        endpoint = f"{self.path}/{rule['id']}/run"
        client_id = str(uuid4())
        payload = {"client_request_id": client_id}
        with patch.object(self.jobs, "_todo_sync_enabled", True), patch.object(group_delivery, "model_output", side_effect=self.provider):
            responses = await asyncio.gather(*(self.client.post(endpoint, params=self.params, headers=self.origin, json=payload) for _ in range(2)))
            self.assertEqual([response.status_code for response in responses], [202, 202])
            first, second = (response.json() for response in responses)
            self.assertEqual(first, second)
            self.assertEqual((first["state"], first["client_request_id"], first["job_id"]), ("queued", client_id, None))
            await self.runner.tick()
            await asyncio.gather(*self.jobs.tasks.values())
            await self.runner.tick()
            self.assertEqual((await self.history(rule))[0]["state"], "running", "Workbench success alone must not claim verified delivery")
            await autonomy.AutonomySupervisor(self.jobs).tick()
            await self.runner.tick()
        run, = await self.history(rule)
        self.assertEqual(run["state"], "complete", self.jobs.get(run["job_id"])["result"])
        job = self.jobs.get(run["job_id"])
        self.assertEqual(job["group_context_mode"], "fresh")
        self.assertFalse(job["group_native_allowed"])
        self.assertEqual(job["external_source"], routines.external_source(self.state.history(self.project_id, self.room["id"], rule["id"])[0]))
        self.assertIn(self.spec["checks"], job["prompt"])
        self.assertIn(self.spec["process"], job["prompt"])
        self.assertEqual(job["group_document_formats"], ["html"])
        entry = job["result"]["delivery"]["documents"][0]
        _, content = self.documents.read(job, entry["id"])
        self.assertIn("当天进度".encode(), content)
        self.assertTrue(job["autonomy"]["notification_id"])
        self.assertFalse(job["autonomy"]["read"])
        cards = (await self.client.get("/api/todo")).json()
        card, = [card for card in cards if card.get("external_source")]
        self.assertEqual((card["room_id"], card["external_source"]["task_id"], card["status"]), (self.room["id"], job["id"], "done"))
        self.assertEqual(len(self.jobs.jobs), 1)

    async def test_no_selected_document_formats_follow_goal(self):
        rule = await self.create(goal="整理实验室进度并交付 Word", document_formats=[])
        response = await self.client.post(f"{self.path}/{rule['id']}/run", params=self.params, headers=self.origin,
                                          json={"client_request_id": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.text)
        with patch.object(group_delivery, "model_output", side_effect=self.provider):
            await self.runner.tick()
            await asyncio.gather(*self.jobs.tasks.values())
            await autonomy.AutonomySupervisor(self.jobs).tick()
            await self.runner.tick()
        run, = await self.history(rule)
        self.assertEqual(run["state"], "complete", run)
        job = self.jobs.get(run["job_id"])
        self.assertIsNone(job["group_document_formats"])
        document, = job["result"]["delivery"]["documents"]
        self.assertEqual(document["format"], "docx")
        _, content = self.documents.read(job, document["id"])
        self.assertTrue(content.startswith(b"PK"))

    async def test_scope_mutation_guards_and_personal_rooms(self):
        payload = {**self.spec, "client_request_id": str(uuid4())}
        for origin in ({}, {"Origin": "https://other.example"}, {**self.origin, "Sec-Fetch-Site": "cross-site"}):
            response = await self.client.post(self.path, params=self.params, headers=origin, json=payload)
            self.assertEqual(response.status_code, 403, response.text)
        for params in ({}, {"project_id": self.other_project}):
            response = await self.client.get(self.path, params=params)
            self.assertEqual(response.status_code, 403, response.text)
        personal = self.make_room(None)
        endpoint = f"/api/groups/{personal['id']}"
        response = await self.client.post(endpoint + "/routines", json=payload, headers=self.origin)
        self.assertEqual(response.status_code, 201, response.text)
        repeated = await self.client.post(endpoint + "/routines", json=payload, headers=self.origin)
        self.assertEqual(response.json(), repeated.json())
        self.assertEqual(response.json()["client_request_id"], payload["client_request_id"])
        self.assertIsNone(response.json()["project_id"])
        self.assertEqual((await self.client.get(endpoint + "/routines", params=self.params)).status_code, 403)
        self.assertEqual((await self.client.delete(endpoint, headers=self.origin)).status_code, 409)
        rule = response.json()
        deletion = await self.client.delete(endpoint + f"/routines/{rule['id']}",
                                            params={"expected_revision": rule["revision"]}, headers=self.origin)
        self.assertEqual(deletion.json(), {"removed": True})
        self.assertEqual((await self.client.delete(endpoint, headers=self.origin)).status_code, 200)

    async def test_scheduled_pause_during_admission_and_roster_drift(self):
        rule = await self.activate(await self.create(trigger={"kind": "daily", "time": "08:01", "timezone": "Asia/Shanghai"}))
        self.now += timedelta(minutes=1)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_catalog():
            entered.set()
            await release.wait()
            return ["writer-model", "reader-model"]

        self.catalog.side_effect = slow_catalog
        tick = asyncio.create_task(self.runner.tick())
        await entered.wait()
        rule = await self.activate(rule, enabled=False)
        release.set()
        await tick
        self.assertFalse(self.jobs.jobs)
        skipped, = await self.history(rule)
        self.assertEqual(skipped["state"], "skipped")
        self.assertIn("暂停", skipped["reason"])
        self.catalog.side_effect = None
        modified_team = self.teams.put(TeamRequest(name="新成员", project_id=self.project_id,
                                                  members=[{**member, "name": "新" + member["name"]} for member in self.members]))
        response = await self.client.put(f"/api/groups/{self.room['id']}/team", params=self.params,
                                        json={"team_id": modified_team["id"], "expected_revision": self.room["team_revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        endpoint = f"{self.path}/{rule['id']}"
        response = await self.client.post(endpoint + "/enabled", params=self.params, headers=self.origin,
                                          json={"enabled": True, "expected_revision": rule["revision"]})
        self.assertEqual(response.status_code, 409)
        response = await self.client.post(endpoint + "/run", params=self.params, headers=self.origin,
                                          json={"client_request_id": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.text)
        await self.runner.tick()
        skipped = (await self.history(rule))[0]
        self.assertIn("群成员已更新", skipped["reason"])
        self.assertFalse(self.jobs.jobs)

    async def test_daily_worker_runs_after_client_closes_and_restarts_without_duplicate(self):
        rule = await self.activate(await self.create(trigger={"kind": "daily", "time": "08:01", "timezone": "Asia/Shanghai"}))
        schedule = datetime.fromisoformat(rule["next_run_at"])
        self.assertEqual(schedule, self.now + timedelta(minutes=1))
        ticks = asyncio.Queue()
        advance = self.runner.tick

        async def observe_tick():
            await advance()
            ticks.put_nowait(None)

        with patch.object(self.runner, "tick", observe_tick), patch.object(group_delivery, "model_output", side_effect=self.provider):
            await self.runner.start()
            try:
                await asyncio.wait_for(ticks.get(), timeout=3)
                self.assertFalse(self.jobs.jobs)
                await self.client.aclose()
                self.now = schedule + timedelta(seconds=1)
                self.runner.wake.set()
                await asyncio.wait_for(ticks.get(), timeout=3)
                await asyncio.gather(*self.jobs.tasks.values())
                await autonomy.AutonomySupervisor(self.jobs).tick()
                await advance()
                await advance()
            finally:
                await self.runner.shutdown()

        job, = self.jobs.jobs.values()
        history = self.state.history(self.project_id, self.room["id"], rule["id"])
        run, = history
        self.assertEqual((run["kind"], run["status"], run["run_id"]), ("daily", "complete", job["id"]))
        self.assertEqual(datetime.fromisoformat(run["scheduled_at"]), schedule)
        self.assertTrue(job["autonomy"]["notification_id"])

        reloaded_state = RoutineStore(self.state.path, clock=lambda: self.now)
        reloaded_jobs = workbench.JobManager(self.jobs.directory)
        reloaded_jobs.recover()
        with patch.object(workbench, "manager", reloaded_jobs), patch.object(groups, "submit_message", AsyncMock(wraps=groups.submit_message)) as submit:
            restarted = routines.RoutineWorker(reloaded_state)
            await restarted.tick()
            await restarted.tick()
        submit.assert_not_awaited()
        self.assertEqual(list(reloaded_jobs.jobs), [job["id"]])
        self.assertEqual(reloaded_state.history(self.project_id, self.room["id"], rule["id"]), history)
        self.assertEqual(datetime.fromisoformat(reloaded_state.get(self.project_id, self.room["id"], rule["id"])["next_run_at"]),
                         schedule + timedelta(days=1))
        self.assertIsNone(autonomy.completion_problem(reloaded_jobs.get(job["id"])))

    async def test_recover_committed_job_after_lost_submit_reply(self):
        rule = await self.create()
        response = await self.client.post(f"{self.path}/{rule['id']}/run", params=self.params, headers=self.origin,
                                          json={"client_request_id": str(uuid4())})
        self.assertEqual(response.status_code, 202)
        real_submit = groups.submit_message

        async def interrupted(*args, **kwargs):
            await real_submit(*args, **kwargs)
            raise asyncio.CancelledError()

        with patch.object(self.jobs, "run", AsyncMock()), patch.object(groups, "submit_message", interrupted):
            with self.assertRaises(asyncio.CancelledError):
                await self.runner.tick()
        self.assertEqual(self.state.active()[0]["status"], "pending")
        job, = self.jobs.jobs.values()
        await self.jobs.shutdown()
        reloaded_jobs = workbench.JobManager(self.jobs.directory)
        reloaded_jobs.recover()
        reloaded_state = RoutineStore(self.state.path, clock=lambda: self.now)
        with patch.object(workbench, "manager", reloaded_jobs), patch.object(groups, "submit_message", AsyncMock()) as submit:
            await routines.RoutineWorker(reloaded_state).tick()
        submit.assert_not_awaited()
        run, = reloaded_state.active()
        self.assertEqual((run["status"], run["run_id"]), ("submitted", job["id"]))
        self.assertEqual(len(reloaded_jobs.jobs), 1)

    async def test_unreadable_existing_job_checkpoint_blocks_redispatch(self):
        rule = await self.create()
        await self.client.post(f"{self.path}/{rule['id']}/run", params=self.params, headers=self.origin,
                               json={"client_request_id": str(uuid4())})
        queued, = self.state.active()
        job_id, _ = groups.message_receipt(self.room["id"], routines.build_message(queued))
        self.jobs.directory.mkdir()
        (self.jobs.directory / (job_id + ".json")).write_text("incomplete checkpoint")
        self.jobs.recover()
        with patch.object(groups, "submit_message", AsyncMock()) as submit:
            await self.runner.tick()
        submit.assert_not_awaited()
        failed, = await self.history(rule)
        self.assertEqual((failed["state"], failed["job_id"]), ("needs_attention", None))

    async def test_phone_control_and_only_local_card_can_trigger_native_rule(self):
        trigger = {"kind": "board_card", "tag": "intake"}
        native = await self.activate(await self.create(trigger=trigger, allow_native=True))
        auth = remote_access.RemoteStore(self.root / "phone-auth")
        address = "https://modeldesk-test.example.ts.net:8443"
        auth.configure(address)
        gateway = remote_access.create_gateway(auth, upstream_transport=httpx.ASGITransport(app=self.api))
        fake_desktop = {"Origin": address, "X-ModelDesk-Remote": ""}
        async with gateway.router.lifespan_context(gateway):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=address) as phone:
                response = await phone.post("/remote/pair", headers=fake_desktop, json={"code": auth.create_pairing("test phone")["code"]})
                self.assertEqual(response.status_code, 200, response.text)
                create = {**self.spec, "client_request_id": str(uuid4()), "allow_native": True}
                response = await phone.post(self.path, params=self.params, headers=fake_desktop, json=create)
                self.assertEqual(response.status_code, 403, response.text)
                endpoint = f"{self.path}/{native['id']}"
                for method, suffix, body in (
                    ("PUT", "", {**self.spec, "expected_revision": native["revision"], "allow_native": False}),
                    ("POST", "/enabled", {"expected_revision": native["revision"], "enabled": False}),
                    ("POST", "/run", {"client_request_id": str(uuid4())}),
                ):
                    response = await phone.request(method, endpoint + suffix, params=self.params, headers=fake_desktop, json=body)
                    self.assertEqual(response.status_code, 403, response.text)
                response = await phone.delete(endpoint, params={**self.params, "expected_revision": native["revision"]}, headers=fake_desktop)
                self.assertEqual(response.status_code, 403, response.text)
                response = await phone.post(self.path, params=self.params, headers=fake_desktop,
                                            json={**create, "allow_native": False, "trigger": trigger})
                self.assertEqual(response.status_code, 201, response.text)
                remote_rule = await self.activate(response.json())
                self.now += timedelta(seconds=1)
                card = {"title": "收到手机任务", "description": "手机提交原始资料", "tags": ["intake"],
                        "room_id": self.room["id"], "project_id": self.project_id}
                from_phone = await phone.post("/api/todo", headers=fake_desktop, json=card)
                self.assertEqual(from_phone.status_code, 200, from_phone.text)
        self.now += timedelta(seconds=1)
        from_local = await self.client.post("/api/todo", headers=self.origin,
                                            json={**card, "title": "本机任务", "description": "本机提交原始资料"})
        self.assertEqual(from_local.status_code, 200, from_local.text)
        with patch.object(self.jobs, "run", AsyncMock()):
            await self.runner.tick()
        first_native = await self.history(native)
        skipped = next(run for run in first_native if run["state"] == "skipped")
        self.assertIn("手机创建", skipped["reason"])
        remote_run = next(run for run in await self.history(remote_rule) if run["job_id"])
        remote_job = self.jobs.get(remote_run["job_id"])
        self.assertFalse(remote_job["group_native_allowed"])
        self.assertIn("手机提交原始资料", remote_job["prompt"])
        self.assertEqual(remote_job["external_source"]["card_id"], from_phone.json()["id"])
        native_run = next(run for run in first_native if run["job_id"])
        native_job = self.jobs.get(native_run["job_id"])
        self.assertTrue(native_job["group_native_allowed"])
        self.assertEqual(native_job["external_source"]["card_id"], from_local.json()["id"])

    async def test_designer_interview_optimize_and_walkthrough_are_read_only_and_safe(self):
        async def fake_model(job, model, system, prompt, **kwargs):
            if "自动化演练模拟官" in system:
                return json.dumps({
                    "overall_assessment": "主流程清晰，但冲突优先级尚未写明。",
                    "cases": [{
                        "title": "邮件与看板日期冲突",
                        "scenario": "邮件写周四交付，看板写周三交付。",
                        "predicted_steps": "读取两处来源并比对日期。",
                        "decision": "ask_human",
                        "applied_rule": "当前配置未约定冲突时以哪份为准",
                        "gap_detected": "缺少多来源冲突时的信任优先级",
                        "suggested_judgment_rule": "当邮件与看板截止日期冲突时，以看板为准并在报告标注差异",
                    }],
                }), False
            return json.dumps({
                "goal_clarity_note": "“发邮件”只是步骤，你的真正目标是确保每周风险被及时同步并闭环。",
                "restatement": "每两天整理一次实验进度与风险，核对来源后生成简报。",
                "next_question": "当聊天的口头进度和看板状态不一致时，应该以哪份为准？",
                "question_focus": "judgment",
                "suggestions": ["去掉手工复制，直接从项目看板和记录汇总"],
                "draft_spec": {
                    "name": "隔天风险简报",
                    "goal": "确保项目进度与风险每两天清晰同步且有据可查",
                    "process": "汇总看板和近期讨论，按风险优先级整理",
                    "judgment": "",
                    "checks": "每条结论注明来源",
                    "examples": "",
                    "correlation_field": "事项编号",
                    "trigger": {"kind": "interval", "every_hours": 48},
                    "document_formats": ["html"],
                    "allow_native": True,
                },
            }), False

        with patch.object(group_delivery, "model_output", fake_model):
            interview = await self.client.post(f"{self.path}/design", params=self.params, headers=self.origin, json={
                "mode": "interview",
                "message": "帮我每两天发一封项目进度邮件",
            })
            self.assertEqual(interview.status_code, 200, interview.text)
            data = interview.json()
            self.assertEqual(data["question_focus"], "judgment")
            self.assertEqual(data["draft_spec"]["trigger"], {"kind": "interval", "every_hours": 48, "time": None, "timezone": None, "weekday": None, "tag": None, "board_status": None})
            # AI can never set allow_native=True.
            self.assertFalse(data["draft_spec"]["allow_native"])
            self.assertEqual(data["readiness"]["score"], 4)

            optimized = await self.client.post(f"{self.path}/design", params=self.params, headers=self.origin, json={
                "mode": "optimize",
                "draft": data["draft_spec"],
            })
            self.assertEqual(optimized.status_code, 200, optimized.text)
            self.assertTrue(optimized.json()["suggestions"])

            walk = await self.client.post(f"{self.path}/walkthrough", params=self.params, headers=self.origin, json={
                "spec": data["draft_spec"],
                "scenario": "邮件说周四，看板说周三",
            })
            self.assertEqual(walk.status_code, 200, walk.text)
            self.assertEqual(walk.json()["cases"][0]["decision"], "ask_human")
            self.assertIn("以看板为准", walk.json()["cases"][0]["suggested_judgment_rule"])
        # Zero workbench jobs created during interview/optimize/walkthrough.
        self.assertEqual(self.jobs.jobs, {})

    async def test_feedback_reflect_proposal_apply_revisions_and_board_status_auto_reflect(self):
        rule = await self.activate(await self.create(
            trigger={"kind": "board_status", "board_status": "review", "tag": "qa"},
            judgment="默认以看板最新状态为准",
            examples="历史反例：遗漏来源日期导致返工",
            correlation_field="工单",
            auto_reflect=True,
        ))
        # Creating a card in 'todo' should NOT trigger board_status='review'.
        self.now += timedelta(seconds=2)
        created_card = await self.client.post("/api/todo", headers=self.origin, json={
            "title": "工单:T-88 接口检查", "description": "核对返回码", "status": "todo",
            "tags": ["qa"], "room_id": self.room["id"], "project_id": self.project_id,
        })
        self.assertEqual(created_card.status_code, 200, created_card.text)
        with patch.object(group_delivery, "model_output", self.provider):
            await self.runner.tick()
            self.assertEqual(await self.history(rule), [])
            # Moving the manual card into 'review' triggers the routine once.
            self.now += timedelta(seconds=2)
            moved = await self.client.post("/api/todo/move", headers=self.origin, json={
                "id": created_card.json()["id"], "status": "review", "from_status": "todo",
            })
            self.assertEqual(moved.status_code, 200, moved.text)
            await self.runner.tick()
            for task in list(self.jobs.tasks.values()):
                await task
            await autonomy.AutonomySupervisor(self.jobs).tick()
            self.now += timedelta(seconds=12)
            await self.runner.tick()

        runs = await self.history(rule)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["state"], "complete")
        self.assertEqual(run["trigger_kind"], "board_status")
        self.assertEqual(run["correlation_key"], "T-88")
        job = self.jobs.get(run["job_id"])
        self.assertIn("团队决策判断与求助边界", job["prompt"])
        self.assertIn("真实案例与反例", job["prompt"])
        self.assertIn("关联追踪编号（工单）：T-88", job["prompt"])

        # Record feedback + waiting_details case state.
        fb = await self.client.post(f"{self.path}/{rule['id']}/runs/{run['id']}/feedback",
                                    params=self.params, headers=self.origin, json={
                                        "verdict": "wrong_judgment",
                                        "note": "未核对附件版本号，且客户尚未回复确认",
                                        "case_state": "waiting_details",
                                    })
        self.assertEqual(fb.status_code, 201, fb.text)
        self.assertTrue(fb.json()["routine"]["telemetry"]["needs_reflection"])
        self.assertEqual(fb.json()["routine"]["telemetry"]["waiting_cases"][0]["correlation_key"], "T-88")

        # Mark case resolved via /case endpoint.
        resolved = await self.client.post(f"{self.path}/{rule['id']}/runs/{run['id']}/case",
                                          params=self.params, headers=self.origin, json={"case_state": "resolved"})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["routine"]["telemetry"]["waiting_cases"], [])

        # Reflect and generate a self-improvement proposal, then apply with optimistic lock.
        async def fake_reflect(job, model, system, prompt, **kwargs):
            return json.dumps({
                "diagnosis": "工单 T-88 反馈未核对附件版本号。",
                "changes_summary": ["在判断边界与验收标准增加附件版本核对要求"],
                "judgment": "默认以看板最新状态为准；同时核对附件版本号，不一致时先停下来求助人工。",
                "checks": "核对每项进度的来源与附件版本号",
            }), False

        with patch.object(group_delivery, "model_output", fake_reflect):
            reflected = await self.client.post(f"{self.path}/{rule['id']}/reflect", params=self.params, headers=self.origin)
            self.assertEqual(reflected.status_code, 200, reflected.text)
            self.assertTrue(reflected.json()["changed"])
            proposal = reflected.json()["proposal"]
            self.assertEqual(proposal["status"], "open")
            self.assertTrue(any(item["field"] == "judgment" for item in proposal["diff"]))

        # Stale expected_revision is rejected.
        stale = await self.client.post(
            f"{self.path}/{rule['id']}/proposals/{proposal['id']}/apply",
            params=self.params, headers=self.origin, json={"expected_revision": rule["revision"] - 1},
        )
        self.assertEqual(stale.status_code, 409)

        applied = await self.client.post(
            f"{self.path}/{rule['id']}/proposals/{proposal['id']}/apply",
            params=self.params, headers=self.origin, json={"expected_revision": rule["revision"]},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        updated_rule = applied.json()["routine"]
        self.assertEqual(updated_rule["revision"], rule["revision"] + 1)
        self.assertIn("核对附件版本号", updated_rule["judgment"])

        revs = await self.client.get(f"{self.path}/{rule['id']}/revisions", params=self.params)
        self.assertEqual(revs.status_code, 200, revs.text)
        self.assertEqual(revs.json()["revisions"][0]["change_note"], "采纳自改进建议")

        # Auto-reflect on needs_attention creates a draft proposal without changing the live spec.
        bad_run = self.state.manual_run(self.project_id, self.room["id"], updated_rule["id"], str(uuid4()), now=self.now + timedelta(seconds=30))
        self.jobs.directory.mkdir(exist_ok=True)
        bad_job_id, _ = groups.message_receipt(self.room["id"], routines.build_message(bad_run))
        (self.jobs.directory / (bad_job_id + ".json")).write_text("damaged checkpoint")
        self.jobs.recover()
        async def fake_auto_reflect(job, model, system, prompt, **kwargs):
            return json.dumps({
                "diagnosis": "检测到后台检查点损坏导致任务异常，补充检查点核验反例。",
                "changes_summary": ["在案例与反例中补充中断异常核查说明"],
                "examples": "历史反例：遗漏来源日期导致返工；若后台检查点异常则先核对群消息再重新运行。",
            }), False

        with patch.object(group_delivery, "model_output", fake_auto_reflect):
            await self.runner.tick()
        proposals = self.state.list_proposals(self.project_id, self.room["id"], updated_rule["id"], status="open")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["trigger_source"], "auto_reflect")
        # Live spec revision was NOT mutated automatically!
        self.assertEqual(self.state.get(self.project_id, self.room["id"], updated_rule["id"])["revision"], updated_rule["revision"])
        # Dismiss works cleanly.
        dismissed = await self.client.post(
            f"{self.path}/{updated_rule['id']}/proposals/{proposals[0]['id']}/dismiss",
            params=self.params, headers=self.origin,
        )
        self.assertEqual(dismissed.status_code, 200, dismissed.text)
        self.assertEqual(dismissed.json()["proposal"]["status"], "dismissed")

    async def test_phone_gateway_allows_safe_design_and_feedback_but_blocks_native_mutations(self):
        native_rule = await self.create(allow_native=True)
        safe_rule = await self.create(allow_native=False)
        native_run = self.state.manual_run(self.project_id, self.room["id"], native_rule["id"], str(uuid4()), now=self.now)
        safe_run = self.state.manual_run(self.project_id, self.room["id"], safe_rule["id"], str(uuid4()), now=self.now)
        self.state.finish(native_run["id"], "complete", now=self.now + timedelta(seconds=5))
        self.state.finish(safe_run["id"], "complete", now=self.now + timedelta(seconds=5))

        auth = remote_access.RemoteStore(self.root / "phone-auth-229")
        address = "https://modeldesk-test.example.ts.net:8443"
        auth.configure(address)
        gateway = remote_access.create_gateway(auth, upstream_transport=httpx.ASGITransport(app=self.api))
        headers = {"Origin": address, "X-ModelDesk-Remote": ""}
        async with gateway.router.lifespan_context(gateway):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=address) as phone:
                pair = await phone.post("/remote/pair", headers=headers, json={"code": auth.create_pairing("phone")["code"]})
                self.assertEqual(pair.status_code, 200)
                # Phone can record feedback on non-native routine.
                ok_fb = await phone.post(
                    f"{self.path}/{safe_rule['id']}/runs/{safe_run['id']}/feedback",
                    params=self.params, headers=headers, json={"verdict": "good", "note": "内容清晰"},
                )
                self.assertEqual(ok_fb.status_code, 201, ok_fb.text)
                # Phone is blocked from mutating or reflecting on allow_native=True routine.
                for suffix, payload in (
                    (f"/{native_rule['id']}/runs/{native_run['id']}/feedback", {"verdict": "good", "note": "ok"}),
                    (f"/{native_rule['id']}/reflect", {}),
                ):
                    denied = await phone.post(self.path + suffix, params=self.params, headers=headers, json=payload)
                    self.assertEqual(denied.status_code, 403, denied.text)

