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
        old, available = ["gemini-old"], ["gemini-current"]
        read = AsyncMock(side_effect=[old, old, available, available, available, available])
        data = directory("gemini-current")
        fetch = AsyncMock(side_effect=[data, data, ValueError("offline")])
        with patch.object(workbench, "subscription_catalog", read), patch.object(catalog, "fetch_catalog", fetch), \
             patch.object(catalog.asyncio, "sleep", new_callable=AsyncMock):
            result = await catalog.refresh_gateway_catalog("antigravity")
            self.assertTrue(result["updated"])
            self.assertEqual(result["providers"], {"antigravity": {"listed": available, "catalog_only": []}})
            raw = self.config.read_bytes()
            self.assertFalse((await catalog.refresh_gateway_catalog("antigravity"))["updated"])
            offline = await catalog.refresh_gateway_catalog("antigravity")
            self.assertEqual(offline["providers"], {"antigravity": {"listed": available, "catalog_only": []}})
            self.assertNotIn("stale", offline)
        self.assertEqual(read.await_count, 6)
        self.assertEqual(self.config.read_bytes(), raw)
        self.assertEqual(Path(yaml.safe_load(raw)["models"]["catalog"]).read_bytes(), data)

    async def test_first_refresh_without_cache_retains_the_live_registry(self):
        entries = [{"id": "gemini-account", "owned_by": "antigravity"}, {"id": "gpt-account", "owned_by": "openai"}]
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=entries)), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(side_effect=httpx.ConnectError("offline"))), \
             patch.object(catalog, "install_catalog") as install:
            result = await catalog.refresh_gateway_catalog()
        self.assertEqual(result["providers"], {
            "antigravity": {"listed": ["gemini-account"], "catalog_only": []},
            "codex": {"listed": ["gpt-account"], "catalog_only": []},
        })
        self.assertIn("暂无可用", result["publisher_warning"])
        self.assertNotIn("stale", result)
        self.assertNotIn("error", result)
        self.assertEqual(self.config.read_bytes(), self.original)
        install.assert_not_called()

    async def test_cold_scoped_refresh_uses_the_active_managed_cache_only_for_metadata(self):
        doc = json.loads(directory("gpt-publisher"))
        doc["antigravity"] = [{"id": "gemini-publisher"}, {"id": "gemini-account"}]
        doc["gemini"] = [{"id": "gemini-api-only"}]
        active_data = json.dumps(doc).encode()
        self.assertTrue(catalog.install_catalog(self.config, directory("old-publisher")))
        self.assertTrue(catalog.install_catalog(self.config, active_data))
        raw = self.config.read_bytes()
        source = Path(yaml.safe_load(raw)["models"]["catalog"])
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=["gemini-account", "gemini-actual-only"])), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(side_effect=ValueError("offline"))), \
             patch.object(catalog, "install_catalog") as install:
            result = await catalog.refresh_gateway_catalog("antigravity")
        self.assertEqual(result["providers"], {
            "antigravity": {"listed": ["gemini-account", "gemini-actual-only"], "catalog_only": ["gemini-publisher"]},
        })
        self.assertIn("已缓存", result["publisher_warning"])
        self.assertNotIn("stale", result)
        self.assertNotIn("error", result)
        self.assertEqual(self.config.read_bytes(), raw)
        self.assertEqual(source.read_bytes(), active_data)
        install.assert_not_called()

    async def test_publisher_failure_ignores_invalid_missing_and_linked_cache(self):
        good = directory("gemini-publisher")
        self.assertTrue(catalog.install_catalog(self.config, good))
        source = Path(yaml.safe_load(self.config.read_bytes())["models"]["catalog"])
        external = self.workspace / "external.json"
        external.write_bytes(good)
        for content in (None, b"{broken}", b'{"antigravity":[{"id":"invalid"}]}', good + b" ", external):
            with self.subTest(content=content):
                source.unlink(missing_ok=True)
                if isinstance(content, Path):
                    source.symlink_to(content)
                elif content is not None:
                    source.write_bytes(content)
                with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=["gemini-account"])), \
                     patch.object(catalog, "fetch_catalog", new=AsyncMock(side_effect=ValueError("offline"))), \
                     patch.object(catalog, "MAX_BYTES", len(good)):
                    result = await catalog.refresh_gateway_catalog("antigravity")
                self.assertEqual(result["providers"]["antigravity"], {"listed": ["gemini-account"], "catalog_only": []})
                self.assertIn("暂无可用", result["publisher_warning"])
                self.assertNotIn("stale", result)

    async def test_local_registry_failure_remains_stale(self):
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(side_effect=httpx.ConnectError("offline"))), \
             patch.object(catalog, "fetch_catalog", new_callable=AsyncMock) as fetch:
            result = await catalog.refresh_gateway_catalog("antigravity")
        self.assertTrue(result["stale"])
        self.assertIn("读取订阅网关失败", result["error"])
        self.assertNotIn("providers", result)
        self.assertNotIn("publisher_warning", result)
        fetch.assert_not_awaited()

    async def test_unchanged_entitlement_never_reports_new_models(self):
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(return_value=["gemini-old"])), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(return_value=directory("gemini-new"))), \
             patch.object(catalog.asyncio, "sleep", new_callable=AsyncMock):
            result = await catalog.refresh_gateway_catalog("antigravity")
            self.assertFalse(result["updated"])
            self.assertEqual(result["providers"]["antigravity"], {"listed": ["gemini-old"], "catalog_only": ["gemini-new"]})

    async def test_codex_registration_does_not_end_the_wait_for_google(self):
        doc = json.loads(directory("gpt-new"))
        doc["antigravity"] = [{"id": "gemini-account"}, {"id": "gemini-publisher-only"}]
        doc["gemini"] = [{"id": "gemini-api-only"}]
        google = {"id": "gemini-account", "owned_by": "antigravity"}
        api = {"id": "gemini-api-only", "owned_by": "google"}
        codex = {"id": "gpt-new", "owned_by": "openai"}
        read = AsyncMock(side_effect=[[api], [codex, api], [codex, api, google]])
        with patch.object(workbench, "subscription_catalog", read), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(return_value=json.dumps(doc).encode())), \
             patch.object(catalog.asyncio, "sleep", new_callable=AsyncMock):
            result = await catalog.refresh_gateway_catalog()
        self.assertEqual(read.await_count, 3)
        self.assertEqual(result["providers"]["antigravity"], {"listed": ["gemini-account"], "catalog_only": ["gemini-publisher-only"]})
        self.assertEqual(result["providers"]["codex"], {"listed": ["gpt-new"], "catalog_only": []})

    async def test_refresh_sees_account_change_even_when_public_directory_is_unchanged(self):
        data = directory("gemini-new")
        self.assertTrue(catalog.install_catalog(self.config, data))
        with patch.object(workbench, "subscription_catalog", new=AsyncMock(side_effect=[["gemini-old"], ["gemini-new"]])), \
             patch.object(catalog, "fetch_catalog", new=AsyncMock(return_value=data)):
            result = await catalog.refresh_gateway_catalog("antigravity")
        self.assertTrue(result["updated"])
        self.assertEqual(result["providers"]["antigravity"]["listed"], ["gemini-new"])
