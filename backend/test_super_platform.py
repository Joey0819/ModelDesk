import asyncio
import ipaddress
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from fastapi import HTTPException

from backend.platform_store import PlatformStore, chunks
from backend import super_platform as platform
from backend.workbench import JobRequest


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlatformStore(Path(self.temp.name) / "resources.json")
        self.patch = patch.object(platform, "store", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def test_fake_dns_uses_checked_public_addresses_without_accepting_private_targets(self):
        fake = [(2, 1, 6, "", ("198.18.0.42", 443))]
        lookup = AsyncMock(return_value=[ipaddress.ip_address("93.184.216.34")])
        with patch.object(platform.socket, "getaddrinfo", return_value=fake), patch.object(platform, "doh_addresses", lookup):
            _, host, addresses = await platform.public_target("https://example.com/path")
            self.assertEqual(host, "example.com")
            self.assertEqual(str(addresses[0]), "93.184.216.34")
            lookup.assert_awaited_once_with("example.com")
            for target in ["http://198.18.0.42", "http://localhost", "http://host.local"]:
                with self.assertRaises(HTTPException):
                    await platform.public_target(target)
            self.assertEqual(lookup.await_count, 1)
        for answer in [[(2, 1, 6, "", ("10.0.0.1", 443))], fake + [(2, 1, 6, "", ("127.0.0.1", 443))]]:
            with patch.object(platform.socket, "getaddrinfo", return_value=answer), patch.object(platform, "doh_addresses", lookup):
                with self.assertRaises(HTTPException):
                    await platform.public_target("https://example.com/")
                self.assertEqual(lookup.await_count, 1)
        lookup.return_value = [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("::1")]
        with patch.object(platform.socket, "getaddrinfo", return_value=fake), patch.object(platform, "doh_addresses", lookup):
            with self.assertRaises(HTTPException):
                await platform.public_target("https://example.com/")

    async def test_dns_bootstrap_uses_fixed_verified_endpoint_and_checks_query(self):
        captured = []
        def handle(request):
            captured.append(request)
            record = 1 if request.url.params["type"] == "A" else 28
            answer = "93.184.216.34" if record == 1 else "2606:2800:220:1:248:1893:25c8:1946"
            return httpx.Response(200, json={"Status": 0, "TC": False,
                "Question": [{"name": "example.com.", "type": record}], "Answer": [{"type": record, "data": answer}]})
        real_client = httpx.AsyncClient
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            self.assertNotEqual(kwargs.get("verify"), False)
            return real_client(transport=httpx.MockTransport(handle), **kwargs)
        with patch.object(platform.httpx, "AsyncClient", side_effect=client):
            addresses = await platform.doh_addresses("example.com")
        self.assertEqual(len(addresses), 2)
        self.assertTrue(all(address.is_global for address in addresses))
        self.assertTrue(all(request.url.host == "1.1.1.1" and request.headers["host"] == "cloudflare-dns.com" and request.extensions["sni_hostname"] == "cloudflare-dns.com" for request in captured))
        bad = httpx.MockTransport(lambda request: httpx.Response(200, json={"Status": 0, "Question": [{"name": "other.example", "type": 1}], "Answer": [{"type": 1, "data": "93.184.216.34"}]}))
        with patch.object(platform.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=bad, **kwargs)):
            with self.assertRaises(ValueError):
                await platform.doh_addresses("example.com")

    async def test_fetch_pins_ip_with_host_and_sni_and_rejects_private_redirect(self):
        captured = []
        def handle(request):
            captured.append(request)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        real_client = httpx.AsyncClient
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertNotEqual(kwargs.get("verify"), False)
            return real_client(transport=httpx.MockTransport(handle), **kwargs)
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch.object(platform.socket, "getaddrinfo", return_value=public), patch.object(platform.httpx, "AsyncClient", side_effect=client):
            with self.assertRaises(HTTPException):
                await platform.web_fetch("https://example.com/original")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].url.host, "93.184.216.34")
        self.assertEqual(captured[0].headers["Host"], "example.com")
        self.assertEqual(captured[0].extensions["sni_hostname"], "example.com")

    async def test_multicast_and_reserved_nat64_targets_are_never_public_destinations(self):
        for value in ["http://224.0.0.1", "http://[ff02::1]", "http://[64:ff9b::7f00:1]"]:
            with self.assertRaises(HTTPException):
                await platform.public_target(value)
        fake = [(2, 1, 6, "", ("198.18.0.42", 443))]
        for address in ["224.0.0.1", "ff02::1", "64:ff9b::7f00:1"]:
            with patch.object(platform.socket, "getaddrinfo", return_value=fake), patch.object(platform, "doh_addresses", AsyncMock(return_value=[ipaddress.ip_address(address)])):
                with self.assertRaises(HTTPException):
                    await platform.public_target("https://example.com/")

    async def test_dns_service_timeout_is_distinct_from_rejected_private_url(self):
        fake = [(2, 1, 6, "", ("198.18.0.42", 443))]
        with patch.object(platform.socket, "getaddrinfo", return_value=fake), patch.object(platform, "doh_addresses", AsyncMock(side_effect=httpx.ReadTimeout("fixture"))):
            with self.assertRaises(HTTPException) as caught:
                await platform.public_target("https://example.com/")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("未验证地址", caught.exception.detail)

    async def test_recoverable_resources_persist_without_overwriting_others(self):
        one = self.store.put("prompts", {"name": "科研评审", "content": "核对证据与结论", "tags": ["科研"]})
        two = self.store.put("prompts", {"name": "代码审查", "content": "检查空输入"})
        self.store.archive("prompts", one["id"])
        self.assertEqual([item["id"] for item in self.store.list("prompts")], [two["id"]])
        self.store.archive("prompts", one["id"], False)
        reloaded = PlatformStore(self.store.path)
        self.assertEqual(len(reloaded.list("prompts")), 2)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    async def test_selected_chinese_rag_has_lines_digest_and_no_foreign_content(self):
        one = self.store.put("knowledge", {"name": "实验规则", "content": "样本说明\n动物实验必须核对刺激回读与行为事件时间。\n采样率 1000 Hz。"})
        foreign = self.store.put("knowledge", {"name": "其他实验", "content": "动物实验刺激密码：本条不应出现在另一任务。"})
        found = self.store.search("knowledge", "动物实验刺激", [one["id"]])
        self.assertTrue(found)
        self.assertTrue(all(item["id"] == one["id"] and item["sha256"] for item in found))
        self.assertTrue(all(item["start_line"] >= 1 for item in found))
        self.assertEqual(self.store.search("knowledge", "动物实验", []), [])
        self.assertNotIn(foreign["id"], json.dumps(found))

    async def test_memory_spaces_and_runtime_tools_are_bound_and_revoked(self):
        self.store.put("memories", {"name": "项目A", "content": "任务代号甲73", "scope": "projectA"})
        self.store.put("memories", {"name": "项目B", "content": "任务代号乙91", "scope": "projectB"})
        result = await platform.invoke_tool("memory_read", {}, {"memory_scope": "projectA"})
        self.assertIn("甲73", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("乙91", json.dumps(result, ensure_ascii=False))
        runtime = platform.prepare_runtime({"id": "fixture", "tools": ["memory_read"], "memory_scope": "projectA"})
        self.assertEqual(platform.runtime_context(runtime["token"])["tools"], {"memory_read"})
        with self.assertRaises(HTTPException):
            await platform.runtime_tool("memory_save", platform.ToolRequest(token=runtime["token"], arguments={"content": "bad"}))
        platform.release_runtime(runtime["token"])
        with self.assertRaises(HTTPException):
            platform.runtime_context(runtime["token"])

    async def test_tools_reject_overflow_schema_errors_and_private_web_targets(self):
        with self.assertRaises(HTTPException):
            await platform.invoke_tool("math", {"a": 1e100, "b": 1e-300, "operation": "divide"}, {})
        with self.assertRaises(HTTPException):
            await platform.invoke_tool("text_stats", {"text": "a", "shell": "bad"}, {})
        for url in ["file:///etc/passwd", "http://127.0.0.1/", "http://user:password@example.com"]:
            with self.assertRaises(HTTPException):
                await platform.public_url(url)
        stats = await platform.invoke_tool("statistics", {"values": [1, 2, 3]}, {})
        self.assertEqual(stats["result"]["mean"], 2)

    async def test_backup_preserves_resource_ids_and_validates_all_before_writes(self):
        original = self.store.put("knowledge", {"name": "说明", "content": "采样率 1000Hz"})
        exported = {"format": "modeldesk-platform", "resources": {"knowledge": [original]}, "jobs": []}
        fresh = PlatformStore(Path(self.temp.name) / "other.json")
        with patch.object(platform, "store", fresh):
            result = platform.import_data(platform.ImportRequest(data=exported))
            self.assertEqual(result["imported"], 1)
            self.assertEqual(fresh.list("knowledge")[0]["id"], original["id"])
            bad = {"format": "modeldesk-platform", "resources": {"prompts": [{"name": "Valid", "content": "x"}, {"name": "", "content": "y"}]}}
            with self.assertRaises(ValueError):
                platform.import_data(platform.ImportRequest(data=bad))
            self.assertEqual(fresh.list("prompts"), [])

    async def test_chathub_import_discards_all_provider_settings_and_credentials(self):
        request = platform.ImportRequest(data={"sync": {"credential": "do-not-import"}, "localStorage": {"private": "do-not-import"},
                    "local": {"prompts": [{"title": "提示词", "prompt": "简短回答"}], "access_token": "do-not-import"}})
        result = platform.import_data(request)
        self.assertFalse(result["credentials_imported"])
        self.assertEqual(len(self.store.list("prompts")), 1)
        self.assertNotIn("do-not-import", self.store.path.read_text())

    async def test_malformed_history_cannot_poison_existing_jobs_or_partially_import(self):
        import uuid
        bad = {"format": "modeldesk-platform", "resources": {"prompts": [{"name": "New", "content": "x"}]},
               "jobs": [{"id": str(uuid.uuid4()), "status": "complete", "events": [], "prompt": "audit"}]}
        with self.assertRaises(HTTPException):
            platform.import_data(platform.ImportRequest(data=bad))
        self.assertEqual(self.store.list("prompts"), [])

    async def test_sdk_execution_requires_explicit_approval_and_valid_environment(self):
        for payload in [{"approved": False, "sdk": "camel"}, {"approved": True, "sdk": None}]:
            with self.assertRaises(ValueError):
                JobRequest(mode="sdk_script", prompt="test", models=["a"], source="RESULT={'answer':42}", **payload)
        valid = JobRequest(mode="sdk_script", prompt="test", models=["a"], source="RESULT={'answer':42}", approved=True, sdk="camel")
        self.assertTrue(valid.approved)
        self.assertEqual(chunks("x" * 5000)[-1]["end_line"], 1)

    async def test_explicit_empty_context_never_restores_sources_from_saved_flow(self):
        import uuid
        resource = str(uuid.uuid4())
        workflow = {"agents": [{"id": "a", "role": "审核", "goal": "核查", "model": "a", "tools": []}],
                    "tasks": [{"id": "t", "description": "核对", "expected_output": "报告", "agent": "a"}], "knowledge_ids": [resource]}
        job = JobRequest(mode="crewai", prompt="test", models=["a"], workflow=workflow, knowledge_ids=[])
        self.assertEqual(job.knowledge_ids, [])
        self.assertEqual(job.workflow.knowledge_ids, [])


if __name__ == "__main__":
    unittest.main()
