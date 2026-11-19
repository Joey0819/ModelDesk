"""Tests for the Todo Kanban board API."""
import copy
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from backend import todo_board


def _make_app():
    app = FastAPI()
    app.include_router(todo_board.router)
    return app


class TodoBoardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name) / "todo"
        self.board_file = self.data_dir / "board.json"
        # Patch DATA_DIR and BOARD_FILE
        self._patches = [
            patch.object(todo_board, "DATA_DIR", self.data_dir),
            patch.object(todo_board, "BOARD_FILE", self.board_file),
        ]
        for p in self._patches:
            p.start()
        # Reset cache
        todo_board._cache = None

    def tearDown(self):
        for p in self._patches:
            p.stop()
        todo_board._cache = None
        self._tmpdir.cleanup()

    async def _client(self):
        app = _make_app()
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    # --- Create ---

    async def test_create_task_success(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "测试任务"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["title"], "测试任务")
            self.assertEqual(data["status"], "todo")
            self.assertEqual(data["priority"], "medium")
            self.assertIn("id", data)
            self.assertIn("created_at", data)
            self.assertIn("updated_at", data)
            self.assertFalse(data["archived"])

    async def test_create_with_all_fields(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={
                "title": "完整任务",
                "description": "详细描述",
                "status": "doing",
                "priority": "high",
                "assignee": "张三",
                "due_date": "2026-12-31",
                "tags": ["前端", "紧急"],
                "position": 5,
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["assignee"], "张三")
            self.assertEqual(data["due_date"], "2026-12-31")
            self.assertEqual(data["tags"], ["前端", "紧急"])
            self.assertEqual(data["position"], 5)

    # --- Validation failures ---

    async def test_empty_title_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": ""})
            self.assertEqual(resp.status_code, 422)

    async def test_whitespace_only_title_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "   "})
            self.assertEqual(resp.status_code, 422)

    async def test_long_title_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "a" * 201})
            self.assertEqual(resp.status_code, 422)

    async def test_invalid_status_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "test", "status": "invalid"})
            self.assertEqual(resp.status_code, 422)

    async def test_control_char_title_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "bad\x00title"})
            self.assertEqual(resp.status_code, 422)

    async def test_credential_title_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "sk-abcdef1234567890abcdef1234567890"})
            self.assertEqual(resp.status_code, 422)

    async def test_invalid_due_date_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "test", "due_date": "not-a-date"})
            self.assertEqual(resp.status_code, 422)

    async def test_extra_field_rejected(self):
        async with await self._client() as client:
            resp = await client.post("/api/todo", json={"title": "test", "unknown_field": "x"})
            self.assertEqual(resp.status_code, 422)

    # --- Partial update ---

    async def test_partial_update(self):
        async with await self._client() as client:
            created = (await client.post("/api/todo", json={"title": "原始标题"})).json()
            resp = await client.put(f"/api/todo/{created['id']}", json={"status": "doing", "priority": "urgent"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "doing")
            self.assertEqual(data["priority"], "urgent")
            self.assertEqual(data["title"], "原始标题")  # unchanged
            self.assertNotEqual(data["updated_at"], created["updated_at"])

    async def test_update_nonexistent_returns_404(self):
        async with await self._client() as client:
            resp = await client.put("/api/todo/nonexistent-id", json={"title": "new"})
            self.assertEqual(resp.status_code, 404)

    async def test_stale_card_edit_does_not_reassign_failed_fleet_job(self):
        from backend import fleet

        app = _make_app()
        app.include_router(fleet.router)
        store = fleet.FleetStore(Path(self._tmpdir.name) / "fleet")
        self.enterContext(patch.object(fleet, "store", store))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8001",
            headers={"Origin": "http://localhost:5173"},
        ) as client:
            token = (await client.post("/api/fleet/enroll-tokens")).json()["token"]
            enrolled = await client.post("/api/fleet/enroll", json={
                "token": token, "node_id": "test-worker", "name": "Fixture worker",
                "platform": "linux", "hostname": "fixture", "agent_version": "0.2.0",
            })
            self.assertEqual(enrolled.status_code, 200, enrolled.text)
            node_headers = {"Authorization": f"Bearer {enrolled.json()['node_token']}"}
            job = (await client.post("/api/fleet/jobs", json={"title": "Fleet fixture", "task_type": "generic"})).json()
            route = f"/api/fleet/jobs/{job['job_id']}"
            card = (await client.get("/api/todo")).json()[0]
            self.assertEqual(card["status"], "todo")

            poll = await client.post("/api/fleet/jobs/poll", json={}, headers=node_headers)
            self.assertEqual(poll.json()["assigned_job"]["job_id"], job["job_id"])
            report = await client.post(f"{route}/report", json={"status": "error", "error": "fixture failed"}, headers=node_headers)
            self.assertEqual(report.status_code, 200, report.text)

            saved = await client.put(f"/api/todo/{card['id']}", json={"priority": "urgent", "status": card["status"]})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["status"], "blocked")
            self.assertEqual(saved.json()["priority"], "urgent")
            self.assertEqual((await client.get(route)).json()["status"], "error")

            moved = await client.post("/api/todo/move", json={
                "id": card["id"], "status": "done", "from_status": "blocked",
            })
            self.assertEqual(moved.status_code, 200, moved.text)
            self.assertEqual(moved.json()["status"], "done")
            self.assertEqual(moved.json()["status_mode"], "manual")
            self.assertEqual(moved.json()["source_status"], "blocked")
            self.assertEqual((await client.get(route)).json()["status"], "error")

            todo_board._cache = None
            self.assertEqual((await client.get("/api/todo")).json()[0], moved.json())
            reassigned = await client.post(f"{route}/reassign")
            self.assertEqual(reassigned.status_code, 200, reassigned.text)
            self.assertEqual(reassigned.json()["status"], "queued")
            refreshed = (await client.get("/api/todo")).json()[0]
            self.assertEqual(refreshed["id"], card["id"])
            self.assertEqual(refreshed["status"], "done")
            self.assertEqual(refreshed["source_status"], "todo")
            self.assertEqual(refreshed["priority"], "urgent")
            reset = await client.put(f"/api/todo/{card['id']}", json={"status_mode": "source"})
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(reset.json()["status"], "todo")
            self.assertEqual(reset.json()["status_mode"], "source")

    async def test_terminal_automatic_delivery_uses_delivery_status_and_supports_job_lookup(self):
        from backend import workbench
        from backend.todo_sync import sync_task

        app = _make_app()
        app.include_router(workbench.router)
        manager = workbench.JobManager(Path(self._tmpdir.name) / "workbench")
        manager._todo_sync_enabled = True
        self.enterContext(patch.object(workbench, "manager", manager))
        project_id, room_id = str(uuid.uuid4()), str(uuid.uuid4())
        jobs = {}
        for label, mode, delivery, expected in [
            ("Incomplete", "auto", {"status": "partial"}, "review"),
            ("Needs revision", "auto", {"status": "partial", "validation": {"status": "needs_revision"}}, "review"),
            ("Blocked", "auto", {"status": "blocked"}, "blocked"),
            ("Delivered", "auto", {"status": "complete", "validation": {"status": "passed"}}, "done"),
            ("Discussion", "discussion", None, "done"),
        ]:
            job = {
                "id": str(uuid.uuid4()), "mode": "group", "prompt": label, "status": "running", "events": [],
                "group_delivery_mode": mode, "project_id": project_id, "group_room_id": room_id,
            }
            jobs[label] = (job, expected)
            manager.jobs[job["id"]] = job
            manager.save(job)
            job.update(status="complete", result={"delivery": delivery})
            manager.save(job)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listed = {task["title"]: task for task in (await client.get("/api/todo")).json()}
            for label, (job, expected) in jobs.items():
                with self.subTest(label=label):
                    self.assertEqual(listed[label]["status"], expected)
                    self.assertEqual(listed[label]["project_id"], project_id)
                    job_id = listed[label]["external_source"]["task_id"]
                    saved = (await client.get(f"/api/workbench/jobs/{job_id}")).json()
                    self.assertEqual(saved["id"], job["id"])
                    self.assertEqual(saved["group_room_id"], room_id)
                    self.assertEqual(saved["result"], job["result"])
            self.assertIn("部分交付", listed["Incomplete"]["description"])
            self.assertIn("未通过验收", listed["Needs revision"]["description"])
            self.assertIn("交付受阻", listed["Blocked"]["description"])

            # Old cards were persisted as done by the generic status mapping.
            job, _ = jobs["Incomplete"]
            sync_task("workbench", job["id"], title="Incomplete", status="complete", project_id=project_id)
            old = (await client.get("/api/todo")).json()
            self.assertEqual(next(task for task in old if task["title"] == "Incomplete")["status"], "done")
            manager.recover()
            repaired = (await client.get("/api/todo")).json()
            self.assertEqual(next(task for task in repaired if task["title"] == "Incomplete")["status"], "review")

    async def test_only_local_admin_can_cancel_fleet_from_board(self):
        from backend import fleet

        store = fleet.FleetStore(Path(self._tmpdir.name) / "fleet")
        self.enterContext(patch.object(fleet, "store", store))
        job = store.enqueue_job(fleet.CreateJobRequest(title="Fleet fixture", task_type="generic"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_make_app()), base_url="http://127.0.0.1:8001") as client:
            card = (await client.get("/api/todo")).json()[0]
            route = f"/api/todo/{card['id']}"
            local = {"Origin": "http://localhost:5173"}
            phone = {**local, "X-ModelDesk-Remote": "phone"}
            denied = await client.put(route, json={"archived": True, "priority": "urgent"}, headers=phone)
            self.assertEqual(denied.status_code, 403)
            self.assertEqual((await client.delete(route, headers=phone)).status_code, 403)
            self.assertEqual(store.get_job(job["job_id"])["status"], "queued")
            visible = (await client.get("/api/todo")).json()[0]
            self.assertEqual(visible["priority"], "medium")

            manual = (await client.post("/api/todo", json={"title": "Manual"}, headers=phone)).json()
            self.assertEqual((await client.delete(f"/api/todo/{manual['id']}", headers=phone)).status_code, 200)
            cancelled = await client.delete(route, headers=local)
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertEqual(store.get_job(job["job_id"])["status"], "cancelled")

    # --- Soft delete ---

    async def test_soft_delete(self):
        async with await self._client() as client:
            created = (await client.post("/api/todo", json={"title": "要删除"})).json()
            resp = await client.delete(f"/api/todo/{created['id']}")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["archived"])
            # Should not appear in default list
            lst = (await client.get("/api/todo")).json()
            self.assertEqual(len(lst), 0)
            # Should appear with archived=true
            lst = (await client.get("/api/todo?archived=true")).json()
            self.assertEqual(len(lst), 1)

    async def test_delete_nonexistent_returns_404(self):
        async with await self._client() as client:
            resp = await client.delete("/api/todo/nonexistent-id")
            self.assertEqual(resp.status_code, 404)

    # --- Reorder ---

    async def test_reorder(self):
        async with await self._client() as client:
            t1 = (await client.post("/api/todo", json={"title": "A"})).json()
            t2 = (await client.post("/api/todo", json={"title": "B"})).json()
            t3 = (await client.post("/api/todo", json={"title": "C"})).json()
            resp = await client.post("/api/todo/reorder", json={
                "ids": [t3["id"], t1["id"], t2["id"]],
                "status": "doing",
                "position_start": 0,
            })
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["reordered"], 3)
            # Verify all moved to doing with correct positions
            lst = (await client.get("/api/todo")).json()
            doing = [t for t in lst if t["status"] == "doing"]
            self.assertEqual(len(doing), 3)
            doing.sort(key=lambda t: t["position"])
            self.assertEqual(doing[0]["id"], t3["id"])
            self.assertEqual(doing[1]["id"], t1["id"])
            self.assertEqual(doing[2]["id"], t2["id"])

    async def test_reorder_mixed_project_scopes(self):
        """A column legitimately mixes manual cards and project cards.

        The board lists every scope in one column, so the drag payload spans
        scopes; rejecting that made every drag in a mixed column fail with 422.
        """
        async with await self._client() as client:
            manual = (await client.post("/api/todo", json={"title": "Manual"})).json()
            scoped = (await client.post("/api/todo", json={
                "title": "Scoped",
                "project_id": "11111111-1111-1111-1111-111111111111",
            })).json()
            resp = await client.post("/api/todo/reorder", json={
                "ids": [scoped["id"], manual["id"]],
                "status": "todo",
                "position_start": 0,
            })
            self.assertEqual(resp.status_code, 200, resp.text)
            lst = (await client.get("/api/todo")).json()
            todo = sorted([t for t in lst if t["status"] == "todo"], key=lambda t: t["position"])
            self.assertEqual([t["id"] for t in todo], [scoped["id"], manual["id"]])

    async def test_reorder_nonexistent_id_returns_422(self):
        async with await self._client() as client:
            t1 = (await client.post("/api/todo", json={"title": "A"})).json()
            resp = await client.post("/api/todo/reorder", json={
                "ids": [t1["id"], "nonexistent"],
                "status": "todo",
                "position_start": 0,
            })
            self.assertEqual(resp.status_code, 422)

    async def test_reorder_cannot_move_synced_task_to_a_stale_or_manual_status(self):
        from backend.todo_sync import sync_task

        sync_task("group", "task-id", title="Group fixture", status="complete")
        async with await self._client() as client:
            card = (await client.get("/api/todo")).json()[0]
            manual = (await client.post("/api/todo", json={"title": "Manual"})).json()
            rejected = await client.post("/api/todo/reorder", json={
                "ids": [manual["id"], card["id"]], "status": "doing",
            })
            self.assertEqual(rejected.status_code, 409)
            listed = {task["id"]: task for task in (await client.get("/api/todo")).json()}
            self.assertEqual(listed[card["id"]]["status"], "done")
            self.assertEqual(listed[manual["id"]]["status"], "todo")

            accepted = await client.post("/api/todo/reorder", json={
                "ids": [manual["id"], card["id"]], "status": "done", "position_start": 5,
            })
            self.assertEqual(accepted.status_code, 200, accepted.text)
            listed = {task["id"]: task for task in (await client.get("/api/todo")).json()}
            self.assertEqual(listed[card["id"]]["position"], 6)

    async def test_filtered_move_keeps_other_cards_in_order_and_following_source(self):
        from backend.todo_sync import sync_task

        project, other_project = str(uuid.uuid4()), str(uuid.uuid4())
        for key, scope, state in (
            ("first", project, "running"), ("hidden", other_project, "running"),
            ("last", project, "running"), ("moved", project, "queued"),
        ):
            sync_task("group", key, title=key, status=state, project_id=scope)

        async with await self._client() as client:
            def by_source(cards):
                return {card["external_source"]["task_id"]: card for card in cards}

            def lane_order(cards, status):
                return [card["external_source"]["task_id"] for card in
                        sorted(cards, key=lambda c: c["position"]) if card["status"] == status]

            all_cards = (await client.get("/api/todo")).json()
            self.assertEqual(lane_order(all_cards, "doing"), ["first", "hidden", "last"])
            visible = by_source((await client.get("/api/todo", params={"project_id": project})).json())
            self.assertEqual(set(visible), {"first", "last", "moved"})
            placement = {"id": visible["moved"]["id"], "status": "doing", "from_status": "todo",
                         "anchor_id": visible["last"]["id"], "placement": "before"}
            result = await client.post("/api/todo/move", json=placement)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["source_status"], "todo")
            snapshot = (await client.get("/api/todo")).json()
            self.assertEqual(lane_order(snapshot, "doing"), ["first", "hidden", "moved", "last"])
            self.assertEqual({key: card["status_mode"] for key, card in by_source(snapshot).items()},
                             {"first": "source", "hidden": "source", "last": "source", "moved": "manual"})
            rejected = await client.post("/api/todo/move", json=placement)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertEqual((await client.get("/api/todo")).json(), snapshot)

            result = await client.post("/api/todo/move", json={
                "id": visible["last"]["id"], "status": "doing", "from_status": "doing",
                "anchor_id": visible["first"]["id"], "placement": "before",
            })
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status_mode"], "source")
            for key in ("moved", "last"):
                sync_task("group", key, title=key, status="complete", project_id=project)
            todo_board._cache = None
            persisted = (await client.get("/api/todo")).json()
            self.assertEqual(lane_order(persisted, "doing"), ["first", "hidden", "moved"])
            self.assertEqual(lane_order(persisted, "done"), ["last"])
            self.assertEqual(by_source(persisted)["moved"]["source_status"], "done")

    async def test_recovery_respects_archives_and_capacity_in_one_save(self):
        def source(key, status, **extras):
            return {"external_source": {"platform": "muse", "task_id": key},
                    "title": key, "status": status, **extras}

        async with await self._client() as client:
            preserved = todo_board.upsert_external_task(**source("preserved", "doing"))
            edit = await client.put(f"/api/todo/{preserved['id']}", json={
                "board_status": "done", "title": "my label", "priority": "urgent",
            })
            self.assertEqual(edit.status_code, 200, edit.text)
            archived = todo_board.upsert_external_task(**source("archived", "doing"))
            todo_board.archive_external_task(archived["external_source"])
            todo_board.upsert_external_task(**source("cancelled", "doing"))
            todo_board.upsert_external_task(**source("following", "doing"))
            room, project = str(uuid.uuid4()), str(uuid.uuid4())
            batch = [source("newest", "todo", project_id=project, room_id=room),
                     source("too old", "todo"), source("cancelled", "archived"),
                     source("missing cancelled", "archived"), source("archived", "blocked"),
                     source("preserved", "blocked", project_id=project, room_id=room),
                     source("preserved", "review"), source("following", "blocked")]
            with (patch.object(todo_board, "MAX_ACTIVE_TASKS", 3),
                  patch.object(todo_board, "_save_board", wraps=todo_board._save_board) as save):
                self.assertEqual(todo_board.backfill_external_tasks(batch), 5)
                save.assert_called_once()
                self.assertEqual(todo_board.backfill_external_tasks(batch), 0)
                save.assert_called_once()

            todo_board._cache = None
            listed = (await client.get("/api/todo", params={"archived": True})).json()
            cards = {card["external_source"]["task_id"]: card for card in listed}
            self.assertEqual(set(cards), {"newest", "preserved", "archived", "cancelled", "following"})
            self.assertEqual((cards["newest"]["project_id"], cards["newest"]["room_id"]), (project, room))
            self.assertEqual((cards["preserved"]["status"], cards["preserved"]["source_status"],
                              cards["preserved"]["title"], cards["preserved"]["priority"]),
                             ("done", "blocked", "my label", "urgent"))
            self.assertEqual(cards["following"]["status"], "blocked")
            self.assertEqual(cards["archived"]["source_status"], "blocked")
            for key in ("archived", "cancelled"):
                self.assertTrue(cards[key]["archived"])
                self.assertEqual(cards[key]["status"], "doing")

    # --- Corrupted file tolerance ---

    async def test_corrupted_file_returns_500(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.board_file.write_text("{invalid json content!!!", encoding="utf-8")
        todo_board._cache = None
        async with await self._client() as client:
            resp = await client.get("/api/todo")
            self.assertEqual(resp.status_code, 500)
            self.assertIn("损坏", resp.json()["detail"])

    # --- Active task limit ---

    async def test_active_task_limit(self):
        todo_board.MAX_ACTIVE_TASKS = 5
        try:
            async with await self._client() as client:
                for i in range(5):
                    resp = await client.post("/api/todo", json={"title": f"Task {i}"})
                    self.assertEqual(resp.status_code, 200)
                resp = await client.post("/api/todo", json={"title": "Over limit"})
                self.assertEqual(resp.status_code, 422)
        finally:
            todo_board.MAX_ACTIVE_TASKS = 2000

    # --- Concurrent write safety ---

    async def test_concurrent_writes_no_data_loss(self):
        """Multiple threads creating tasks should not lose any."""
        async with await self._client() as client:
            # Pre-create the board file
            await client.post("/api/todo", json={"title": "init"})

        errors = []
        created_ids = []
        lock = threading.Lock()

        def create_task(index):
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _do():
                    async with await self._client() as c:
                        resp = await c.post("/api/todo", json={"title": f"Concurrent {index}"})
                        if resp.status_code == 200:
                            with lock:
                                created_ids.append(resp.json()["id"])
                        else:
                            with lock:
                                errors.append(f"Thread {index}: status {resp.status_code}")
                loop.run_until_complete(_do())
                loop.close()
            except Exception as e:
                with lock:
                    errors.append(f"Thread {index}: {e}")

        threads = [threading.Thread(target=create_task, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Errors during concurrent writes: {errors}")
        # All 10 + 1 init should be persisted
        todo_board._cache = None
        async with await self._client() as client:
            lst = (await client.get("/api/todo")).json()
            self.assertEqual(len(lst), 11)

    # --- Rollback on write failure ---

    async def test_rollback_on_save_failure(self):
        """When save_json fails, memory must not retain the mutation."""
        async with await self._client() as client:
            # Create one valid task first
            resp = await client.post("/api/todo", json={"title": "正常任务"})
            self.assertEqual(resp.status_code, 200)

        # Verify exactly 1 task in cache and on disk
        todo_board._cache = None  # force reload from disk
        async with await self._client() as client:
            lst = (await client.get("/api/todo")).json()
            self.assertEqual(len(lst), 1)

        # Patch save_json to raise OSError on next call
        original_save_json = todo_board.save_json
        call_count = 0

        def failing_save_json(target, value):
            nonlocal call_count
            call_count += 1
            raise OSError("Simulated disk failure")

        with patch.object(todo_board, "save_json", side_effect=failing_save_json):
            async with await self._client() as client:
                resp = await client.post("/api/todo", json={"title": "写盘失败的任务"})
                self.assertEqual(resp.status_code, 500)

        # After failed write: memory must NOT contain the new task
        async with await self._client() as client:
            lst = (await client.get("/api/todo")).json()
            titles = [t["title"] for t in lst]
            self.assertNotIn("写盘失败的任务", titles)
            self.assertEqual(len(lst), 1)
            self.assertEqual(lst[0]["title"], "正常任务")

        # Disk must also still have only 1 task
        raw = json.loads(self.board_file.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["tasks"]), 1)
        self.assertEqual(raw["tasks"][0]["title"], "正常任务")

    # --- Persistence verification ---

    async def test_data_persisted_to_disk(self):
        async with await self._client() as client:
            await client.post("/api/todo", json={"title": "持久化测试"})
        # Read from disk directly
        raw = json.loads(self.board_file.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["tasks"]), 1)
        self.assertEqual(raw["tasks"][0]["title"], "持久化测试")
        self.assertEqual(raw["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
