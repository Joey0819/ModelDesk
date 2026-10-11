"""Check tool output boundaries and concurrent native framework events."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import time
import unittest

from backend.workers.workflow import arithmetic, emit


class YieldingOutput(io.StringIO):
    def write(self, value):
        result = super().write(value)
        time.sleep(0.001)  # Let competing framework tool threads reach emit().
        return result


class WorkerToolsTests(unittest.TestCase):
    def test_nonfinite_arithmetic_never_emits_an_invalid_json_event(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(ValueError):
                arithmetic(1e100, 1e-300, "divide")
        self.assertEqual(output.getvalue(), "")

    def test_concurrent_tool_events_each_remain_one_valid_json_line(self):
        output = YieldingOutput()
        with redirect_stdout(output), ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda number: emit("tool_result", output=number), range(24)))
        events = [json.loads(line.removeprefix("MODELDESK_EVENT ")) for line in output.getvalue().splitlines()]
        self.assertEqual(sorted(event["output"] for event in events), list(range(24)))


if __name__ == "__main__":
    unittest.main()
