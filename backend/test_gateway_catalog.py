"""Local gateway refresh, including offline and externally managed installations."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import yaml

from backend import gateway_catalog as catalog
from backend import workbench


def directory(model):
    return json.dumps({provider: [{"id": model}] for provider in catalog.REQUIRED_PROVIDERS}).encode()


class GatewayCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(catalog, "WORKSPACE", self.workspace))
        binary = self.workspace / "runtime/cli-proxy-api"
        binary.parent.mkdir()
        binary.touch()
        self.config = self.workspace / ".private/config.yaml"
        self.config.parent.mkdir()
        self.original = b"# Keep this comment\r\nconfig-version: 8\r\nserver: {host: 127.0.0.1, port: 8317}\r\n"
        self.config.write_bytes(self.original)
        self.config.chmod(0o600)

    def test_atomic_rotation_preserves_configuration_and_previous_catalog(self):
        first, second = directory("one"), directory("two")
        self.assertTrue(catalog.install_catalog(self.config, first))
        new = self.config.read_bytes()
        self.assertTrue(new.startswith(self.original))
        self.assertNotIn(b"\n", new.replace(b"\r\n", b""))
        path_a = Path(yaml.safe_load(new)["models"]["catalog"])
        self.assertEqual(path_a.read_bytes(), first)
        self.assertFalse(catalog.install_catalog(self.config, first))
        self.assertEqual(self.config.read_bytes(), new)
        self.assertTrue(catalog.install_catalog(self.config, second))
        path_b = Path(yaml.safe_load(self.config.read_bytes())["models"]["catalog"])
        self.assertNotEqual(path_a, path_b)
        self.assertEqual((path_a.read_bytes(), path_b.read_bytes()), (first, second))
        for f in (self.config, path_a, path_b):
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)

    async def test_custom_source_and_unmanaged_gateway_are_left_to_the_operator(self):
        self.config.write_bytes(self.original + b"models: {catalog: /another-directory/models.json}\n")
        raw = self.config.read_bytes()
        with patch.object(catalog, "fetch_catalog", new_callable=AsyncMock) as fetch:
            self.assertTrue((await catalog.refresh_gateway_catalog())["skipped"])
            self.assertEqual(self.config.read_bytes(), raw)
            self.config.unlink()
            self.assertTrue((await catalog.refresh_gateway_catalog())["skipped"])
            fetch.assert_not_awaited()

    async def test_publisher_fallback_validates_complete_response_and_bounds_size(self):
        valid = directory("available")
        invalid = [b'{"codex-plus":[]}', b"{}", valid.replace(b'{"id": "available"}', b'{"id": ""}')]
        invalid += [json.dumps({**json.loads(valid), "gemini": [{"id": "a"}, {"id": "a"}]}).encode(), valid + b" " * 8]
        real_client = httpx.AsyncClient
        for rejected in invalid:
            with self.subTest(rejected=rejected[:30]):
                responses = iter([rejected, valid])
                calls = []
                def handler(req):
                    calls.append(str(req.url))
                    return httpx.Response(200, content=next(responses))
                def mocked(**kwargs):
                    return real_client(transport=httpx.MockTransport(handler), **kwargs)
                with patch.object(catalog.httpx, "AsyncClient", side_effect=mocked), patch.object(catalog, "MAX_BYTES", len(valid)):
                    self.assertEqual(await catalog.fetch_catalog(), valid)
                self.assertEqual(calls, list(catalog.SOURCES))

    async def test_refresh_waits_for_actual_gateway_keeps_cache_when_offline(self):
        read = AsyncMock(side_effect=[["old"], ["old"], ["entitled"], ["entitled"], ["entitled"]])
        fetch = AsyncMock(side_effect=[directory("entitled"), directory("entitled"), ValueError("offline")])
        with patch.object(workbench, "subscription_catalog", read), patch.object(catalog, "fetch_catalog", fetch), \
             patch.object(catalog.asyncio, "sleep", new_callable=AsyncMock):
            self.assertTrue((await catalog.refresh_gateway_catalog())["updated"])
            raw = self.config.read_bytes()
            self.assertFalse((await catalog.refresh_gateway_catalog())["updated"])
            self.assertTrue((await catalog.refresh_gateway_catalog())["stale"])
        self.assertEqual(read.await_count, 5)
        self.assertEqual(self.config.read_bytes(), raw)
        self.assertEqual(Path(yaml.safe_load(raw)["models"]["catalog"]).read_bytes(), directory("entitled"))

    async def test_unchanged_entitlement_never_reports_new_models(self):
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=["old"])), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(return_value=directory("new"))), \
             patch.object(catalog.asyncio, "sleep", new_callable=AsyncMock):
            self.assertFalse((await catalog.refresh_gateway_catalog())["updated"])
