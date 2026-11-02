import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from backend.execution_store import WorkerOwnership, save_checkpoint


class CheckpointTests(unittest.TestCase):
    def test_failed_replace_preserves_previous_checkpoint_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            identifier = str(uuid.uuid4())
            save_checkpoint(directory, identifier, {"id": identifier, "status": "dispatched"})
            with patch("backend.execution_store.os.replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    save_checkpoint(directory, identifier, {"id": identifier, "status": "complete"})
            path = Path(directory) / (identifier + ".json")
            self.assertEqual(json.loads(path.read_text())["status"], "dispatched")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('.checkpoint-*')), [])

    def test_exclusive_owner_is_released_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            claim = WorkerOwnership(directory)
            claim.acquire()
            code = "from backend.execution_store import WorkerOwnership; import sys; WorkerOwnership(sys.argv[1]).acquire()"
            duplicate = subprocess.run([sys.executable, "-c", code, directory], capture_output=True)
            self.assertNotEqual(duplicate.returncode, 0)
            claim.release()
            fresh = subprocess.run([sys.executable, "-c", code, directory], capture_output=True)
            self.assertEqual(fresh.returncode, 0, fresh.stderr.decode())
            claim.acquire()
            claim.release()

    def test_symlink_and_nonfinite_values_cannot_replace_valid_record(self):
        with tempfile.TemporaryDirectory() as directory:
            identifier = str(uuid.uuid4())
            save_checkpoint(directory, identifier, {"status": "known"})
            with self.assertRaises(ValueError):
                save_checkpoint(directory, identifier, {"budget": float('nan')})
            path = Path(directory) / (identifier + '.json')
            self.assertEqual(json.loads(path.read_text())["status"], "known")
            other = Path(directory) / 'other'
            other.write_text('preserved')
            path.unlink(); path.symlink_to(other)
            with self.assertRaises(ValueError):
                save_checkpoint(directory, identifier, {})
            self.assertEqual(other.read_text(), 'preserved')


class SupervisorTests(unittest.TestCase):
    def test_budget_survives_restart_and_healthy_services_are_never_spawned(self):
        from scripts.modeldesk_worker import Supervisor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/services.py').write_text('# fixture')
            calls = []
            clock = [1000]
            def runner(args, **kwargs):
                calls.append(args[-1])
                return subprocess.CompletedProcess(args, 1)
            def probe(url):
                return ':8001/' not in url
            for _ in range(5):
                worker = Supervisor(root, root / 'state', runner=runner, probe=probe, clock=lambda: clock[0])
                worker.tick()
                clock[0] += 100
            self.assertEqual(calls, ['council-api'] * 3)
            self.assertEqual(worker.services['council-api']['state'], 'needs_attention')
            self.assertEqual(worker.services['gateway']['state'], 'ready')
