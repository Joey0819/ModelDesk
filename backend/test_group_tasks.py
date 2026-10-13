"""Real Git snapshots, durable subprocess lifecycle, scope and CLI boundaries."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import httpx
import psutil
from fastapi import FastAPI, HTTPException

from backend import group_tasks as tasks, groups, projects
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore


CAPABILITIES = {"engines": [{"id": engine, "label": engine, "available": True, "authenticated": True,
                            "isolation": "test_process"} for engine in ["codex", "claude"]]}


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "original"
        self.repo.mkdir()
        tasks.local_git(self.repo, "init", "-q", "--template=", "--initial-branch=main")
        (self.repo / "hello.py").write_text('print("hello")\n')
        tasks.local_git(self.repo, "add", ".")
        tasks.local_git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "initial", "--no-gpg-sign")
        self.projects = projects.ProjectStore(self.root / "projects")
        self.project = self.projects.register("test", str(self.repo), instructions="Give explicit test evidence")
        self.rooms = groups.RoomStore(self.root / "rooms")
        self.teams = TeamStore(self.root / "teams")
        self.patches = [patch.object(projects, "store", self.projects), patch.object(groups, "store", self.rooms),
                        patch.object(tasks, "engine_status", AsyncMock(return_value=CAPABILITIES))]
        for p in self.patches:
            p.start()
        self.team = self.teams.put(TeamRequest(name="test", project_id=self.project["id"], members=[
            {"id": "writer", "name": "Writer", "model": "gpt-test", "role": "implement"},
            {"id": "reviewer", "name": "Reviewer", "model": "claude-test", "role": "review"}]))
        self.room = self.rooms.create(groups.RoomRequest(title="test", project_id=self.project["id"], team_id=self.team["id"]), self.teams)
        self.manager = tasks.TaskManager(self.root / "tasks")

    async def asyncTearDown(self):
        await self.manager.shutdown()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def request(self, **changes):
        return tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="writer", engine="codex", prompt="Implement greeting", **changes)

    async def run_task(self, request=None, script=None):
        script = script or '''import pathlib, json
pathlib.Path('hello.py').write_text('print("changed")\\n')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Changed greeting; no tests run.'}}))
print(json.dumps({'type':'turn.completed'}))
'''
        with patch.object(tasks, "worker_command", return_value=[sys.executable, "-c", script]):
            item = await self.manager.create(request or self.request())
            await self.manager.tasks[item["id"]]
        return self.manager.get(item["id"], self.project["id"], self.room["id"])

    async def test_real_subprocess_frozen_patch_leaves_source_clean(self):
        item = await self.run_task()
        self.assertEqual(item["status"], "complete", item["error"])
        self.assertEqual(item["changed_files"], [{"path": "hello.py", "status": "modified"}])
        self.assertEqual(item["patch_sha256"], item["diff_sha256"])
        self.assertIn('+print("changed")', self.manager.read_patch(item["id"], self.project["id"]))
        self.assertEqual((self.repo / "hello.py").read_text(), 'print("hello")\n')
        self.assertFalse(tasks.local_git(self.repo, "status", "--porcelain"))
        self.assertIn("Give explicit test evidence", item["project_context"])
        workspace = self.manager.task_directory(item["id"]) / "repo"
        self.assertFalse(tasks.local_git(workspace, "remote"))
        self.assertNotEqual(tasks.local_git(workspace, "rev-parse", "HEAD").strip().decode(), item["base_sha"])
        self.assertEqual((self.manager.directory / (item["id"] + ".json")).stat().st_mode & 0o777, 0o600)

    async def test_review_exact_patch_distinct_member_and_readonly_detection(self):
        source = await self.run_task()
        request = tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="reviewer", engine="codex", kind="review", source_task_id=source["id"], prompt="Check defects")
        with self.assertRaises(HTTPException) as error:
            await self.manager.create(request.model_copy(update={"member_id": "writer"}))
        self.assertEqual(error.exception.status_code, 422)
        script = "import json, pathlib; assert 'changed' in pathlib.Path('hello.py').read_text(); print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'PASS'}}))"
        review = await self.run_task(request, script)
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(review["review_of_patch_sha256"], source["diff_sha256"])
        failed = await self.run_task(request)
        # Default fake script rewrites identical content; force a real alteration.
        changed = await self.run_task(request, script.replace("print(json.dumps", "pathlib.Path('new.txt').write_text('change'); print(json.dumps"))
        self.assertEqual(failed["status"], "complete")
        self.assertEqual(changed["status"], "error")
        self.assertIn("只读", changed["error"])

    async def test_saved_member_model_reviews_exact_patch_without_cli(self):
        source = await self.run_task()
        request = tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="reviewer", engine="member_model", kind="review", source_task_id=source["id"], prompt="Review independently")
        capability = {"engines": [{"id": "member_model", "label": "成员模型", "available": True, "authenticated": True, "isolation": "no_tools_readonly_model", "models": ["claude-test"]}]}
        captured = []
        async def handle(http_request):
            captured.append(json.loads(http_request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "PASS. The change is straightforward; tests were not run."}, "finish_reason": "stop"}]})
        original_client = httpx.AsyncClient
        with patch.object(tasks, "engine_status", AsyncMock(return_value=capability)), patch.object(tasks, "worker_command") as cli, patch.object(tasks.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs)):
            review = await self.manager.create(request)
            await self.manager.tasks[review["id"]]
        result = self.manager.get(review["id"], self.project["id"])
        self.assertEqual(result["status"], "complete", result["error"])
        self.assertEqual(result["engine_model"], "claude-test")
        self.assertEqual(captured[0]["model"], "claude-test")
        self.assertIn(source["diff_sha256"], captured[0]["messages"][1]["content"])
        self.assertIn('print(\\"changed\\")', captured[0]["messages"][1]["content"])
        self.assertNotIn("tools", captured[0])
        cli.assert_not_called()
        self.assertEqual(result["review_of_patch_sha256"], source["diff_sha256"])

    async def test_member_review_rejects_truncated_response(self):
        source = await self.run_task()
        request = tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="reviewer", engine="member_model", kind="review", source_task_id=source["id"], prompt="Review")
        capability = {"engines": [{"id": "member_model", "label": "成员模型", "available": True, "authenticated": True, "isolation": "no_tools_readonly_model", "models": ["claude-test"]}]}
        original_client = httpx.AsyncClient
        async def handle(_):
            return httpx.Response(200, json={"choices": [{"message": {"content": "PASS"}, "finish_reason": "length"}]})
        with patch.object(tasks, "engine_status", AsyncMock(return_value=capability)), patch.object(tasks.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs)):
            review = await self.manager.create(request)
            await self.manager.tasks[review["id"]]
        result = self.manager.get(review["id"], self.project["id"])
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["output"])

    async def test_dirty_and_non_root_projects_are_rejected(self):
        (self.repo / "unsaved.txt").write_text("user work")
        with self.assertRaises(HTTPException) as error:
            await self.manager.create(self.request())
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual((self.repo / "unsaved.txt").read_text(), "user work")
        sub = self.repo / "sub"
        sub.mkdir()
        with self.assertRaises(HTTPException):
            tasks.source_repository({"path": str(sub)})

    async def test_python_test_cache_is_omitted_from_frozen_patch(self):
        script = "import pathlib, runpy, json; pathlib.Path('new.py').write_text('VALUE = 42\\n'); import new; print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Imported new module'}}))"
        item = await self.run_task(script=script)
        self.assertEqual(item["status"], "complete", item["error"])
        self.assertEqual(item["changed_files"], [{"path": "new.py", "status": "added"}])
        self.assertNotIn("__pycache__", item["diff"])

    async def test_cross_project_and_room_scope(self):
        item = await self.run_task()
        with self.assertRaises(HTTPException) as error:
            self.manager.get(item["id"], str(uuid.uuid4()), self.room["id"])
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            self.manager.get(item["id"], self.project["id"], str(uuid.uuid4()))
        with self.assertRaises(HTTPException):
            await self.manager.create(self.request().model_copy(update={"member_id": "imposter"}))

    async def test_tampered_patch_and_baseline_cannot_be_reviewed(self):
        item = await self.run_task()
        directory = self.manager.task_directory(item["id"])
        (directory / "changes.patch").write_text("tampered")
        with self.assertRaises(HTTPException):
            self.manager.read_patch(item["id"], self.project["id"])
        (directory / "changes.patch").write_text(item["diff"])
        (directory / "baseline/hello.py").write_text("different")
        request = tasks.TaskRequest(project_id=self.project["id"], room_id=self.room["id"], member_id="reviewer", engine="codex", kind="review", source_task_id=item["id"], prompt="review")
        review = await self.run_task(request)
        self.assertEqual(review["status"], "error")
        self.assertIn("快照已变化", review["error"])

    async def test_symlink_and_binary_changes_do_not_create_mergeable_patch(self):
        scripts = ["import pathlib; pathlib.Path('escape').symlink_to('/etc/hosts')", "import pathlib; pathlib.Path('binary').write_bytes(b'\\0abc')"]
        suffix = "; import json; print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}))"
        for script in scripts:
            item = await self.run_task(script=script + suffix)
            self.assertEqual(item["status"], "error")
            self.assertIsNone(item["patch_sha256"])

    async def test_restart_marks_active_interrupted_without_replay(self):
        item = await self.run_task()
        self.manager.records[item["id"]]["status"] = "running"
        self.manager.save(self.manager.records[item["id"]])
        reloaded = tasks.TaskManager(self.manager.directory)
        self.assertEqual(reloaded.get(item["id"], self.project["id"])["status"], "interrupted")
        self.assertFalse(reloaded.tasks)
        self.assertEqual(len([e for e in reloaded.records[item["id"]]["events"] if e["type"] == "running"]), 1)

    async def test_cancel_terminates_real_process(self):
        script = "import time; time.sleep(60)"
        with patch.object(tasks, "worker_command", return_value=[sys.executable, "-c", script]):
            item = await self.manager.create(self.request())
            for _ in range(100):
                if item["id"] in self.manager.processes:
                    break
                await asyncio.sleep(.02)
            process = self.manager.processes[item["id"]]
            cancelled = await self.manager.cancel(item["id"], self.project["id"], self.room["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(process.pid, 0)

    async def test_cancel_terminates_detached_tool_child(self):
        script = '''import subprocess, sys, pathlib, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path('child.pid').write_text(str(child.pid))
time.sleep(60)
'''
        child = None
        try:
            with patch.object(tasks, "worker_command", return_value=[sys.executable, "-c", script]):
                item = await self.manager.create(self.request())
                pidfile = self.manager.task_directory(item["id"]) / "repo/child.pid"
                for _ in range(200):
                    if pidfile.exists():
                        break
                    await asyncio.sleep(.02)
                child = psutil.Process(int(pidfile.read_text()))
                process = self.manager.processes[item["id"]]
                self.assertNotEqual(os.getpgid(child.pid), os.getpgid(process.pid))
                cancelled = await self.manager.cancel(item["id"], self.project["id"], self.room["id"])
            self.assertEqual(cancelled["status"], "cancelled", cancelled["error"])
            self.assertIsNotNone(process.returncode)
            self.assertFalse(tasks.process_alive(child))
            self.assertFalse(self.manager.process_monitors)
        finally:
            if child and tasks.process_alive(child):
                child.kill()

    async def test_completed_cli_cleans_reparented_tool_before_freezing(self):
        script = '''import subprocess, sys, pathlib, time, json
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path('child.pid').write_text(str(child.pid))
time.sleep(.5)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Finished; no tests run'}}))
'''
        child = None
        try:
            item = await self.run_task(script=script)
            self.assertEqual(item["status"], "complete", item["error"])
            pidfile = self.manager.task_directory(item["id"]) / "repo/child.pid"
            try:
                child = psutil.Process(int(pidfile.read_text()))
            except psutil.NoSuchProcess:
                pass
            self.assertTrue(child is None or not tasks.process_alive(child))
        finally:
            if child and tasks.process_alive(child):
                child.kill()

    async def test_http_mutations_reject_foreign_origin(self):
        app = FastAPI()
        app.include_router(tasks.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url="http://local") as client:
            with patch.object(tasks, "manager", self.manager):
                for headers in [{}, {"Origin": "https://attacker.invalid"}, {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"}]:
                    response = await client.post("/api/group-tasks", json=self.request().model_dump(mode="json"), headers=headers)
                    self.assertEqual(response.status_code, 403)
        self.assertFalse(self.manager.records)

    def test_command_and_environment_are_closed(self):
        with patch.object(tasks.shutil, "which", side_effect=lambda name: "/usr/local/bin/" + name):
            codex = tasks.worker_command("codex", "code", self.repo)
        self.assertIn("--ignore-user-config", codex)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex)
        profile = next(value for value in codex if value.startswith("permissions.modeldesk="))
        self.assertIn('\":root\" = \"deny\"', profile)
        self.assertIn("enabled=false", profile)
        self.assertIn('\".git\" = \"read\"', profile)
        with patch.object(tasks.shutil, "which", side_effect=lambda name: "/usr/local/bin/" + name):
            claude = tasks.worker_command("claude", "review", self.repo)
        self.assertIn("--restricted", claude)
        self.assertIn("--safe-mode", claude)
        self.assertEqual(claude[claude.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("Bash", str(claude))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "private", "GITHUB_TOKEN": "private", "NODE_OPTIONS": "injection"}):
            env = tasks.worker_environment()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("NODE_OPTIONS", env)


if __name__ == "__main__":
    unittest.main()
