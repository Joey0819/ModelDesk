import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from backend import antigravity_catalog as catalog
from backend.model_connections import refresh_catalog


class NativeCatalogTests(IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.binary = root / "agy"
        self.cache = root / "models.json"
        for mock in [patch.object(catalog, "CACHE", self.cache),
                     patch.object(catalog.antigravity_agent, "find_cli", return_value=self.binary)]:
            mock.start()
            self.addCleanup(mock.stop)

    def install_fake(self, body):
        self.binary.write_text("#!/bin/sh\n[ \"$1\" = models ] || exit 2\n" + body)
        self.binary.chmod(0o700)

    async def test_refresh_persists_exact_native_gemini_text_ids_and_preserves_evidence(self):
        self.install_fake("printf 'gemini-4-neon-high\\tGemini 4 Neon (High)\\ngemini-4-neon-high\\tDuplicate\\ngemini-x-image\\tImage\\nclaude-alt\\tOther family\\n'")
        with patch("backend.model_connections.refresh_gateway_catalog", side_effect=AssertionError("wrong source")):
            first = await refresh_catalog(catalog.PROVIDER_ID)
        pinned = catalog.DEFAULT_ID + ":gemini-4-neon-high"
        self.assertEqual(first["native_agent"]["listed"], [pinned])
        with patch.object(catalog, "list_cli_models", side_effect=AssertionError("a status read should not start agy")):
            default, named = catalog.native_catalog({pinned})
        self.assertEqual((default["id"], default["verified"], named["label"], named["verified"], named["group_only"]),
                         (catalog.DEFAULT_ID, False, "Gemini 4 Neon (High)", True, True))
        self.assertEqual(catalog.model_slug(pinned), "gemini-4-neon-high")
        for invalid in [None, "gemini-4-neon-high", catalog.DEFAULT_ID + ":--settings", catalog.DEFAULT_ID + ":gemini-a b"]:
            self.assertFalse(catalog.is_agent_id(invalid))
        self.install_fake("printf '%s' 'private diagnostic here' >&2; exit 1")
        failure = await catalog.refresh_native_catalog()
        self.assertTrue(failure["stale"])
        self.assertNotIn("private diagnostic", json.dumps(failure))
        self.assertEqual(failure["listed"], [pinned])
        self.assertEqual(failure["catalog_checked_at"], first["checked_at"])
        with patch.object(catalog.antigravity_agent, "find_cli", return_value=None):
            self.assertTrue(all(item["unavailable"] for item in catalog.native_catalog({pinned})))

    async def test_account_removals_and_unusable_output(self):
        self.install_fake("printf 'gemini-a\\tGemini A\\n'")
        await catalog.refresh_native_catalog()
        self.install_fake("printf 'google-other\\tAnother family\\n'")
        self.assertEqual((await catalog.refresh_native_catalog())["listed"], [])
        self.install_fake("printf '<login screen>\\n'")
        self.assertTrue((await catalog.refresh_native_catalog())["stale"])
        self.cache.write_bytes(b'{"garbage')
        self.assertEqual([row["id"] for row in catalog.native_catalog()], [catalog.DEFAULT_ID])
