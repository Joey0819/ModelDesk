"""Real Council orchestration over a mocked local gateway, without credentials."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend import openrouter
from backend.workbench import JobManager, JobRequest


class CouncilTransportTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler, options):
        original = httpx.AsyncClient
        def client(**kwargs):
            options.append(kwargs)
            return original(transport=httpx.MockTransport(handler), **kwargs)
        return patch.object(openrouter.httpx, "AsyncClient", side_effect=client)

    async def test_retries_only_transport_and_preserves_endpoint_proxy_and_budget(self):
        requests, options, errors = [], [], {}
        def handler(request):
            requests.append(json.loads(request.content))
            if len(requests) < 3:
                return httpx.Response(500, json={"error": {"message": "private-token-body"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "actual answer"}, "finish_reason": "stop"}]})
        stream = io.StringIO()
        with self.transport(handler, options), patch.object(openrouter, "OPENROUTER_API_URL", "http://127.0.0.1:8317/v1/chat/completions"), \
             patch.object(openrouter, "OPENROUTER_API_KEY", "private-header-fixture"), patch.object(openrouter.asyncio, "sleep", new=AsyncMock()), contextlib.redirect_stdout(stream):
            response = await openrouter.query_model("same-model", [{"role": "user", "content": "original task"}], max_tokens=512, diagnostics=errors)
        self.assertEqual(response["content"], "actual answer")
        self.assertEqual(response["transport_retries"], 2)
        self.assertEqual(errors, {})
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(item["model"] == "same-model" for item in requests))
        self.assertTrue(all(not option["trust_env"] and option["timeout"].connect <= 5 and option["timeout"].read <= 180 for option in options))
        self.assertNotIn("private-token-body", stream.getvalue())
        self.assertNotIn("private-header-fixture", stream.getvalue())
        for status in [400, 401, 403]:
            requests.clear()
            def denied(request):
                requests.append(request)
                return httpx.Response(status, json={"error": {"message": "never expose upstream body"}})
            with self.transport(denied, []), patch.object(openrouter, "OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"):
                errors = {}
                response = await openrouter.query_model("same-model", [], diagnostics=errors)
            self.assertIsNone(response)
            self.assertEqual(len(requests), 1)
            self.assertEqual(errors["same-model"]["status"], status)
            self.assertEqual(errors["same-model"]["transport_retries"], 0)
        requests.clear()
        with self.transport(handler, []), patch.object(openrouter, "OPENROUTER_API_URL", "http://localhost:8317/v1/chat/completions"), \
             patch.object(openrouter, "_clock", side_effect=[0, 0, 179.9]):
            response = await openrouter.query_model("same-model", [], diagnostics={})
        self.assertIsNone(response)
        self.assertEqual(len(requests), 1)

    async def test_actual_council_retains_all_failure_and_partial_model_diagnostics(self):
        async def run_case(all_failed=False, length=False):
            requests, options = [], []
            def handler(request):
                body = json.loads(request.content)
                requests.append(body)
                if all_failed or body["model"] == "b":
                    return httpx.Response(500, json={"error": {"message": "upstream-private-fixture"}})
                prompt = body["messages"][-1]["content"]
                self.assertIn("COMMON_USER_PREFERENCE", prompt)
                final = "You are the Chairman" in prompt
                text = "actual final delivery" if final else "FINAL RANKING:\n1. Response A" if "You are evaluating" in prompt else "actual first answer"
                return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "length" if final and length else "stop"}]})
            with tempfile.TemporaryDirectory() as directory:
                manager = JobManager(Path(directory))
                manager.recover()
                with self.transport(handler, options), patch.object(openrouter, "OPENROUTER_API_URL", "http://127.0.0.1:8317/v1/chat/completions"), patch.object(openrouter.asyncio, "sleep", new=AsyncMock()):
                    task = manager.create(JobRequest(mode="council", prompt="UNCHANGED_CANONICAL_TASK", system_prompt="COMMON_USER_PREFERENCE", models=["a", "b"]))
                    await manager.tasks[task["id"]]
                    job = manager.get(task["id"])
                self.assertEqual(job["prompt"], "UNCHANGED_CANONICAL_TASK")
                result = job["result"]
                self.assertTrue(result["partial_failure"])
                self.assertIn("b", result["metadata"]["diagnostics"]["responses"])
                self.assertEqual(result["metadata"]["diagnostics"]["responses"]["b"]["transport_retries"], 2)
                self.assertNotIn("upstream-private-fixture", json.dumps(job))
                if all_failed:
                    self.assertEqual(job["status"], "error")
                    self.assertIn("HTTP 500", job["error"])
                    self.assertNotIn("登录", job["error"])
                    self.assertEqual(result["stage1"], [])
                    self.assertEqual(len(requests), 6)
                elif length:
                    self.assertEqual(job["status"], "error")
                    self.assertTrue(result["partial"])
                    self.assertEqual(result["stage3"]["response"], "actual final delivery")
                else:
                    self.assertEqual(job["status"], "complete")
                    self.assertEqual(result["stage3"]["response"], "actual final delivery")
                    self.assertEqual(result["metadata"]["failed_models"], ["b"])
                    self.assertEqual(result["metadata"]["failed_reviewers"], ["b"])
                    self.assertIn("不得声称", requests[-1]["messages"][0]["content"])
                await manager.shutdown()
        await run_case(all_failed=True)
        await run_case()
        await run_case(length=True)


if __name__ == "__main__":
    unittest.main()
