from datetime import datetime, timedelta, timezone
import platform
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from backend import antigravity_catalog, readiness, workbench
from fastapi import HTTPException


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_status_distinguishes_installed_from_verified_when_gateway_is_down(self):
        model = antigravity_catalog.DEFAULT_ID
        jobs = {}
        with patch.object(workbench.manager, "jobs", jobs), \
             patch.object(workbench, "subscription_catalog", AsyncMock(side_effect=httpx.ConnectError("offline"))), \
             patch.object(workbench, "connection_models", return_value=[]), \
             patch.object(workbench.connection_store, "list", return_value=[]), \
             patch.object(antigravity_catalog, "read_cache", return_value={"checked_at": None, "models": []}), \
             patch("backend.antigravity_agent.find_cli", return_value="fixture-cli"), \
             patch("backend.antigravity_agent.run_antigravity_agent", AsyncMock()) as generation:
            installed = await workbench.status()
            jobs["fixture"] = {"id": "fixture", "models": [model], "events": [{"type": "stage", "stage": "native_text_response",
                "created_at": datetime.now(timezone.utc).isoformat(), "data": {"model": model, "execution_source": "native_agent"}}]}
            verified = await workbench.status()
        generation.assert_not_awaited()
        for status, verified_ids in [(installed, []), (verified, [model])]:
            self.assertFalse(status["gateway"]["online"])
            self.assertTrue(status["tools"]["group"]["available"])
            self.assertFalse(status["tools"]["chat"]["available"])
            providers = {row["id"]: row for row in status["providers"]}
            self.assertEqual(providers["antigravity"]["available_models"], [])
            self.assertEqual(providers["antigravity-agent"]["available_models"], [model])
            self.assertEqual(providers["antigravity-agent"]["verified_models"], verified_ids)
            self.assertEqual(status["models"][0]["capability"], "text")
            self.assertEqual(status["models"][0]["verified"], bool(verified_ids))

    def test_success_evidence_expires_and_future_or_undated_records_are_not_green(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(readiness.recent_test({"ok": True, "checked_at": now.isoformat()}))
        for stamp in [(now-timedelta(days=2)).isoformat(), (now+timedelta(days=1)).isoformat(), "bad", None]:
            self.assertFalse(readiness.recent_test({"ok": True, "checked_at": stamp}))

    async def test_installed_and_authorized_muse_still_requires_a_usable_window(self):
        is_linux = platform.system() == 'Linux'
        with patch('backend.group_tasks.engine_status', AsyncMock(return_value={"engines": [{"id":"codex", "label":"Codex", "available":True, "authenticated":False}]})), \
             patch('backend.muse_bridge.manager.status', AsyncMock(return_value={"installed":True, "permission":True, "native_ready":False})), \
             patch('backend.workbench.subscription_catalog', AsyncMock(return_value=['model-a'])), \
             patch('backend.antigravity_agent.find_cli', return_value=None), \
             patch('backend.model_connections.store.list', return_value=[]), \
             patch('scripts.modeldesk_worker.read_status', return_value={"online":False}):
            result=await readiness.inspect()
        states={row['id']:row['state'] for row in result['agents']}
        expected_muse = 'unavailable' if is_linux else 'needs_attention'
        self.assertEqual(states, {'subscription':'reachable', 'antigravity-agent':'unavailable',
                                  'codex':'unauthorized', 'muse':expected_muse})
        self.assertFalse(result['checks_generate_content'])

    async def test_connection_probe_never_generates_or_follows_redirects(self):
        calls=[]
        def transport(request):
            calls.append(request)
            return httpx.Response(302, headers={'location':'https://untrusted.example/'})
        original=httpx.AsyncClient
        factory=lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs)
        config={"id":"test", "name":"Fixture", "enabled":True, "last_test":{"ok":True, "checked_at":"2026-10-13T10:00:00Z"}}
        with patch('backend.model_connections.store.get_private', return_value={'base_url':'http://127.0.0.1:1234/v1','api_key':'PRIVATE_TEST'}), \
             patch.object(readiness.httpx, 'AsyncClient', side_effect=factory):
            result=await readiness.connection_health(config)
        self.assertEqual(result['state'],'degraded')
        self.assertEqual([(call.method,call.url.path) for call in calls],[('GET','/v1/models')])
        self.assertNotIn('PRIVATE_TEST',str(result))

    async def test_testing_connections_do_not_raise_false_production_health_alarms(self):
        production = {"id": "production"}
        testing = {"id": "testing", "testing": True}
        with patch('backend.group_tasks.engine_status', AsyncMock(return_value={"engines": []})), \
             patch('backend.muse_bridge.manager.status', AsyncMock(return_value={})), \
             patch('backend.workbench.subscription_catalog', AsyncMock(return_value=['model-a'])), \
             patch('backend.model_connections.store.list', return_value=[production, testing]), \
             patch('scripts.modeldesk_worker.read_status', return_value={"online": False}), \
             patch.object(readiness, 'connection_health', AsyncMock(return_value={"id": "production", "state": "reachable"})) as probe:
            await readiness.inspect()
        probe.assert_awaited_once_with(production)

    async def test_admission_checks_only_selected_endpoints_and_allows_unknown_catalogs(self):
        record = {"id": "selected", "name": "Selected"}
        with patch('backend.model_connections.store.resolve', return_value=(record, 'm')), \
             patch('backend.model_connections.store.list', return_value=[record, {"id": "unused"}]), \
             patch.object(readiness, 'connection_health', AsyncMock(return_value={"state":"unreachable", "label":"Selected", "reason":"服务未启动"})) as probe:
            with self.assertRaises(HTTPException) as raised:
                await readiness.preflight_connections(['local-model', 'connection:selected:m', 'connection:selected:m'])
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn('任务尚未开始', raised.exception.detail)
            self.assertEqual(probe.await_count, 1)
            probe.return_value = {"state": "unknown"}
            await readiness.preflight_connections(['connection:selected:m'])
            probe.reset_mock()
            await readiness.preflight_connections(['local-model'])
            probe.assert_not_awaited()
