"""Feishu self-service bootstrap: owner gating, two-step confirm, rollback and unlink."""
import asyncio
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import feishu, groups, projects, workbench
from backend.feishu_bootstrap import Bootstrap, BootstrapError
from backend.feishu_store import ConfigInput, FeishuStore, parse_setup_command
from backend.groups import RoomStore
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore

OWNER = "ou_owner"
STRANGER = "ou_stranger"


class FakeSDK:
    def __init__(self):
        self.calls = []

    def reply(self, message, kind, content, dispatch):
        self.calls.append(("reply", message, kind, content["text"]))
        return "om_reply_" + str(len(self.calls))

    def upload(self, entry, raw):
        self.calls.append(("upload", entry, raw))
        return "file_confirmed"

    def bot_info(self):
        return {"open_id": "ou_bot"}

    def chats(self, token=None):
        return {"items": [], "has_more": False, "page_token": None}


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = FeishuStore(self.root / "feishu")
        self.jobs = workbench.JobManager(self.root / "jobs")
        self.rooms = RoomStore(self.root / "rooms")
        self.teams = TeamStore(self.root / "teams")
        self.projects = projects.ProjectStore(self.root / "projects")
        self.catalog = ["conn:m1", "conn:m2", "conn:m3"]
        self.bootstrap = Bootstrap(self.store, room_store=self.rooms, team_store=self.teams,
                                   project_store=self.projects, catalog=lambda: self.catalog[:2])
        self.sdk = FakeSDK()
        self.controller = feishu.FeishuController(self.store, self.jobs, sdk_factory=lambda config: self.sdk)
        for owner, field, value in [(workbench, "manager", self.jobs), (groups, "store", self.rooms),
                                    (projects, "store", self.projects)]:
            patch.object(owner, field, value).start()
        patch.object(self.controller, "bootstrap_service", lambda: self.bootstrap).start()
        self.addCleanup(patch.stopall)
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", app_secret="secret",
                                           owner_sender_ids=[OWNER], bindings=[]))

    def event(self, **changes):
        return {"app_id": "cli_test", "sender_type": "user", "sender_id": OWNER, "chat_id": "oc_boot",
                "chat_type": "p2p", "message_id": "om_" + uuid.uuid4().hex, "message_type": "text",
                "text": "/modeldesk setup 新项目", "create_time": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                **changes}

    def received(self, **changes):
        identifier = self.store.ingest(self.event(**changes))
        return next(row for row in self.store.events() if row["id"] == identifier)

    async def tick(self):
        await self.controller.tick()

    # ---- gating ---------------------------------------------------------

    async def test_non_owner_unbound_message_is_dropped_without_persisting_body(self):
        identifier = self.store.ingest(self.event(sender_id=STRANGER))
        self.assertIsNone(identifier)
        observed = self.store.observed("cli_test")
        self.assertEqual(observed[0]["last_outcome"], "unbound")
        with self.store.db() as db:
            events = db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(events, 0)
        self.assertNotIn("新项目", json.dumps(observed))

    async def test_group_chat_setup_is_rejected_even_for_owner(self):
        identifier = self.store.ingest(self.event(chat_type="group"))
        self.assertIsNone(identifier)
        self.assertFalse(self.store.events())
        observed = self.store.observed("cli_test")
        self.assertEqual(observed[0]["last_outcome"], "unbound")

    async def test_non_setup_text_from_owner_is_still_not_persisted(self):
        identifier = self.store.ingest(self.event(text="随便聊两句"))
        self.assertIsNone(identifier)
        with self.store.db() as db:
            events = db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(events, 0)
        observed = self.store.observed("cli_test")
        self.assertNotIn("随便聊两句", json.dumps(observed))

    async def test_owner_setup_is_admitted_as_bootstrap_event_with_command_only(self):
        record = self.received()
        self.assertEqual(record["input_mode"], "bootstrap")
        self.assertEqual(record["command"], "setup 新项目")
        self.assertIsNone(record["binding"])
        self.assertIsNone(record["job_id"])
        self.assertEqual(self.sdk.calls, [])
        await self.tick()
        self.assertEqual(len(self.sdk.calls), 1)
        self.assertIn("即将创建", self.sdk.calls[0][3])
        self.assertIn("尚未执行", self.sdk.calls[0][3])
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        self.assertTrue(code.isdigit() and len(code) == 4)
        # setup alone must not create anything
        self.assertEqual(self.projects.read_registry(), {})
        self.assertEqual(self.teams.list(None), [])
        self.assertEqual(self.rooms.list(None), [])

    async def test_full_flow_creates_all_four_ledgers_with_cross_references(self):
        setup = self.received()
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        confirm = self.received(text="/modeldesk confirm " + code, message_id="om_confirm")
        await self.tick()
        self.assertEqual(self.sdk.calls[-1][3].split("\n")[0], "开通完成：")
        registry = self.projects.read_registry()
        self.assertEqual(len(registry), 1)
        project = next(iter(registry.values()))
        self.assertEqual(project["name"], "新项目")
        self.assertTrue(project["managed"])
        teams = self.teams.list(project["id"])
        self.assertEqual(len(teams), 1)
        team = teams[0]
        self.assertEqual(team["project_id"], project["id"])
        self.assertEqual([member["model"] for member in team["members"]], ["conn:m1", "conn:m2"])
        rooms = self.rooms.list(project["id"])
        self.assertEqual(len(rooms), 1)
        room = rooms[0]
        self.assertEqual(room["title"], "新项目")
        self.assertEqual(room["team_id"], team["id"])
        self.assertEqual(room["team_snapshot"]["id"], team["id"])
        bindings = self.store.config()["bindings"]
        self.assertEqual(len(bindings), 1)
        binding = bindings[0]
        self.assertEqual(binding["chat_id"], "oc_boot")
        self.assertEqual(binding["project_id"], project["id"])
        self.assertEqual(binding["room_id"], room["id"])
        self.assertEqual(binding["allowed_sender_ids"], [OWNER])
        self.assertFalse(binding["allow_native"])
        self.assertTrue(binding["natural_language_tasks"])
        # both events settled
        states = {row["id"]: row["state"] for row in self.store.events()}
        self.assertEqual(states[setup["id"]], "delivered")
        self.assertEqual(states[confirm["id"]], "delivered")

    async def test_custom_models_with_clause(self):
        setup = self.received(text="/modeldesk setup 数据分析 with conn:m3,conn:m2")
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        confirm = self.received(text="/modeldesk confirm " + code)
        await self.tick()
        project = next(iter(self.projects.read_registry().values()))
        team = self.teams.list(project["id"])[0]
        self.assertEqual([member["model"] for member in team["members"]], ["conn:m3", "conn:m2"])
        self.assertIn("conn:m3、conn:m2", self.sdk.calls[0][3])

    async def test_confirm_code_expiry_and_reuse_are_rejected(self):
        self.received()
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        # force expiry
        with self.store.db() as db:
            db.execute("UPDATE plans SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
        expired = self.received(text="/modeldesk confirm " + code)
        await self.tick()
        self.assertIn("已过期", self.sdk.calls[-1][3])
        self.assertFalse(self.projects.read_registry())
        latest = next(row for row in self.store.events() if row["id"] == expired["id"])
        self.assertEqual(latest["state"], "delivered")
        # fresh plan, used once; a new confirm cannot create a second ledger
        self.received(text="/modeldesk setup 第二个项目", message_id="om_setup2")
        await self.tick()
        code2 = self.sdk.calls[-1][3].split("/modeldesk confirm ")[1][:4]
        self.received(text="/modeldesk confirm " + code2, message_id="om_confirm2")
        await self.tick()
        self.assertIn("开通完成", self.sdk.calls[-1][3])
        self.received(text="/modeldesk confirm " + code2, message_id="om_confirm3")
        await self.tick()
        # one project only; the second confirm hit the (now bound) chat path
        self.assertEqual(len(self.projects.read_registry()), 1)

    async def test_same_message_id_redelivery_creates_one_ledger(self):
        event = self.event()
        identifier = self.store.ingest(event)
        self.assertEqual(self.store.ingest(dict(event)), identifier)
        self.assertEqual(len(self.store.events()), 1)
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        confirm_event = self.event(text="/modeldesk confirm " + code, message_id="om_confirm")
        self.store.ingest(confirm_event)
        self.store.ingest(dict(confirm_event))
        self.assertEqual(len([row for row in self.store.events() if row["command"].startswith("confirm")]), 1)
        await self.tick()
        await self.tick()
        self.assertEqual(len(self.projects.read_registry()), 1)
        project = next(iter(self.projects.read_registry().values()))
        self.assertEqual(len(self.rooms.list(project["id"])), 1)
        self.assertEqual(len(self.teams.list(project["id"])), 1)

    async def test_hourly_rate_limit(self):
        for index in range(3):
            self.received(text="/modeldesk setup 项目%d" % index, message_id="om_setup_%d" % index)
            await self.tick()
        self.assertEqual(len(self.projects.read_registry()), 0)
        fourth = self.received(text="/modeldesk setup 第四个", message_id="om_setup_4")
        await self.tick()
        self.assertIn("每小时最多 3 次", self.sdk.calls[-1][3])
        latest = next(row for row in self.store.events() if row["id"] == fourth["id"])
        self.assertEqual(latest["state"], "delivered")

    async def test_failure_rolls_back_project_and_team(self):
        self.received()
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        # Make room creation fail after project and team are created.
        def fail(room_request, teams):
            raise HTTPException(503, "磁盘错误")
        with patch.object(self.rooms, "create", side_effect=fail):
            confirm = self.received(text="/modeldesk confirm " + code)
            await self.tick()
        self.assertIn("开通失败", self.sdk.calls[-1][3])
        self.assertEqual(self.projects.read_registry(), {})
        self.assertEqual(self.rooms.list(None), [])
        # team archived
        self.assertEqual(self.teams.list(None, archived=False), [])
        latest = next(row for row in self.store.events() if row["id"] == confirm["id"])
        self.assertEqual(latest["state"], "delivered")

    async def test_unlink_removes_only_binding(self):
        setup = self.received()
        await self.tick()
        code = self.sdk.calls[0][3].split("/modeldesk confirm ")[1][:4]
        self.received(text="/modeldesk confirm " + code)
        await self.tick()
        self.assertEqual(len(self.store.config()["bindings"]), 1)
        # New message now takes the bound path with unlink command.
        unlink = self.store.ingest(self.event(text="/modeldesk unlink", message_id="om_unlink"))
        self.assertIsNotNone(unlink)
        record = next(row for row in self.store.events() if row["id"] == unlink)
        self.assertEqual(record["input_mode"], "command")
        await self.tick()
        self.assertIn("已解除", self.sdk.calls[-1][3])
        self.assertEqual(self.store.config()["bindings"], [])
        project = next(iter(self.projects.read_registry().values()))
        self.assertEqual(len(self.teams.list(project["id"])), 1)
        self.assertEqual(len(self.rooms.list(project["id"])), 1)

    async def test_unlink_requires_owner(self):
        project = self.projects.register("守卫项目")
        team = self.teams.put(TeamRequest(name="团队", project_id=project["id"], members=[
            {"id": "a", "name": "统筹", "model": "m1", "role": ""},
            {"id": "b", "name": "执行", "model": "m2", "role": ""}]))
        room = self.rooms.create(groups.RoomRequest(title="房间", project_id=project["id"], team_id=team["id"]), self.teams)
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", app_secret="secret", bindings=[{
            "chat_id": "oc_bound", "name": "已绑定", "project_id": project["id"], "room_id": room["id"],
            "allowed_sender_ids": [STRANGER], "allow_native": False, "natural_language_tasks": False}]))
        event = self.event(chat_id="oc_bound", sender_id=STRANGER, text="/modeldesk unlink")
        identifier = self.store.ingest(event)
        self.assertIsNotNone(identifier)
        await self.tick()
        self.assertIn("只有配置的管理员", self.sdk.calls[-1][3])
        self.assertEqual(len(self.store.config()["bindings"]), 1)

    async def test_no_owners_means_gate_closed(self):
        self.store.save_config(ConfigInput(enabled=True, app_id="cli_test", app_secret="secret", bindings=[]))
        self.assertIsNone(self.store.ingest(self.event()))
        self.assertFalse(self.store.events())


class RoomDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unused_room_can_be_deleted_and_used_room_returns_409(self):
        from backend import group_tasks, muse_bridge
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.rooms = RoomStore(root / "rooms")
        self.teams = TeamStore(root / "teams")
        self.projects = projects.ProjectStore(root / "projects")
        for owner, field, value in [(groups, "store", self.rooms), (projects, "store", self.projects)]:
            patch.object(owner, field, value).start()
        self.addCleanup(patch.stopall)
        project = self.projects.register("项目")
        team = self.teams.put(TeamRequest(name="团队", project_id=project["id"], members=[
            {"id": "a", "name": "统筹", "model": "m1", "role": ""},
            {"id": "b", "name": "执行", "model": "m2", "role": ""}]))
        room = self.rooms.create(groups.RoomRequest(title="空房间", project_id=project["id"], team_id=team["id"]), self.teams)
        self.assertTrue(self.rooms.exists(room["id"]))
        removed = self.rooms.hard_delete(room["id"], project["id"])
        self.assertTrue(removed["removed"])
        self.assertFalse(self.rooms.exists(room["id"]))
        # a room with history refuses deletion
        room2 = self.rooms.create(groups.RoomRequest(title="有历史的房间", project_id=project["id"], team_id=team["id"]), self.teams)
        job = {"id": str(uuid.uuid4()), "group_room_id": room2["id"], "conversation_id": room2["id"], "status": "complete"}
        with patch.object(workbench.manager, "jobs", {job["id"]: job}):
            with self.assertRaises(HTTPException) as caught:
                self.rooms.hard_delete(room2["id"], project["id"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("已有历史任务", caught.exception.detail)
        # an agent task also blocks deletion
        self.assertTrue(self.rooms.exists(room2["id"]))
        record = {"id": str(uuid.uuid4()), "project_id": project["id"], "room_id": room2["id"], "status": "complete"}
        with patch.object(workbench.manager, "jobs", {}), patch.object(group_tasks.manager, "records", {record["id"]: record}):
            with self.assertRaises(HTTPException) as caught2:
                self.rooms.hard_delete(room2["id"], project["id"])
        self.assertEqual(caught2.exception.status_code, 409)
        self.assertTrue(self.rooms.exists(room2["id"]))

    async def test_delete_endpoint_local_only_and_409_for_history(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        rooms, teams, projects_store = RoomStore(root / "rooms"), TeamStore(root / "teams"), projects.ProjectStore(root / "projects")
        for owner, field, value in [(groups, "store", rooms), (projects, "store", projects_store)]:
            patch.object(owner, field, value).start()
        self.addCleanup(patch.stopall)
        project = projects_store.register("项目")
        team = teams.put(TeamRequest(name="团队", project_id=project["id"], members=[
            {"id": "a", "name": "统筹", "model": "m1", "role": ""},
            {"id": "b", "name": "执行", "model": "m2", "role": ""}]))
        room = rooms.create(groups.RoomRequest(title="房间", project_id=project["id"], team_id=team["id"]), teams)
        api = FastAPI()
        api.include_router(groups.router)
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001",
                                     headers={"Origin": "http://localhost:5173"}) as client:
            response = await client.delete(f"/api/groups/{room['id']}", params={"project_id": project["id"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["removed"])
        # remote header is rejected
        async with httpx.AsyncClient(transport=transport, base_url="http://remote.example",
                                     headers={"X-ModelDesk-Remote": "phone"}) as client:
            response = await client.delete(f"/api/groups/{room['id']}")
            self.assertEqual(response.status_code, 403)


class ParseSetupCommandTests(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(parse_setup_command("setup 项目"), ("setup", "项目", []))
        self.assertEqual(parse_setup_command("SETUP  项目 with m1, m2"), ("setup", "项目", ["m1", "m2"]))
        self.assertEqual(parse_setup_command("setup 项目 with m1，m2；m3"), ("setup", "项目", ["m1", "m2", "m3"]))
        self.assertEqual(parse_setup_command("confirm 1234"), ("confirm", "1234", []))
        self.assertEqual(parse_setup_command("confirm 12a4"), ("confirm", "", []))
        self.assertIsNone(parse_setup_command("task 写报告"))
        self.assertIsNone(parse_setup_command(""))
        self.assertIsNone(parse_setup_command("setup"))
        self.assertEqual(parse_setup_command("setup with m1"), ("setup", "with m1", []))


def bootstrap_defaults():
    """The controller constructs Bootstrap(store) lazily; expose module defaults for patching."""
    from backend import feishu_bootstrap
    return feishu_bootstrap


if __name__ == "__main__":
    unittest.main()
