"""Exercise real Git merges, stale approvals, isolation and exact review binding."""
import hashlib
import asyncio
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import FastAPI
import httpx
from backend import group_approvals as approvals, projects


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve()
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@localhost")
        (self.repo / "hello.py").write_text("def hello():\n    return 'before'\n")
        self.git("add", "hello.py")
        self.git("commit", "-m", "Initial")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "hello.py").write_text("def hello():\n    return 'after'\n")
        self.patch_text = self.git("diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD")
        self.git("restore", "hello.py")
        self.projects = projects.ProjectStore(self.directory / "projects")
        self.project = self.projects.register("Demo", str(self.repo))
        self.room = str(uuid.uuid4())
        self.task = {"id": str(uuid.uuid4()), "project_id": self.project["id"], "room_id": self.room,
                     "member_id": "coder", "kind": "code", "status": "complete", "base_sha": self.base,
                     "patch_sha256": hashlib.sha256(self.patch_text.encode()).hexdigest(),
                     "changed_files": ["hello.py"], "prompt": "Make a small edit", "output": "Edited hello"}
        self.review = {**self.task, "id": str(uuid.uuid4()), "member_id": "reviewer", "kind": "review",
                       "source_task_id": self.task["id"], "review_of_patch_sha256": self.task["patch_sha256"],
                       "output": "Checked the exact change; return value changed."}
        self.tasks = {self.task["id"]: self.task, self.review["id"]: self.review}

        def get(identifier, project_id, room_id):
            task = self.tasks[identifier]
            if task["project_id"] != project_id or task["room_id"] != room_id:
                raise HTTPException(403, "wrong scope")
            return task

        self.worker = SimpleNamespace(get=get, read_patch=lambda *args: self.patch_text)
        self.patches = [patch.object(projects, "store", self.projects),
                        patch.object(approvals, "task_manager", return_value=self.worker),
                        patch.object(approvals.groups.store, "get", return_value={"id": self.room})]
        for mocked in self.patches:
            mocked.start()
        self.manager = approvals.ApprovalManager(self.directory / "approvals")
        self.request = approvals.ApprovalRequest(project_id=self.project["id"], room_id=self.room,
                                                 task_id=self.task["id"], review_task_id=self.review["id"])

    def tearDown(self):
        for mocked in self.patches:
            mocked.stop()
        self.temporary.cleanup()

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                                capture_output=True, text=True, check=True)
        return result.stdout

    def decision(self, card, **changes):
        return approvals.DecisionRequest(project_id=card["project_id"], room_id=card["room_id"],
                                          decision="approve", expected_base_sha=card["base_sha"],
                                          expected_patch_sha256=card["patch_sha256"], **changes)

    def test_actual_merge_is_exact_and_idempotent_and_does_not_push(self):
        card = self.manager.create(self.request)
        self.assertEqual(self.manager.create(self.request)["id"], card["id"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)
        result = self.manager.decide(card["id"], self.decision(card))
        self.assertEqual(result["status"], "merged")
        self.assertFalse(result["pushed"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), result["merge_commit_sha"])
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), self.base)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.repo / "hello.py").read_text(), "def hello():\n    return 'after'\n")
        restarted = approvals.ApprovalManager(self.directory / "approvals")
        self.assertEqual(restarted.decide(card["id"], self.decision(card))["merge_commit_sha"], result["merge_commit_sha"])
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "2")

    def test_other_member_and_exact_completed_review_are_required(self):
        for key, value in [("member_id", "coder"), ("status", "running"), ("source_task_id", str(uuid.uuid4())),
                           ("review_of_patch_sha256", "a" * 64), ("output", "")]:
            original = self.review[key]
            self.review[key] = value
            with self.assertRaises(HTTPException):
                self.manager.create(self.request)
            self.review[key] = original
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)

    def test_stale_base_and_branch_cannot_merge(self):
        card = self.manager.create(self.request)
        self.git("checkout", "-b", "other")
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))
        self.git("checkout", "main")
        self.git("commit", "--allow-empty", "-m", "New unrelated work")
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))
        self.assertEqual((self.repo / "hello.py").read_text(), "def hello():\n    return 'before'\n")

    def test_uncommitted_work_and_index_are_preserved(self):
        card = self.manager.create(self.request)
        (self.repo / "notes.txt").write_text("user work")
        self.git("add", "notes.txt")
        before = self.git("diff", "--cached")
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))
        self.assertEqual(self.git("diff", "--cached"), before)
        self.assertEqual((self.repo / "notes.txt").read_text(), "user work")

    def test_reject_has_no_filesystem_side_effects(self):
        card = self.manager.create(self.request)
        request = self.decision(card).model_copy(update={"decision": "reject"})
        self.assertEqual(self.manager.decide(card["id"], request)["status"], "rejected")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))

    def test_tampered_patch_and_cross_project_blocked(self):
        card = self.manager.create(self.request)
        self.patch_text += "\n"
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))
        request = self.decision(card).model_copy(update={"project_id": uuid.uuid4()})
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], request)

    def test_no_hooks_execute_and_symlink_patch_is_rejected(self):
        sentinel = self.directory / "hook-executed"
        hook = self.repo / ".git/hooks/post-merge"
        hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
        hook.chmod(0o755)
        card = self.manager.create(self.request)
        self.manager.decide(card["id"], self.decision(card))
        self.assertFalse(sentinel.exists())
        unsafe = "diff --git a/escape b/escape\nnew file mode 120000\nindex 0000000..983dcb6\n--- /dev/null\n+++ b/escape\n@@ -0,0 +1 @@\n+/tmp/secret\n\\ No newline at end of file\n"
        with self.assertRaises(HTTPException):
            self.manager.candidate(self.repo, {**card, "base_sha": self.git("rev-parse", "HEAD").strip()}, unsafe)

    def test_inflight_merge_is_not_replayed(self):
        card = self.manager.create(self.request)
        card["status"] = "merging"
        self.manager.save(card)
        restarted = approvals.ApprovalManager(self.directory / "approvals")
        with self.assertRaises(HTTPException):
            restarted.decide(card["id"], self.decision(card))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)

    def test_ignored_user_file_is_never_overwritten_by_new_agent_file(self):
        (self.repo / ".gitignore").write_text("notes.txt\n")
        self.git("add", ".gitignore")
        self.git("commit", "-m", "Ignore local notes")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "notes.txt").write_text("agent draft\n")
        self.git("add", "-f", "notes.txt")
        self.patch_text = self.git("diff", "--cached", "--binary", "HEAD")
        self.git("reset", "HEAD", "notes.txt")
        (self.repo / "notes.txt").write_text("precious user notes\n")
        self.task.update(base_sha=self.base, patch_sha256=hashlib.sha256(self.patch_text.encode()).hexdigest(), changed_files=["notes.txt"])
        self.review.update(base_sha=self.base, review_of_patch_sha256=self.task["patch_sha256"])
        card = self.manager.create(self.request)
        with self.assertRaises(HTTPException):
            self.manager.decide(card["id"], self.decision(card))
        self.assertEqual((self.repo / "notes.txt").read_text(), "precious user notes\n")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)

    def test_http_mutations_require_local_origin_and_exact_scope(self):
        async def run():
            app = FastAPI()
            app.include_router(approvals.router)
            with patch.object(approvals, "manager", self.manager):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 6000)), base_url="http://127.0.0.1:8001") as client:
                    body = self.request.model_dump(mode="json")
                    for headers in [{}, {"Origin": "https://malicious.example"}, {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"}]:
                        result = await client.post("/api/group-approvals", json=body, headers=headers)
                        self.assertEqual(result.status_code, 403)
                    result = await client.post("/api/group-approvals", json=body, headers={"Origin": "http://localhost:5173"})
                    self.assertEqual(result.status_code, 201)
                    card = result.json()
                    decision = self.decision(card).model_copy(update={"project_id": uuid.uuid4()}).model_dump(mode="json")
                    result = await client.post(f"/api/group-approvals/{card['id']}/decision", json=decision, headers={"Origin": "http://localhost:5173"})
                    self.assertEqual(result.status_code, 403)
        asyncio.run(run())
