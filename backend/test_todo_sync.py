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


class TestSyncKeyIsNotClientWritable(unittest.TestCase):
    """The (platform, task_id) sync key is owned by todo_sync, not by HTTP.

    Accepting it over HTTP let any local caller pre-claim a key (hijacking the
    card a real task would sync into) or rewrite one (detaching a live card so
    the next sync creates a duplicate). The models must refuse it outright.
    """

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_create_model_rejects_external_source(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(
                title="Bad",
                external_source={"platform": "feishu", "task_id": "x"},
            )

    def test_update_model_rejects_external_source(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskUpdate(external_source={"platform": "feishu", "task_id": "x"})

    def test_upsert_rejects_unknown_platform(self):
        with self.assertRaises(ValueError):
            tb.upsert_external_task(
                external_source={"platform": "invalid_system", "task_id": "x"},
                title="Bad", status="todo",
            )

    def test_upsert_accepts_each_supported_platform(self):
        for platform in ["workbench", "group", "feishu"]:
            task = tb.upsert_external_task(
                external_source={"platform": platform, "task_id": f"id-{platform}"},
                title="OK", status="todo",
            )
            self.assertEqual(task["external_source"]["platform"], platform)


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
        # A scratch-directory manager is isolated from the live board by design;
        # force the sync path on so this test still covers the swallow-on-error branch.
        jm._todo_sync_enabled = True
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
        # Scratch-directory managers skip syncing; force it on to test the guard.
        tm._todo_sync_enabled = True
        tm._todo_synced = {}
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

    def test_scratch_managers_do_not_touch_the_live_board(self):
        """Test fixtures must never leak onto the production kanban board."""
        import uuid as _uuid
        from backend.workbench import JobManager
        from backend.group_tasks import TaskManager
        from backend.harness.store import RunStore

        before = json.loads(tb.BOARD_FILE.read_text(encoding="utf-8"))["tasks"] if tb.BOARD_FILE.exists() else []

        jm = JobManager(Path(_test_tmp) / "isolation_wb")
        job = {"id": str(_uuid.uuid4()), "status": "queued", "prompt": "ISOLATION_FIXTURE",
               "mode": "chat", "events": [],
               "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}
        jm.jobs[job["id"]] = job
        jm.save(job)

        store_dir = Path(_test_tmp) / "isolation_group"
        store_dir.mkdir(parents=True, exist_ok=True)
        tm = TaskManager(store_dir)
        item = {"id": str(_uuid.uuid4()), "status": "queued", "title": "ISOLATION_FIXTURE",
                "events": [], "updated_at": "2026-01-01T00:00:00+00:00"}
        tm.records[item["id"]] = item
        tm.save(item)

        after = json.loads(tb.BOARD_FILE.read_text(encoding="utf-8"))["tasks"] if tb.BOARD_FILE.exists() else []
        titles = [t.get("title") for t in after]
        self.assertNotIn("ISOLATION_FIXTURE", titles)
        self.assertEqual(len(after), len(before))


class TestExternalSourceInHttpEndpoints(unittest.TestCase):
    """Test that the HTTP API accepts/rejects external_source correctly."""

    def setUp(self):
        _reset_board()

    def tearDown(self):
        _reset_board()

    def test_create_never_accepts_a_sync_key(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(
                title="Synced Card",
                external_source={"platform": "feishu", "task_id": "fs-123"},
            )
        created = tb.create_task(tb.TaskCreate(title="Plain Card"))
        self.assertIsNone(created.get("external_source"))

    def test_extra_fields_still_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            tb.TaskCreate(title="Bad", bogus_field="nope")

    def test_sync_card_is_still_created_by_the_sync_path(self):
        # The board must keep showing mirrored cards even though HTTP cannot
        # create them: the sync entry point is the only writer.
        task = tb.upsert_external_task(
            external_source={"platform": "feishu", "task_id": "fs-123"},
            title="Synced Card", status="doing",
        )
        self.assertEqual(task["external_source"], {"platform": "feishu", "task_id": "fs-123"})
        self.assertEqual(task["status"], "doing")


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
