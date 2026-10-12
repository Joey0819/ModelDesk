"""Check that group participants actually see the shared, isolated topic."""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch

import httpx

from backend import group_chat


class Owner:
    def __init__(self, job, previous=()):
        self.jobs = {item["id"]: item for item in [*previous, job]}

    def emit(self, job, event_type, **fields):
        job["events"].append({"type": event_type, "seq": len(job["events"]) + 1, **copy.deepcopy(fields)})


def job(**fields):
    return {"id": "current", "mode": "group", "conversation_id": "topic", "created_at": "2026-10-11T10:00:00", "models": ["first", "second"], "rounds": 2,
            "prompt": "提出方案并交叉质疑", "system_prompt": "指出证据边界", "roles": {"first": "研究员", "second": "审稿人"}, "relay_prompt": "检查上一位的论证", "context_turns": 12, "events": [], **fields}


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_shared_context_and_per_model_roles(self):
        current = job()
        captured = []
        client_type = httpx.AsyncClient

        def handle(request):
            payload = json.loads(request.content)
            captured.append(payload)
            text = "实际发言-" + payload["model"] + "-" + str(len(captured))
            return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

        with patch.object(group_chat.httpx, "AsyncClient", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)):
            result = await group_chat.run_group(Owner(current), current)
        self.assertEqual([item["model"] for item in captured], ["first", "second", "first", "second"])
        self.assertNotIn("实际发言", captured[0]["messages"][1]["content"])
        self.assertIn("实际发言-first-1", captured[1]["messages"][1]["content"])
        self.assertIn("实际发言-second-2", captured[2]["messages"][1]["content"])
        self.assertIn("审稿人", captured[1]["messages"][0]["content"])
        self.assertIn("检查上一位", captured[1]["messages"][0]["content"])
        self.assertEqual(len(result["messages"]), 4)
        self.assertFalse(result["partial_failure"])
        self.assertEqual([event["type"] for event in current["events"]].count("message"), 4)

    def test_history_is_topic_isolated_and_bounded_with_attribution(self):
        old = job(id="old", status="complete", created_at="2026-10-11T09:00:00", prompt="同话题上下文", result={"messages": [{"model": "second", "content": "历史回答"}]})
        foreign = job(id="foreign", created_at="2026-10-11T09:01:00", conversation_id="other", prompt="不应发送的另一话题", result={"messages": [{"model": "first", "content": "不应发送"}]})
        current = job(context_turns=1)
        history = group_chat.topic_history(Owner(current, [old, foreign]), current)
        messages = group_chat.discussion_messages(current, "first", history)
        self.assertIn("[second]", messages[1]["content"])
        self.assertIn("历史回答", messages[1]["content"])
        self.assertNotIn("另一话题", str(messages))
        self.assertNotIn("同话题上下文", messages[1]["content"])
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        long = group_chat.discussion_messages(current, "first", [{"speaker": "second", "content": "x" * 50000}])
        self.assertIn("被截断", str(long))
        self.assertLess(len(long[1]["content"]), 26000)

    async def test_partial_failure_is_retained_without_exposing_gateway_secret(self):
        current = job(rounds=1)
        client_type = httpx.AsyncClient

        def handle(request):
            payload = json.loads(request.content)
            if payload["model"] == "first":
                return httpx.Response(403, json={"error": "secret-access-token"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "实际答案"}, "finish_reason": "length"}]})

        with patch.object(group_chat.httpx, "AsyncClient", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)):
            result = await group_chat.run_group(Owner(current), current)
        self.assertTrue(result["partial_failure"])
        self.assertIn("403", result["messages"][0]["error"])
        self.assertTrue(result["messages"][1]["incomplete"])
        self.assertNotIn("secret-access-token", json.dumps(current))
        markdown = "\n".join(group_chat.markdown_group(result))
        self.assertIn("实际答案", markdown)
        self.assertIn("不完整", markdown)

    async def test_cancellation_preserves_completed_utterance(self):
        current = job(rounds=1)
        entered = asyncio.Event()
        release = asyncio.Event()
        client_type = httpx.AsyncClient

        async def handle(request):
            if json.loads(request.content)["model"] == "second":
                entered.set()
                await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "已完成发言"}, "finish_reason": "stop"}]})

        with patch.object(group_chat.httpx, "AsyncClient", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)):
            running = asyncio.create_task(group_chat.run_group(Owner(current), current))
            await asyncio.wait_for(entered.wait(), 1)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertEqual(len(current["result"]["messages"]), 1)
        self.assertEqual(current["result"]["messages"][0]["content"], "已完成发言")


if __name__ == "__main__":
    unittest.main()
