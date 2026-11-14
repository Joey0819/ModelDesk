"""Platform reporting in the workbench status payload.

GET /api/workbench/status must expose the host platform so the frontend can
name the machine truthfully instead of assuming a Mac. The block reuses
backend/platform_support.py and costs no subprocess per request.
"""

import platform as _platform
import unittest
from unittest.mock import patch

from backend import workbench
from backend.platform_support import get_platform_info, is_linux, is_macos, is_windows, machine_label, machine_short


class StatusPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_payload_contains_platform_block_for_this_host(self):
        with patch.object(workbench, "subscription_catalog") as catalog, \
                patch.object(workbench, "connection_models", return_value=[]), \
                patch.object(workbench, "connection_store") as connections, \
                patch("backend.readiness.model_evidence", return_value={}), \
                patch("backend.media_providers.capabilities", new=self._no_capabilities):
            catalog.side_effect = RuntimeError("offline in test")
            connections.list.return_value = []
            payload = await workbench.status()

        self.assertIn("platform", payload)
        block = payload["platform"]
        self.assertEqual(block, get_platform_info())
        # Truthful per this host, not a Mac assumption.
        self.assertEqual(block["system"], _platform.system())
        self.assertEqual(block["is_macos"], _platform.system() == "Darwin")
        self.assertEqual(block["is_linux"], _platform.system() == "Linux")
        self.assertEqual(block["is_windows"], _platform.system() == "Windows")
        self.assertEqual(block["machine_label"], machine_label())
        if is_linux:
            self.assertTrue(block["is_linux"])
            self.assertEqual(block["machine_label"], "运行 ModelDesk 的这台电脑")
            self.assertEqual(block["machine_short"], "这台电脑")
        if is_macos:
            self.assertTrue(block["is_macos"])
            self.assertEqual(block["machine_label"], "这台 Mac")
            self.assertEqual(block["machine_short"], "Mac")
        if is_windows:
            self.assertTrue(block["is_windows"])

    @staticmethod
    async def _no_capabilities(catalog=None, connections=None):
        return []

    async def test_platform_labels_match_helpers_used_by_user_facing_strings(self):
        # autonomy.py / feishu.py / muse_bridge.py interpolate these helpers,
        # so the status payload and the backend copy can never disagree.
        block = get_platform_info()
        self.assertIn("machine_label", block)
        self.assertIn("machine_short", block)
        self.assertEqual(block["machine_label"], machine_label())
        self.assertEqual(block["machine_short"], machine_short())
        self.assertIn(machine_label(), ("运行 ModelDesk 的这台电脑", "这台 Mac"))
        self.assertIn(machine_short(), ("这台电脑", "Mac"))


if __name__ == "__main__":
    unittest.main()
