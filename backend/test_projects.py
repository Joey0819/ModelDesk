"""Real temporary Git scopes, immutable snapshots, and evidence validation."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from backend.projects import ProjectStore, SnapshotRequest, checked_path
from backend.review import validated_findings, review_prompt


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.store = ProjectStore(self.base / "state")
        self.project = self.store.register("测试项目", str(self.root))

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *arguments):
        result = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-C", str(self.root), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return result.stdout.decode().strip()

    def initialize_git(self):
        self.git("init", "-q")
        (self.root / "a.py").write_text("def value():\n    return 1\n")
        (self.root / "deleted.py").write_text("deleted_source = True\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        return self.git("rev-parse", "HEAD")

    def test_working_staged_deleted_untracked_and_secret_scope(self):
        self.initialize_git()
        (self.root / "a.py").write_text("def value():\n    return 2\n")
        self.git("add", "a.py")
        (self.root / "a.py").write_text("def value():\n    return 3\n")
        (self.root / "deleted.py").unlink()
        (self.root / "new.py").write_text("new_source = True\n")
        (self.root / ".env").write_text("SECRET=never-export-this\n")
        working = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="working"))
        files = {item["path"]: item for item in working["files"]}
        self.assertIn("return 3", files["a.py"]["current"]["text"])
        self.assertIn("return 1", files["a.py"]["base"]["text"])
        self.assertIsNone(files["deleted.py"]["current"])
        self.assertIn("deleted_source", files["deleted.py"]["base"]["text"])
        self.assertIsNone(files["new.py"]["base"])
        self.assertIn(".env", [item["path"] for item in working["omitted"]])
        self.assertNotIn("never-export-this", json.dumps(working))
        staged = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="staged"))
        self.assertEqual([item["path"] for item in staged["files"]], ["a.py"])
        self.assertIn("return 2", staged["files"][0]["current"]["text"])
        self.assertNotIn("return 3", staged["files"][0]["current"]["text"])
        detail = self.store.detail(self.project["id"])
        self.assertTrue(detail["git"]["available"])
        self.assertIn("a.py", [item["path"] for item in detail["git"]["status"]])

    def test_last_commit_and_branch_exclude_uncommitted_data(self):
        original = self.initialize_git()
        branch = self.git("symbolic-ref", "--short", "HEAD")
        self.git("branch", "baseline", original)
        (self.root / "a.py").write_text("def value():\n    return 9\n")
        self.git("add", "a.py")
        self.git("commit", "-qm", "change")
        (self.root / "a.py").write_text("def value():\n    return 99\n")
        for request in [SnapshotRequest(scope="last_commit"), SnapshotRequest(scope="branch", base_ref="baseline")]:
            snapshot = self.store.build_snapshot(self.project["id"], request)
            self.assertIn("return 9", snapshot["files"][0]["current"]["text"])
            self.assertNotIn("return 99", snapshot["files"][0]["current"]["text"])
            self.assertIn("return 1", snapshot["files"][0]["base"]["text"])
        self.assertEqual(self.store.detail(self.project["id"])["git"]["branch"], branch)

    def test_merge_commit_uses_first_parent_changes_and_never_executes_filters(self):
        self.initialize_git()
        initial_branch = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-qb", "feature")
        (self.root / "feature.py").write_text("feature = True\n")
        self.git("add", "feature.py")
        self.git("commit", "-qm", "feature")
        self.git("checkout", "-q", initial_branch)
        self.git("merge", "--no-ff", "-qm", "merge", "feature")
        snapshot = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="last_commit"))
        self.assertEqual([item["path"] for item in snapshot["files"]], ["feature.py"])
        marker = self.base / "filter-executed"
        self.git("config", "filter.audit.clean", "touch " + str(marker))
        (self.root / ".gitattributes").write_text("*.py filter=audit\n")
        (self.root / "a.py").write_text("def value():\n    return 2\n")
        self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="working"))
        self.assertFalse(marker.exists())

    def test_unquoted_yaml_and_known_provider_credentials_are_excluded(self):
        samples = {"settings.yaml": "api_key: " + "AIzaSy" + "x" * 33, "password.yaml": "password: unquoted-audit-fixture", "github.txt": "ghp_" + "x" * 36, "aws.txt": "AKIA" + "A" * 16, "settings.json": '{"authorization":"Bearer audit-only-fake-value"}'}
        for name, content in samples.items():
            (self.root / name).write_text(content)
        snapshot = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="selected", files=list(samples)))
        self.assertEqual(snapshot["files"], [])
        self.assertEqual(len(snapshot["omitted"]), len(samples))
        for content in samples.values():
            self.assertNotIn(content, json.dumps(snapshot))

    def test_non_git_selected_and_symlink_path_secret_binary_exclusion(self):
        (self.root / "ok.py").write_text("print('safe')\n")
        outside = self.base / "outside.txt"
        outside.write_text("outside-private-value")
        (self.root / "escape.py").symlink_to(outside)
        (self.root / "binary.dat").write_bytes(b"abc\x00def")
        (self.root / "private.py").write_text('api_key = "' + 'sk-proj-' + 'abcdefghijklmnopqrstuv' + '"\n')
        snapshot = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="selected", files=["ok.py", "escape.py", "binary.dat", "private.py", "../outside.txt"]))
        self.assertEqual([item["path"] for item in snapshot["files"]], ["ok.py"])
        self.assertEqual(len(snapshot["omitted"]), 4)
        self.assertNotIn("outside-private-value", json.dumps(snapshot))
        self.assertNotIn("abcdefghijklmnopqrstuv", json.dumps(snapshot))
        with self.assertRaises(ValueError):
            checked_path(self.root, "../outside.txt")
        with self.assertRaises(ValueError):
            self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="staged"))

    def test_foreign_stale_tampered_and_durable_snapshots(self):
        self.initialize_git()
        (self.root / "a.py").write_text("def value():\n    return 2\n")
        snapshot = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="working"))
        self.assertEqual(self.store.snapshot(self.project["id"], snapshot["id"], True)["digest"], snapshot["digest"])
        restored = ProjectStore(self.store.directory)
        self.assertEqual(restored.get(self.project["id"])["path"], str(self.root.resolve()))
        self.assertEqual(restored.snapshot(self.project["id"], snapshot["id"])["digest"], snapshot["digest"])
        path = self.store.directory / "snapshots" / (snapshot["id"] + ".json")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        second_root = self.base / "second"
        second_root.mkdir()
        second = self.store.register("second", str(second_root))
        with self.assertRaises(HTTPException) as foreign:
            self.store.snapshot(second["id"], snapshot["id"])
        self.assertEqual(foreign.exception.status_code, 409)
        (self.root / "a.py").write_text("def value():\n    return 3\n")
        with self.assertRaises(HTTPException) as stale:
            self.store.snapshot(self.project["id"], snapshot["id"], True)
        self.assertEqual(stale.exception.status_code, 409)
        data = json.loads(path.read_text())
        data["files"][0]["current"]["text"] = "tampered"
        path.write_text(json.dumps(data))
        with self.assertRaises(HTTPException) as tampered:
            self.store.snapshot(self.project["id"], snapshot["id"])
        self.assertEqual(tampered.exception.status_code, 409)

    def test_truncation_explicit_and_findings_range_evidence_provenance(self):
        (self.root / "large.py").write_text("\n".join(f"line_{i} = {i}" for i in range(1, 1001)))
        snapshot = self.store.build_snapshot(self.project["id"], SnapshotRequest(scope="selected", files=["large.py"]))
        self.assertFalse(snapshot["coverage"]["complete"])
        self.assertTrue(snapshot["truncated"])
        valid = {"file": "large.py", "line_start": 2, "line_end": 2, "side": "current", "severity": "P2", "title": "test finding", "body": "trigger and impact", "evidence": "line_2 = 2", "suggestion": "fix it"}
        sources = [("actual-a", json.dumps({"findings": [valid, {**valid, "file": "../private"}, {**valid, "line_start": 999, "line_end": 999}, {**valid, "evidence": "invented code"}]})), ("actual-b", json.dumps({"findings": [valid]}))]
        findings, rejected = validated_findings(snapshot, sources)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["models"], ["actual-a", "actual-b"])
        self.assertEqual(len(rejected), 3)
        self.assertIn("UNTRUSTED_REPOSITORY_SNAPSHOT", review_prompt(snapshot, "review"))
        removed = self.store.remove(self.project["id"])
        self.assertFalse(removed["files_deleted"])
        self.assertTrue((self.root / "large.py").exists())


if __name__ == "__main__":
    unittest.main()
