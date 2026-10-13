"""HTTP rooms, actual provider dispatch, persisted history and project isolation."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import attachments, group_chat, groups, projects, super_platform, workbench
from backend.harness.manager import manager as harness
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore
from backend.platform_store import PlatformStore


class GroupRoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = workbench.JobManager(self.root / "jobs")
        self.rooms = groups.RoomStore(self.root / "rooms")
        self.teams = TeamStore(self.root / "teams")
        self.projects = projects.ProjectStore(self.root / "projects")
        self.assets = attachments.AssetStore(self.root / "assets")
        self.resources = PlatformStore(self.root / "resources.json")
        self.patches = ExitStack()
        for owner, field, value in [(workbench, "manager", self.jobs), (groups, "store", self.rooms),
                                     (projects, "store", self.projects), (attachments, "store", self.assets),
                                     (super_platform, "store", self.resources), (harness, "teams", self.teams)]:
            self.patches.enter_context(patch.object(owner, field, value))
        self.catalog = self.patches.enter_context(patch.object(workbench, "model_catalog", AsyncMock(return_value=["a", "b"])))
        self.project = self.projects.register("test project")
        self.other = self.projects.register("other project")
        self.team = self.teams.put(TeamRequest(name="writers", project_id=self.project["id"], members=[
            {"id": "writer", "name": "作者", "model": "a", "role": "给出初稿"},
            {"id": "reviewer", "name": "审稿人", "model": "b", "role": "检查作者的论证"}]))
        api = FastAPI()
        api.include_router(groups.router)
        api.include_router(workbench.router)
        api.include_router(super_platform.router)
        self.client_type = httpx.AsyncClient
        self.client = self.client_type(transport=httpx.ASGITransport(app=api), base_url="http://local")
        self.calls = []
        self.gateway_handler = None

        async def handle(request):
            payload = json.loads(request.content)
            self.calls.append(payload)
            if self.gateway_handler:
                return await self.gateway_handler(payload)
            return httpx.Response(200, json={"choices": [{"message": {"content": f"OUTPUT_{payload['model']}_{len(self.calls)}"}, "finish_reason": "stop"}]})

        self.patches.enter_context(patch.object(group_chat.httpx, "AsyncClient", side_effect=lambda **kwargs: self.client_type(transport=httpx.MockTransport(handle), **kwargs)))

    async def asyncTearDown(self):
        await self.jobs.shutdown()
        await self.client.aclose()
        self.patches.close()
        self.temp.cleanup()

    async def room(self, **changes):
        response = await self.client.post("/api/groups", json={"title": "发布讨论", "project_id": self.project["id"], "team_id": self.team["id"], **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def params(self, room, **values):
        return {**({"project_id": room["project_id"]} if room["project_id"] else {}), **values}

    async def send(self, room, *, wait=True, **body):
        response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room), json={"prompt": "继续讨论", **body})
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        if wait:
            task = self.jobs.tasks.get(job["id"])
            if task:
                await task
            job = self.jobs.get(job["id"])
            self.assertEqual(job["status"], "complete", job.get("error"))
        return job

    async def test_room_snapshot_project_scope_and_reloading(self):
        room = await self.room()
        self.assertEqual((await self.client.get("/api/groups")).json(), [])
        self.assertEqual(len((await self.client.get("/api/groups", params=self.params(room))).json()), 1)
        for suffix in ["", "/messages"]:
            response = await self.client.get(f"/api/groups/{room['id']}{suffix}", params={"project_id": self.other["id"]})
            self.assertEqual(response.status_code, 403)
        changed = TeamRequest.model_validate({key: self.team[key] for key in TeamRequest.model_fields})
        changed.members[0].role = "changed role"
        self.teams.put(changed, self.team["id"])
        self.teams.archive(self.team["id"], self.project["id"])
        reloaded = groups.RoomStore(self.root / "rooms").get(room["id"], room["project_id"])
        self.assertEqual(reloaded["team_snapshot"], room["team_snapshot"])
        job = await self.send(room, recipient_member_ids=["writer"])
        self.assertIn("给出初稿", self.calls[0]["messages"][0]["content"])
        self.assertNotIn("changed role", str(self.calls))
        self.assertEqual(job["group_team_snapshot"], room["team_snapshot"])
        self.assertEqual((self.root / "rooms" / (room["id"] + ".json")).stat().st_mode & 0o777, 0o600)

    async def test_targeted_actual_dispatch_and_shared_history(self):
        room = await self.room()
        first = await self.send(room, prompt="记住项目代号 ROOM_ALPHA_735", recipient_member_ids=["writer"])
        second = await self.send(room, prompt="检查作者上一轮的内容", recipient_member_ids=["reviewer"])
        third = await self.send(room, recipient_member_ids=["reviewer", "writer"])
        self.assertEqual([call["model"] for call in self.calls], ["a", "b", "a", "b"])
        self.assertIn("ROOM_ALPHA_735", str(self.calls[1]))
        self.assertIn("OUTPUT_a_1", str(self.calls[1]))
        self.assertIn("OUTPUT_a_3", str(self.calls[3]))
        self.assertEqual(first["models"], ["a", "b"])
        self.assertEqual(second["result"]["recipient_models"], ["b"])
        self.assertEqual(second["result"]["messages"][0]["member_name"], "审稿人")
        self.assertEqual(second["result"]["messages"][0]["member_id"], "reviewer")
        self.assertEqual(third["group_recipient_member_ids"], ["writer", "reviewer"])
        self.assertEqual(second["context_history"]["used_turns"], 1)
        self.assertEqual(second["tools"], [])
        self.assertTrue(any(event.get("member_name") == "审稿人" for event in second["events"]))

    async def test_same_model_members_keep_separate_roles_mentions_history_and_artifacts(self):
        from backend.artifacts import selected_artifact
        from backend.conversations import deliverables
        roster = [
            {"id": "product", "name": "产品经理", "model": "a", "role": "PRODUCT_ROLE_18 明确产品目标"},
            {"id": "research", "name": "市场调研员", "model": "a", "role": "RESEARCH_ROLE_27 核实市场证据"},
            {"id": "decision", "name": "决策者", "model": "a", "role": "DECISION_ROLE_39 比较方案并决策"},
        ]
        self.team = self.teams.put(TeamRequest(name="同一模型的不同角色", project_id=self.project["id"], members=roster))
        room = await self.room()
        self.assertEqual(self.rooms.get(room["id"], room["project_id"])["team_snapshot"]["members"], roster)
        first = await self.send(room, prompt="请共同规划产品", rounds=2)
        self.assertEqual(first["models"], ["a"])
        self.assertEqual([call["model"] for call in self.calls], ["a"] * 6)
        self.assertEqual(first["result"]["recipient_member_ids"], [member["id"] for member in roster])
        self.assertEqual([message["member_id"] for message in first["result"]["messages"]], ["product", "research", "decision"] * 2)
        for index, call in enumerate(self.calls):
            role = roster[index % 3]
            system = call["messages"][0]["content"]
            self.assertIn(role["role"], system)
            self.assertIn(role["id"], system)
            for other in roster:
                if other["id"] != role["id"]:
                    self.assertNotIn(other["role"], system)
            if index:
                self.assertIn(f"OUTPUT_a_{index}", str(call["messages"]))
                self.assertIn(roster[(index - 1) % 3]["name"], str(call["messages"]))
        for index, member in enumerate(roster, start=3):
            artifact = selected_artifact(first, f"group:{index}")
            self.assertIn(member["name"], artifact["title"])
            self.assertEqual(artifact["content"], f"OUTPUT_a_{index + 1}")
        prior, _ = deliverables(first)
        self.assertEqual([item["member_id"] for item in prior], ["product", "research", "decision"] * 2)
        self.assertIn("成员 product", group_chat.markdown_group(first["result"])[0])
        # Rehydrate records before @mentioning only one of the three members.
        reloaded = workbench.JobManager(self.root / "jobs")
        reloaded.recover()
        self.patches.enter_context(patch.object(workbench, "manager", reloaded))
        self.jobs = reloaded
        second = await self.send(room, prompt="请调研员继续", recipient_member_ids=["research"])
        self.assertEqual(len(self.calls), 7)
        self.assertEqual([message["member_id"] for message in second["result"]["messages"]], ["research"])
        for member in roster:
            self.assertIn(member["name"], str(self.calls[-1]["messages"]))
            self.assertIn(member["id"], str(self.calls[-1]["messages"]))
        self.assertIn("OUTPUT_a_6", str(self.calls[-1]))

    async def test_same_model_member_roles_are_kept_for_tool_enabled_discussion(self):
        self.team = self.teams.put(TeamRequest(name="工具角色组", project_id=self.project["id"], members=[
            {"id": "writer", "name": "作者", "model": "a", "role": "WRITE_ONLY_ROLE"},
            {"id": "reviewer", "name": "审稿人", "model": "a", "role": "REVIEW_ONLY_ROLE"}]))
        room = await self.room()
        observed = []

        async def tool_reply(job, model, messages):
            observed.append((model, messages))
            return {"text": f"TOOL_ROLE_OUTPUT_{len(observed)}"}

        with patch.object(self.jobs, "tool_response", side_effect=tool_reply):
            result = await self.send(room, tools=["text_stats"])
        self.assertEqual([model for model, _ in observed], ["a", "a"])
        self.assertIn("WRITE_ONLY_ROLE", observed[0][1][0]["content"])
        self.assertNotIn("REVIEW_ONLY_ROLE", observed[0][1][0]["content"])
        self.assertIn("REVIEW_ONLY_ROLE", observed[1][1][0]["content"])
        self.assertIn("TOOL_ROLE_OUTPUT_1", observed[1][1][1]["content"])
        self.assertIn("成员 writer", observed[1][1][1]["content"])
        self.assertEqual([message["member_id"] for message in result["result"]["messages"]], ["writer", "reviewer"])
        self.assertEqual(self.calls, [])

    async def test_apply_roster_updates_future_dispatch_preserves_history_and_checks_scope(self):
        room = await self.room()
        first = await self.send(room, recipient_member_ids=["writer"])
        changed = TeamRequest.model_validate({key: self.team[key] for key in TeamRequest.model_fields})
        changed.members[0].name = "新作者"
        changed.members[0].role = "提出可验证的第二版"
        self.teams.put(changed, self.team["id"])
        endpoint = f"/api/groups/{room['id']}/team"
        body = {"team_id": self.team["id"], "expected_revision": room["team_revision"]}
        forbidden = await self.client.put(endpoint, params={"project_id": self.other["id"]}, json=body)
        self.assertEqual(forbidden.status_code, 403)
        updated = await self.client.put(endpoint, params=self.params(room), json=body)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertNotEqual(updated.json()["team_revision"], room["team_revision"])
        self.assertEqual((await self.client.put(endpoint, params=self.params(room), json=body)).status_code, 409)
        second = await self.send(updated.json(), recipient_member_ids=["writer"])
        self.assertEqual(second["group_team_snapshot"]["members"][0]["name"], "新作者")
        self.assertEqual(self.jobs.get(first["id"])["group_team_snapshot"]["members"][0]["name"], "作者")
        self.assertIn("提出可验证的第二版", str(self.calls[-1]))
        persisted = groups.RoomStore(self.root / "rooms").get(room["id"], room["project_id"])
        self.assertEqual(persisted["team_revision"], updated.json()["team_revision"])

    async def test_roster_apply_blocks_active_discussion_and_agent_but_allows_other_rooms(self):
        from backend import group_tasks
        room = await self.room()
        endpoint = f"/api/groups/{room['id']}/team"
        body = {"team_id": self.team["id"], "expected_revision": room["team_revision"]}
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting_gateway(_):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]})
        self.gateway_handler = waiting_gateway
        job = await self.send(room, wait=False, recipient_member_ids=["writer"])
        await asyncio.wait_for(entered.wait(), 2)
        try:
            self.assertEqual((await self.client.put(endpoint, params=self.params(room), json=body)).status_code, 409)
        finally:
            release.set()
            await self.jobs.tasks[job["id"]]
        records = {"task": {"room_id": room["id"], "project_id": room["project_id"], "status": "queued"}}
        with patch.object(group_tasks.manager, "records", records):
            self.assertEqual((await self.client.put(endpoint, params=self.params(room), json=body)).status_code, 409)
            records["task"]["room_id"] = str(uuid.uuid4())
            self.assertEqual((await self.client.put(endpoint, params=self.params(room), json=body)).status_code, 200)

    async def test_roster_change_during_gateway_check_rejects_stale_dispatch(self):
        room = await self.room()
        async def changed_catalog():
            changed = self.rooms.get(room["id"], room["project_id"])
            changed["team_snapshot"]["members"][0]["name"] = "changed while checking"
            self.rooms.save(changed)
            return ["a", "b"]
        self.catalog.side_effect = changed_catalog
        response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room), json={"prompt": "test"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.jobs.jobs, {})

    async def test_followup_waits_for_prior_turn_and_restart_keeps_history(self):
        room = await self.room()
        entered, release = asyncio.Event(), asyncio.Event()

        async def gateway(payload):
            if payload["model"] == "a":
                entered.set()
                await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "SERIALIZED_FIRST" if payload["model"] == "a" else "SECOND_REPLY"}, "finish_reason": "stop"}]})

        self.gateway_handler = gateway
        first = await self.send(room, wait=False, recipient_member_ids=["writer"])
        await asyncio.wait_for(entered.wait(), 2)
        second = await self.send(room, wait=False, recipient_member_ids=["reviewer"])
        self.assertEqual(len(self.calls), 1)
        release.set()
        await asyncio.gather(*list(self.jobs.tasks.values()))
        self.assertIn("SERIALIZED_FIRST", str(self.calls[1]))
        reloaded = workbench.JobManager(self.root / "jobs")
        reloaded.recover()
        self.assertEqual(reloaded.get(first["id"])["status"], "complete")
        self.assertEqual(reloaded.get(second["id"])["group_room_id"], room["id"])
        self.patches.enter_context(patch.object(workbench, "manager", reloaded))
        self.jobs = reloaded
        continued = await self.send(room, recipient_member_ids=["reviewer"])
        self.assertEqual(continued["context_history"]["used_turns"], 2)
        self.assertIn("SERIALIZED_FIRST", str(self.calls[-1]))

    async def test_chronological_pagination_and_other_room_isolation(self):
        room = await self.room()
        sent = [await self.send(room, prompt=f"room turn {index}", recipient_member_ids=["writer"]) for index in range(3)]
        foreign = await self.room(title="another room")
        foreign_job = await self.send(foreign, prompt="FOREIGN_ROOM_PRIVATE", recipient_member_ids=["writer"])
        self.assertNotIn("room turn", str(self.calls[-1]))
        latest = (await self.client.get(f"/api/groups/{room['id']}/messages", params=self.params(room, limit=2))).json()
        self.assertEqual([job["id"] for job in latest["jobs"]], [job["id"] for job in sent[1:]])
        self.assertEqual(latest["next_before"], sent[1]["id"])
        earlier = (await self.client.get(f"/api/groups/{room['id']}/messages", params=self.params(room, limit=2, before=latest["next_before"]))).json()
        self.assertEqual([job["id"] for job in earlier["jobs"]], [sent[0]["id"]])
        self.assertIsNone(earlier["next_before"])
        response = await self.client.get(f"/api/groups/{room['id']}/messages", params=self.params(room, before=foreign_job["id"]))
        self.assertEqual(response.status_code, 422)
        continued = await self.send(room, recipient_member_ids=["reviewer"], context_turns=1)
        self.assertEqual(continued["context_history"]["used_turns"], 1)
        self.assertNotIn("FOREIGN_ROOM_PRIVATE", str(self.calls[-1]))
        self.assertLessEqual(continued["context_history"]["chars"], continued["context_history"]["budget_chars"])

    async def test_normal_harness_and_import_jobs_cannot_inject_room_history(self):
        room = await self.room()
        request = {"mode": "group", "prompt": "injected", "models": ["a", "b"], "conversation_id": room["id"], "project_id": room["project_id"], "group_room_id": room["id"]}
        response = await self.client.post("/api/workbench/jobs", json=request)
        self.assertEqual(response.status_code, 403, response.text)
        with self.assertRaises(HTTPException):
            self.jobs.create(workbench.JobRequest.model_validate(request), metadata={"harness_run_id": str(uuid.uuid4())})
        legitimate = await self.send(room, recipient_member_ids=["writer"])
        imported = copy.deepcopy(legitimate)
        imported["id"] = str(uuid.uuid4())
        imported["events"] = []
        for key in list(imported):
            if key.startswith("group_"):
                imported.pop(key)
        from backend.super_platform import ImportRequest, import_data
        with self.assertRaises(HTTPException) as caught:
            import_data(ImportRequest(data={"format": "modeldesk-platform", "resources": {}, "jobs": [imported]}))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(len(self.jobs.jobs), 1)

    async def test_invalid_recipients_context_and_disconnected_models_do_not_dispatch(self):
        room = await self.room()
        for body in [{"recipient_member_ids": ["unknown"]}, {"recipient_member_ids": ["writer", "writer"]},
                     {"project_id": self.other["id"]}, {"models": ["b"]}, {"tools": ["arbitrary_shell"]}, {"rounds": 7}]:
            response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room), json={"prompt": "test", **body})
            self.assertEqual(response.status_code, 422, response.text)
        foreign_knowledge = self.resources.put("knowledge", {"name": "other", "content": "PRIVATE_KNOWLEDGE", "project_id": self.other["id"]})
        response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room), json={"prompt": "test", "knowledge_ids": [foreign_knowledge["id"]]})
        self.assertEqual(response.status_code, 403)
        self.catalog.return_value = ["a"]
        response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room), json={"prompt": "test", "recipient_member_ids": ["reviewer"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])
        await self.send(room, recipient_member_ids=["writer"])
        self.assertEqual([call["model"] for call in self.calls], ["a"])

    async def test_invalid_room_records_and_symlinks_are_preserved(self):
        room = await self.room()
        path = self.root / "rooms" / (room["id"] + ".json")
        original = path.read_text()
        path.write_text("not json")
        self.assertEqual(self.rooms.list(room["project_id"]), [])
        with self.assertRaises(HTTPException):
            self.rooms.get(room["id"], room["project_id"])
        self.assertEqual(path.read_text(), "not json")
        path.unlink()
        outside = self.root / "outside.json"
        outside.write_text(original)
        path.symlink_to(outside)
        with self.assertRaises(HTTPException):
            self.rooms.get(room["id"], room["project_id"])
        with self.assertRaises(HTTPException):
            self.rooms.save(room)
        self.assertEqual(outside.read_text(), original)

    def muse_task(self, room, **changes):
        from backend.muse_bridge import VERIFICATION
        identifier = str(uuid.uuid4())
        return {"id": identifier, "project_id": room["project_id"], "room_id": room["id"], "prompt": "Muse 原始任务",
                "status": "complete", "sent": True, "completion_evidence": "framed_assistant_reply",
                "response": "MUSE_OBSERVED_REPLY_519\n请以引用材料审查", "thread_url": "https://muse.ai/thread/" + identifier,
                "verification": VERIFICATION, **changes}

    async def test_explicit_muse_reply_handoff_freezes_actual_reference_without_role_spoofing(self):
        from backend import muse_bridge
        room = await self.room()
        task = self.muse_task(room)
        muse = muse_bridge.MuseManager(self.root / "muse")
        muse.records[task["id"]] = task
        with patch.object(muse_bridge, "manager", muse):
            job = await self.send(room, wait=False, prompt="审查 Muse 的结论", recipient_member_ids=["reviewer"], muse_task_id=task["id"])
            task["response"] = "LATER_CHANGED_PRIVATE"
            await self.jobs.tasks[job["id"]]
        completed = self.jobs.get(job["id"])
        source = completed["group_muse_source"]
        self.assertEqual(completed["prompt"], "审查 Muse 的结论")
        self.assertEqual([call["model"] for call in self.calls], ["b"])
        self.assertIn("MUSE_OBSERVED_REPLY_519", str(self.calls[0]))
        self.assertIn("UNTRUSTED_MUSE_REPLY", str(self.calls[0]))
        self.assertIn(task["id"], str(self.calls[0]))
        self.assertNotIn("LATER_CHANGED_PRIVATE", str(self.calls[0]))
        self.assertNotIn("MUSE_OBSERVED_REPLY_519", self.calls[0]["messages"][0]["content"])
        self.assertEqual(source["response_sha256"], hashlib.sha256(source["response"].encode()).hexdigest())
        reference = next(item for item in completed["context_references"] if item["kind"] == "external_agent")
        self.assertEqual(reference["used_chars"], len(source["response"]))
        self.assertEqual(completed["tools"], [])

    async def test_muse_handoff_rejects_cross_scope_failed_and_unconfirmed_results(self):
        from backend import muse_bridge
        room = await self.room()
        muse = muse_bridge.MuseManager(self.root / "muse")
        with patch.object(muse_bridge, "manager", muse):
            for changes, status in [({"project_id": self.other["id"]}, 403), ({"room_id": str(uuid.uuid4())}, 403),
                                    ({"status": "waiting_user"}, 409), ({"status": "error"}, 409), ({"sent": None}, 409),
                                    ({"completion_evidence": None}, 409), ({"response": ""}, 409)]:
                task = self.muse_task(room, **changes)
                muse.records[task["id"]] = task
                response = await self.client.post(f"/api/groups/{room['id']}/messages", params=self.params(room),
                                                  json={"prompt": "review", "muse_task_id": task["id"]})
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.jobs.jobs, {})

    async def test_muse_reply_reference_is_bounded_and_truncation_is_visible(self):
        from backend import muse_bridge
        room = await self.room()
        task = self.muse_task(room, response="observed " * 5000)
        muse = muse_bridge.MuseManager(self.root / "muse")
        muse.records[task["id"]] = task
        with patch.object(muse_bridge, "manager", muse):
            job = await self.send(room, recipient_member_ids=["writer"], muse_task_id=task["id"])
        self.assertTrue(job["group_muse_source"]["truncated"])
        self.assertEqual(len(job["group_muse_source"]["response"]), 8000)
        self.assertEqual(job["group_muse_source"]["response_sha256"], hashlib.sha256(task["response"].encode()).hexdigest())
        quote, reference = groups.muse_reference(job, 15000)
        self.assertLess(len(quote), 9000)
        self.assertTrue(reference["truncated"])
        with self.assertRaises(HTTPException):
            groups.muse_reference(job, 23900)


if __name__ == "__main__":
    unittest.main()
