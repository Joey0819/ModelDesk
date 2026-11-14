"""Platform reporting in the workbench status payload.

GET /api/workbench/status must expose the host platform so the frontend can
name the machine truthfully instead of assuming a Mac. The block reuses
backend/platform_support.py and costs no subprocess per request.
"""

import platform as _platform
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend import remote_access as remote, workbench
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

    async def test_public_platform_info_never_discloses_local_paths(self):
        # /remote/info answers before pairing. It must not leak where binaries
        # live on this host, while still giving the phone the labels it needs.
        from backend.platform_support import public_platform_info
        public = public_platform_info()
        self.assertNotIn("tailscale_path", public)
        for value in public.values():
            if isinstance(value, str):
                self.assertFalse(value.startswith("/"), f"local path leaked: {value}")
                self.assertNotIn("/usr/", value)
        # Labels stay available so remote screens can word themselves.
        self.assertEqual(public["machine_label"], machine_label())
        self.assertEqual(public["machine_short"], machine_short())
        self.assertEqual(public["is_macos"], is_macos)
        self.assertEqual(public["is_linux"], is_linux)
        self.assertEqual(public["has_native_muse_bridge"], is_macos)
        # The admin surface keeps the full block, including the path.
        self.assertIn("tailscale_path", get_platform_info())


    async def test_unpaired_remote_info_carries_no_local_path(self):
        # /remote/info answers before pairing (pairing_required: True). Hit the
        # real gateway app unauthenticated and inspect the actual wire payload:
        # no field may carry a filesystem path, while labels still arrive.
        origin = "https://modeldesk.example.ts.net:8443"
        with tempfile.TemporaryDirectory() as temp:
            store = remote.RemoteStore(temp)
            store.configure(origin)
            app = remote.create_gateway(store)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=origin,
                                         headers={"Host": "modeldesk.example.ts.net:8443"}) as client:
                response = await client.get("/remote/info")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["pairing_required"])
            self.assertIn("platform", payload)
            for value in payload["platform"].values():
                if isinstance(value, str):
                    self.assertFalse(value.startswith("/"), f"local path leaked: {value}")
                    self.assertNotIn("/usr/", value)
            self.assertNotIn("tailscale_path", payload["platform"])
            self.assertEqual(payload["platform"]["machine_label"], machine_label())


if __name__ == "__main__":
    unittest.main()
