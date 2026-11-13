"""Tests for the Todo Kanban board API."""
import copy
import json
import tempfile
import threading
import unittest
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

    async def test_reorder_nonexistent_id_returns_422(self):
        async with await self._client() as client:
            t1 = (await client.post("/api/todo", json={"title": "A"})).json()
            resp = await client.post("/api/todo/reorder", json={
                "ids": [t1["id"], "nonexistent"],
                "status": "todo",
                "position_start": 0,
            })
            self.assertEqual(resp.status_code, 422)

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
