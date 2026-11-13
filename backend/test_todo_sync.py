"""Tests for todo_sync and todo_board external_source support."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# We need to set up a temporary data directory before importing board modules
_test_tmp = tempfile.mkdtemp(prefix="todo_sync_test_")
_test_data_dir = Path(_test_tmp) / "data" / "todo"
_test_data_dir.mkdir(parents=True, exist_ok=True)

# Patch BOARD_FILE and DATA_DIR before importing todo_board
import backend.todo_board as tb
_original_board_file = tb.BOARD_FILE
_original_data_dir = tb.DATA_DIR
tb.BOARD_FILE = _test_data_dir / "board.json"
tb.DATA_DIR = _test_data_dir


def _reset_board():
    """Reset board to empty state for each test."""
    tb._cache = None
    if tb.BOARD_FILE.exists():
        tb.BOARD_FILE.unlink()


class TestStatusMapping(unittest.TestCase):
    """Test all six status mappings from workbench/group to kanban."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_queued_maps_to_todo(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-1", title="Test", status="queued")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "todo")

    def test_running_maps_to_doing(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-2", title="Test", status="running")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "doing")

    def test_error_maps_to_blocked(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-3", title="Test", status="error")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "blocked")

    def test_interrupted_maps_to_blocked(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-4", title="Test", status="interrupted")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "blocked")

    def test_complete_maps_to_done(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-5", title="Test", status="complete")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "done")

    def test_cancelled_archives(self):
        from backend.todo_sync import sync_task
        # First create an active card
        sync_task("workbench", "job-6", title="Test", status="queued")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0].get("archived", False))
        # Now cancel it
        sync_task("workbench", "job-6", title="Test", status="cancelled")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0].get("archived", False))


class TestIdempotentUpsert(unittest.TestCase):
    """Test that calling sync_task twice with same (platform, task_id) creates only one card."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_same_job_twice_creates_one_card(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "job-dup", title="First", status="queued")
        sync_task("workbench", "job-dup", title="Updated", status="running")
        tasks = tb._load_board()["tasks"]
        active = [t for t in tasks if not t.get("archived", False)]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["title"], "Updated")
        self.assertEqual(active[0]["status"], "doing")

    def test_different_platforms_create_separate_cards(self):
        from backend.todo_sync import sync_task
        sync_task("workbench", "same-id", title="WB", status="queued")
        sync_task("group", "same-id", title="Group", status="queued")
        tasks = tb._load_board()["tasks"]
        active = [t for t in tasks if not t.get("archived", False)]
        self.assertEqual(len(active), 2)


class TestSchemaV1ToV2Migration(unittest.TestCase):
    """Test backward compatibility: v1 board loads correctly with schema v2."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_v1_board_loads_and_migrates(self):
        # Write a v1-format board directly
        v1_data = {
            "schema_version": 1,
            "tasks": [
                {
                    "id": "aaa-bbb-ccc",
                    "title": "Old Task",
                    "description": "",
                    "status": "todo",
                    "priority": "medium",
                    "assignee": "",
                    "due_date": None,
                    "tags": [],
                    "project_id": None,
                    "position": 0,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "archived": False,
                }
            ],
        }
        tb.BOARD_FILE.write_text(json.dumps(v1_data), encoding="utf-8")
        tb._cache = None  # Force reload
        data = tb._load_board()
        self.assertEqual(data["schema_version"], 2)
        self.assertIsNone(data["tasks"][0].get("external_source"))
        # Verify the migrated data is cached
        self.assertEqual(tb._cache["schema_version"], 2)


class TestInvalidPlatformRejected(unittest.TestCase):
    """Test that invalid platform values are rejected."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_invalid_platform_in_external_source_field(self):
        from fastapi import HTTPException
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(
                title="Bad",
                external_source={"platform": "invalid_system", "task_id": "x"},
            )

    def test_valid_platforms_accepted(self):
        for platform in ["workbench", "group", "feishu"]:
            model = tb.TaskCreate(
                title="OK",
                external_source={"platform": platform, "task_id": "test-id"},
            )
            self.assertIsNotNone(model.external_source)


class TestSyncFailureDoesNotAffectTaskSave(unittest.TestCase):
    """Test that sync exceptions are swallowed and don't affect the source system."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_workbench_save_succeeds_when_sync_raises(self):
        """Patch todo_sync.sync_task to raise; assert JobManager.save still works."""
        import uuid as _uuid
        from backend.workbench import JobManager
        wb_dir = Path(_test_tmp) / "workbench_test"
        jm = JobManager(wb_dir)
        job_id = str(_uuid.uuid4())
        job = {
            "id": job_id,
            "status": "queued",
            "prompt": "Test prompt",
            "mode": "chat",
            "events": [],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        jm.jobs[job_id] = job
        # Patch sync_task to always raise
        with patch("backend.todo_sync.sync_task", side_effect=RuntimeError("sync boom")):
            # Should NOT raise
            jm.save(job)
        # Verify the job was persisted
        job_file = jm.directory / f"{job_id}.json"
        self.assertTrue(job_file.exists())

    def test_group_tasks_save_succeeds_when_sync_raises(self):
        """Patch todo_sync.sync_task to raise; assert TaskManager.save still works."""
        import uuid as _uuid
        from backend.group_tasks import TaskManager
        from backend.harness.store import RunStore
        store_dir = Path(_test_tmp) / "group_test"
        store_dir.mkdir(parents=True, exist_ok=True)
        store = RunStore(store_dir)
        tm = TaskManager.__new__(TaskManager)
        tm.directory = store_dir
        tm.store = store
        tm.records = {}
        item_id = str(_uuid.uuid4())
        item = {
            "id": item_id,
            "status": "queued",
            "title": "Test",
            "events": [],
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        tm.records[item_id] = item
        with patch("backend.todo_sync.sync_task", side_effect=RuntimeError("sync boom")):
            # Should NOT raise
            tm.save(item)


class TestExternalSourceInHttpEndpoints(unittest.TestCase):
    """Test that the HTTP API accepts/rejects external_source correctly."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_create_with_external_source(self):
        body = tb.TaskCreate(
            title="Synced Card",
            external_source={"platform": "feishu", "task_id": "fs-123"},
        )
        result = tb.create_task(body)
        self.assertIsNotNone(result["external_source"])
        self.assertEqual(result["external_source"]["platform"], "feishu")

    def test_extra_fields_still_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(title="Bad", bogus_field="nope")

    def test_external_source_extra_keys_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(
                title="Bad",
                external_source={"platform": "feishu", "task_id": "x", "extra": "nope"},
            )


class TestArchiveTask(unittest.TestCase):
    """Test archive_task function."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_archive_existing(self):
        from backend.todo_sync import sync_task, archive_task
        sync_task("group", "g-1", title="G1", status="queued")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len([t for t in tasks if not t.get("archived")]), 1)
        archive_task("group", "g-1")
        tasks = tb._load_board()["tasks"]
        self.assertEqual(len([t for t in tasks if not t.get("archived")]), 0)

    def test_archive_nonexistent_is_safe(self):
        from backend.todo_sync import archive_task
        # Should not raise
        archive_task("group", "nonexistent")


if __name__ == "__main__":
    unittest.main()
