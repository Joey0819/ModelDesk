"""Room-scoped Feishu coverage through the local API and paired-phone gateway."""
import tempfile
import time
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from backend import feishu, groups, projects, remote_access, workbench
from backend.feishu_store import ConfigInput, FeishuStore
from backend.harness.manager import manager as harness
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore


LOCAL_ORIGIN = "http://localhost:5173"
PHONE_ORIGIN = "https://modeldesk.example.ts.net:8443"


class FeishuRoomSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        resources = self.enterContext(ExitStack())
        root = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        self.store = FeishuStore(root / "feishu")
        self.controller = feishu.FeishuController(self.store, workbench.JobManager(root / "jobs"))
        self.controller.status.update(state="connected", reason="private diagnostics")
        self.rooms = groups.RoomStore(root / "rooms")
        self.projects = projects.ProjectStore(root / "projects")
        self.teams = TeamStore(root / "teams")
        for module, name, replacement in (
            (feishu, "store", self.store), (feishu, "controller", self.controller),
            (groups, "store", self.rooms), (projects, "store", self.projects), (harness, "teams", self.teams),
        ):
            resources.enter_context(patch.object(module, name, replacement))
        self.project = self.projects.register("First private project")
        self.other = self.projects.register("Other private project")
        self.api = FastAPI()
        self.api.include_router(groups.router)
        self.api.include_router(feishu.router)
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.api), base_url="http://127.0.0.1:8001",
        ))
        self.anchor = await self.create_room(self.project["id"])
        self.room = await self.create_room(self.other["id"])
        self.phone = remote_access.RemoteStore(root / "phone")
        self.phone.configure(PHONE_ORIGIN)

    async def create_room(self, project_id):
        team = self.teams.put(TeamRequest(name="Saved team", project_id=project_id, members=[
            {"id": "peer", "name": "Peer", "model": "test", "role": "Review"},
            {"id": "writer", "name": "Writer", "model": "test", "role": "Write"},
        ]))
        response = await self.client.post("/api/groups", json={"title": "Duplicate name", "project_id": project_id, "team_id": team["id"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def configure(self, *, chat_id="oc_private", all_groups=False, enabled=True, room=None):
        target = room or self.anchor
        self.store.save_config(ConfigInput(enabled=enabled, app_id="cli_test", app_secret="hidden-secret",
            owner_sender_ids=["ou_owner"], bindings=[{
                "chat_id": chat_id, "name": "Private chat name", "room_id": target["id"], "project_id": target["project_id"],
                "allowed_sender_ids": ["ou_sender"], "allow_all_groups": all_groups, "allow_native": True,
                "natural_language_tasks": True, "push_group_results": True,
            }]))

    def observe(self, *, chat_id="oc_private", chat_type="p2p", text="/modeldesk task hidden message content"):
        return self.store.ingest({"app_id": "cli_test", "sender_type": "user", "sender_id": "ou_sender", "chat_id": chat_id,
            "chat_type": chat_type, "message_id": "om_" + uuid.uuid4().hex, "message_type": "text", "text": text,
            "create_time": str(int(time.time() * 1000))})

    def path(self, room):
        path = f"/api/groups/{room['id']}/feishu"
        return f"{path}?project_id={room['project_id']}" if room["project_id"] else path

    async def summary(self, room):
        response = await self.client.get(self.path(room))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_direct_private_connection_and_scoped_project_validation(self):
        self.assertEqual((await self.summary(self.room))["status"], "not_configured")
        self.assertFalse(self.store.directory.exists())
        self.configure()
        self.observe()
        self.assertEqual(await self.summary(self.anchor), {
            "room_id": self.anchor["id"], "project_id": self.project["id"], "mode": "direct", "status": "connected",
            "destination": "private", "switch_command": None, "bot_url": "https://applink.feishu.cn/client/bot/open?appId=cli_test",
        })
        self.assertEqual((await self.summary(self.room))["mode"], "none")
        path = self.path(self.anchor).split("?", 1)[0]
        self.assertEqual((await self.client.get(path)).status_code, 403)
        self.assertEqual((await self.client.get(path, params={"project_id": self.other["id"]})).status_code, 403)
        self.assertEqual((await self.client.get(path, params={"project_id": "bad"})).status_code, 422)
        self.projects.remove(self.project["id"])
        self.assertEqual((await self.client.get(self.path(self.anchor))).status_code, 404)

    async def test_all_groups_and_duplicate_names_for_future_group(self):
        self.configure(all_groups=True)
        self.observe()
        future = await self.create_room(self.other["id"])
        for room in (self.anchor, self.room, future):
            result = await self.summary(room)
            self.assertEqual(result["mode"], "all_groups")
            self.assertEqual(result["switch_command"], "/modeldesk use " + room["id"])
            event_id = self.observe(text=result["switch_command"])
            event = next(item for item in self.store.events() if item["id"] == event_id)
            with patch.object(self.controller, "reply_once") as reply:
                await self.controller.accept(event)
            reply.assert_awaited_once()
            route = self.store.get_chat_route("cli_test", "oc_private", event["config_revision"], "ou_sender")
            self.assertEqual((route["room_id"], route["project_id"]), (room["id"], room["project_id"]))

    async def test_group_unknown_legacy_and_disabled_states(self):
        independent = await self.create_room(None)
        self.configure(chat_id="oc_group", all_groups=True, room=independent)
        config = self.store.config()
        del config["bindings"][0]["project_id"]
        self.store._write_config(config)
        unknown = await self.summary(independent)
        self.assertEqual(unknown["destination"], "unknown")
        self.assertIsNone(unknown["bot_url"])
        self.assertFalse((self.store.directory / "receipts.sqlite3").exists())
        self.observe(chat_id="oc_group", chat_type="group")
        group = await self.summary(independent)
        self.assertEqual(group["destination"], "group")
        self.assertIsNone(group["bot_url"])
        self.assertEqual(group["switch_command"], "/modeldesk use " + independent["id"])
        self.configure(chat_id="oc_group", enabled=False, room=independent)
        disabled = await self.summary(independent)
        self.assertEqual((disabled["mode"], disabled["status"]), ("direct", "disabled"))
        self.assertIsNone(disabled["switch_command"])

    async def test_only_paired_gateway_can_read_safe_summary_and_cannot_manage_bindings(self):
        self.configure(all_groups=True)
        self.observe()
        direct_network_client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.api, client=("192.0.2.42", 5050)), base_url="http://127.0.0.1:8001",
        ))
        path = self.path(self.room)
        self.assertEqual((await direct_network_client.get(path, headers={"X-ModelDesk-Remote": "phone"})).status_code, 403)
        gateway = remote_access.create_gateway(self.phone, upstream_transport=httpx.ASGITransport(app=self.api))
        await self.enterAsyncContext(gateway.router.lifespan_context(gateway))
        client = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=PHONE_ORIGIN))
        self.assertEqual((await client.get(path, headers={"X-ModelDesk-Remote": "phone"})).status_code, 401)
        pairing = await client.post("/remote/pair", headers={"Origin": PHONE_ORIGIN}, json={"code": self.phone.create_pairing("phone")["code"]})
        self.assertEqual(pairing.status_code, 200, pairing.text)
        paired = await client.get(path)
        self.assertEqual(paired.status_code, 200, paired.text)
        self.assertEqual(paired.json(), await self.summary(self.room))
        self.assertEqual(paired.headers["Cache-Control"], "no-store")
        for private_data in ("hidden-secret", "ou_owner", "ou_sender", "oc_private", "hidden message content",
                             "private diagnostics", "Private chat name", self.anchor["id"], self.project["id"]):
            self.assertNotIn(private_data, paired.text)
        self.assertEqual((await client.get("/api/feishu/rooms")).status_code, 403)
        grant_path = f"/api/feishu/rooms/{self.anchor['id']}/binding/oc_private/allow-all-groups"
        self.assertEqual((await client.put(grant_path, headers={"Origin": PHONE_ORIGIN}, json={"project_id": self.project["id"]})).status_code, 403)
        self.phone.revoke()
        self.assertEqual((await client.get(path)).status_code, 401)

    async def test_local_opt_in_updates_only_expected_binding_preserving_pending_work(self):
        self.configure()
        self.observe()
        pending = self.store.events()[0]
        path = f"/api/feishu/rooms/{self.anchor['id']}/binding/oc_private/allow-all-groups"
        headers = {"Origin": LOCAL_ORIGIN}
        self.assertEqual((await self.client.put(path, headers=headers, json={"project_id": self.other["id"]})).status_code, 409)
        before = self.store.config()
        grant = await self.client.put(path, headers=headers, json={"project_id": self.project["id"]})
        self.assertEqual(grant.status_code, 200, grant.text)
        self.assertEqual(grant.json(), {"allow_all_groups": True})
        before["bindings"][0]["allow_all_groups"] = True
        self.assertEqual(self.store.config(), before)
        self.assertTrue(self.store.current(pending))
        self.assertFalse(pending["binding"]["allow_all_groups"])
        self.assertEqual((await self.summary(self.room))["mode"], "all_groups")
        self.observe()
        self.assertTrue(self.store.events()[-1]["binding"]["allow_all_groups"])
        self.assertEqual((await self.client.put(path, headers=headers, json={"project_id": self.project["id"]})).status_code, 200)
        self.assertEqual(self.store.config(), before)
        self.configure()
        self.assertFalse(self.store.current(pending))
