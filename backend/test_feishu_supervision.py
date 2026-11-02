from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from backend.feishu_supervision import reserve


class SupervisionTests(unittest.TestCase):
    def test_restart_budget_survives_process_recreation_and_config_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); now=datetime.now(timezone.utc)
            self.assertTrue(reserve(path,'revision-one',instant=now)['allowed'])
            self.assertFalse(reserve(path,'revision-one',instant=now+timedelta(seconds=5))['allowed'])
            self.assertTrue(reserve(path,'revision-one',instant=now+timedelta(seconds=11))['allowed'])
            self.assertTrue(reserve(path,'revision-one',instant=now+timedelta(seconds=42))['allowed'])
            self.assertFalse(reserve(path,'revision-one',instant=now+timedelta(seconds=500))['allowed'])
            self.assertTrue(reserve(path,'revision-one',instant=now+timedelta(seconds=901))['allowed'])
            self.assertTrue(reserve(path,'revision-two',instant=now+timedelta(seconds=902))['allowed'])

    def test_corrupt_record_does_not_create_an_unbounded_restart_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'worker-supervision.json').write_text('not-json')
            with self.assertRaises(ValueError):
                reserve(path,'revision-one')
