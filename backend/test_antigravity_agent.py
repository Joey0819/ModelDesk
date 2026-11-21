"""Exercise the official headless wire protocol with a disposable CLI fixture."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend import antigravity_agent as agent


FIXTURE = r'''
import json
import os
from pathlib import Path
import sys
from time import sleep

arguments = sys.argv[1:]
persona = arguments[arguments.index("--agent") + 1]
cwd = Path.cwd()
profile = (cwd / ".agents" / "agents" / (persona + ".md")).read_text()
print("DISPOSABLE-PRIVATE-KEY debug transcript", file=sys.stderr, flush=True)

def send(event, **data):
    print(json.dumps({"event": event, **data}), flush=True)

if scenario == "no_init":
    sys.exit(1)
if scenario == "unknown_model":
    send("result", result={"status": "ERROR", "error": "Unknown model; DISPOSABLE-PRIVATE-KEY"})
    sys.exit(1)
if scenario == "startup_timeout":
    sleep(30)
effective_persona = "unexpected-agent" if scenario == "wrong_agent" else persona
init = {"agent": effective_persona, "cwd": str(cwd), "tools": ["run_command", "view_file", "finish"],
        "permission_mode": "request-review" if scenario == "not_strict" else "strict"}
if "--model" in arguments:
    init["model"] = "different-model" if scenario == "wrong_model" else arguments[arguments.index("--model") + 1]
send("init", conversation_id="new-fixture-thread", init=init)
line = sys.stdin.buffer.readline()
if not line:
    sys.exit(2)
draft_receipt = Path(receipt).with_suffix(".tmp")
draft_receipt.write_text(json.dumps({"prompt": json.loads(line), "directory": str(cwd), "profile": profile,
    "arguments": arguments, "input_closed": not sys.stdin.buffer.read(), "secret_in_env": "GEMINI_API_KEY" in os.environ,
    "pid": os.getpid()}))
draft_receipt.replace(receipt)
if scenario == "tool_step":
    send("step_update", step_update={"conversation_id": "new-fixture-thread", "step_type": "tool", "tool_name": "run_command"})
if scenario == "never_returns":
    sleep(30)
if scenario == "bad_output":
    print("DISPOSABLE-PRIVATE-KEY invalid JSON", flush=True)
if scenario == "too_many_events":
    for _ in range(10000):
        send("step_update", step_update={"conversation_id": "new-fixture-thread", "step_type": "checkpoint"})
send("result", result={"conversation_id": "other-session" if scenario == "wrong_session" else "new-fixture-thread",
    "num_turns": 2 if scenario == "resumed" else 1, "status": "ERROR" if scenario == "error_status" else "SUCCESS",
    "error": "DISPOSABLE-PRIVATE-KEY provider diagnostic" if scenario == "error_status" else None,
    "response": "x" * 10000 if scenario == "too_large" else " Fixture answer.\n"})
if scenario == "unsuccessful_exit":
    sys.exit(3)
'''


class AntigravityAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.receipt = self.folder / "receipt.json"
        self.cli = self.folder / "agy fixture"
        self.enterContext(patch.object(agent, "find_cli", return_value=self.cli))

    def arrange(self, variant):
        self.receipt.unlink(missing_ok=True)
        self.cli.write_text(f"#!{Path(sys.executable).resolve()}\nscenario = {variant!r}\nreceipt = {str(self.receipt)!r}\n" + FIXTURE)
        self.cli.chmod(0o700)

    async def test_text_uses_one_fresh_verified_workspace_and_stdin(self):
        prompt = '/teamwork "引用"\n$(echo do-not-expand)'
        receipts = []
        for _ in range(2):
            self.arrange("success")
            with patch.dict(os.environ, {"GEMINI_API_KEY": "DISPOSABLE-PRIVATE-KEY"}):
                self.assertEqual(await agent.run_antigravity_agent(prompt), "Fixture answer.")
            receipt = json.loads(self.receipt.read_text())
            receipts.append(receipt)
            self.assertEqual(receipt["prompt"], {"event": "user", "message": {"content": prompt}})
            self.assertNotIn(prompt, receipt["arguments"])
            self.assertIn("--disable-slash-commands", receipt["arguments"])
            self.assertNotIn("--dangerously-skip-permissions", receipt["arguments"])
            self.assertNotIn("--continue", receipt["arguments"])
            self.assertIn("tools: [finish]", receipt["profile"])
            self.assertIn("inheritMcp: false", receipt["profile"])
            settings = json.loads(receipt["arguments"][receipt["arguments"].index("--settings") + 1])
            self.assertEqual(settings["toolPermission"], "strict")
            self.assertEqual(settings["permissions"]["allow"], [])
            self.assertIn("command(*)", settings["permissions"]["deny"])
            self.assertTrue(receipt["input_closed"])
            self.assertFalse(receipt["secret_in_env"])
            self.assertFalse(Path(receipt["directory"]).exists())
        self.assertNotEqual(receipts[0]["directory"], receipts[1]["directory"])

    async def test_unverified_launch_never_receives_user_text(self):
        for variant in ("not_strict", "wrong_agent", "no_init"):
            with self.subTest(variant=variant):
                self.arrange(variant)
                with self.assertRaisesRegex(agent.AntigravityAgentError, "独立纯文本模式") as result:
                    await agent.run_antigravity_agent("private task")
                self.assertNotIn("DISPOSABLE-PRIVATE-KEY", str(result.exception))
                self.assertFalse(self.receipt.exists())

    async def test_requires_the_exact_explicit_model_and_refuses_invalid_ids(self):
        self.arrange("success")
        self.assertEqual(await agent.run_antigravity_agent("Small", model="gemini-4-neon-high"), "Fixture answer.")
        arguments = json.loads(self.receipt.read_text())["arguments"]
        self.assertEqual(arguments[arguments.index("--model") + 1], "gemini-4-neon-high")
        for variant in ("wrong_model", "unknown_model"):
            self.arrange(variant)
            with self.assertRaisesRegex(agent.AntigravityAgentError, "未启用所选模型") as result:
                await agent.run_antigravity_agent("Small", model="gemini-4-neon-high")
            self.assertNotIn("DISPOSABLE-PRIVATE-KEY", str(result.exception))
        for model in ("", "--mode", "gemini 4", "gemini/4", "a" * 141):
            with self.assertRaisesRegex(agent.AntigravityAgentError, "模型 ID 无效"):
                await agent.run_antigravity_agent("Small", model=model)
        self.assertFalse(self.receipt.exists())

    async def test_requires_actual_single_turn_success_and_hides_diagnostics(self):
        for variant in ("tool_step", "bad_output", "wrong_session", "resumed", "error_status", "unsuccessful_exit"):
            with self.subTest(variant=variant):
                self.arrange(variant)
                with self.assertRaises(agent.AntigravityAgentError) as result:
                    await agent.run_antigravity_agent("A short task")
                self.assertNotIn("DISPOSABLE-PRIVATE-KEY", str(result.exception))

    async def test_bounds_input_stream_response_and_elapsed_time(self):
        with self.assertRaisesRegex(agent.AntigravityAgentError, "任务过长"):
            await agent.run_antigravity_agent("a" * (agent.MAX_PROMPT_BYTES + 1))
        self.arrange("too_many_events")
        with patch.object(agent, "MAX_STREAM_BYTES", 400), self.assertRaisesRegex(agent.AntigravityAgentError, "输出超过限制"):
            await agent.run_antigravity_agent("Small")
        self.arrange("too_large")
        with patch.object(agent, "MAX_RESPONSE_BYTES", 200), self.assertRaises(agent.AntigravityAgentError):
            await agent.run_antigravity_agent("Small")
        self.arrange("startup_timeout")
        with patch.object(agent, "STARTUP_SECONDS", 0.1), self.assertRaisesRegex(agent.AntigravityAgentError, "限定时间"):
            await agent.run_antigravity_agent("Small")
        self.assertFalse(self.receipt.exists())

    async def test_cancellation_kills_the_running_turn_and_cleans_workspace(self):
        self.arrange("never_returns")
        task = asyncio.create_task(agent.run_antigravity_agent("Small"))
        try:
            async with asyncio.timeout(4):
                while not self.receipt.exists():
                    await asyncio.sleep(0.01)
            run = json.loads(self.receipt.read_text())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(Path(run["directory"]).exists())
        if os.name == "posix":
            with self.assertRaises(ProcessLookupError):
                os.kill(run["pid"], 0)


class AntigravityInstallationTests(unittest.TestCase):
    def test_macos_app_candidates_follow_path_and_home_installations(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bundled = root / "Gemini.app" / "Contents" / "Helpers" / "agy"
        bundled.parent.mkdir(parents=True)
        bundled.touch(mode=0o700)
        home_install = root / ".local/bin/agy"
        with patch.object(agent.shutil, "which", return_value=None) as lookup, \
             patch.object(agent.Path, "home", return_value=root), \
             patch.object(agent.sys, "platform", "darwin"), patch.object(agent, "PACKAGED_CLI_PATHS", (bundled,)):
            self.assertEqual(agent.find_cli(), bundled.resolve())
            home_install.parent.mkdir(parents=True)
            home_install.touch(mode=0o700)
            self.assertEqual(agent.find_cli(), home_install.resolve())
            lookup.return_value = str(root / "path-agy")
            self.assertEqual(agent.find_cli(), (root / "path-agy").resolve())
