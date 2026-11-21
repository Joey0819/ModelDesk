"""One isolated text turn using the official, separately signed-in Antigravity CLI."""

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
from tempfile import TemporaryDirectory
import uuid


STARTUP_SECONDS = 25
TURN_SECONDS = 120
MAX_PROMPT_BYTES = 80_000
MAX_RESPONSE_BYTES = 320_000
MAX_STREAM_BYTES = 1_000_000
MAX_EVENT_BYTES = MAX_RESPONSE_BYTES + 8192
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,139}")
UNKNOWN_MODEL = re.compile(r"unknown model|no model matched|model[^\r\n]{0,160}not found|invalid model", re.I)
PACKAGED_CLI_PATHS = (
    Path("/Applications/Gemini.app/Contents/Helpers/agy"),
    Path("/Applications/Antigravity.app/Contents/Helpers/agy"),
)
SESSION_SETTINGS = {
    "agentMode": "",
    "toolPermission": "strict",
    "allowNonWorkspaceAccess": False,
    "permissions": {
        "allow": [], "ask": [],
        "deny": ["command(*)", "read_file(*)", "write_file(*)", "read_url(*)", "execute_url(*)", "mcp(*)"],
    },
}


class AntigravityAgentError(RuntimeError):
    """A safe user-facing failure without the CLI's diagnostic text."""


def find_cli():
    executable = shutil.which("agy")
    if executable:
        return Path(executable).resolve()
    candidates = [Path.home() / ".local/bin/agy"]
    if sys.platform == "darwin":
        candidates.extend(PACKAGED_CLI_PATHS)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def runtime_environment():
    basic_keys = {"HOME", "USERPROFILE", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT"}
    return {key: value for key, value in os.environ.items() if key.upper() in basic_keys}


def write_persona(workspace, name):
    agents = workspace / ".agents" / "agents"
    agents.mkdir(parents=True)
    (agents / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: ModelDesk isolated text assistant\n"
        "mainAgent: true\nsubagent: false\ntools: [finish]\ncommandExecutionPolicy: off\n"
        "mcpServers: []\ninheritMcp: false\ninheritCustomizations: false\n---\n\n"
        "Respond using only the text in the supplied message. Return the answer as text.\n",
        encoding="utf-8",
    )


async def read_events(stream):
    total = 0
    while line := await stream.readline():
        total += len(line)
        if total > MAX_STREAM_BYTES:
            raise AntigravityAgentError("Antigravity Agent 输出超过限制，请缩短任务后重试。")
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        yield event


async def stop_process(process):
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.returncode is None:
        process.kill()
    with suppress(TimeoutError, BrokenPipeError, ConnectionResetError):
        async with asyncio.timeout(2):
            await process.stdout.read()
            await process.wait()


async def exchange(process, workspace, persona, payload, model):
    events = read_events(process.stdout)
    async with asyncio.timeout(STARTUP_SECONDS):
        first = await anext(events, {})
    if model is not None and first.get("event") == "result" and isinstance(first.get("result"), dict):
        error = first["result"].get("error")
        if isinstance(error, str) and UNKNOWN_MODEL.search(error):
            raise AntigravityAgentError("Antigravity Agent 未启用所选模型，请刷新后重新选择。")
    init = first.get("init")
    conversation = first.get("conversation_id")
    if (first.get("event") != "init" or not isinstance(init, dict) or not isinstance(conversation, str)
            or not conversation or init.get("agent") != persona or init.get("cwd") != str(workspace)
            or init.get("permission_mode") != "strict"):
        raise AntigravityAgentError("无法确认 Antigravity Agent 已启用独立纯文本模式。请检查官方 CLI 和登录状态。")
    # The CLI reports its whole tool registry in init. Strict headless mode
    # requires approval for every action, and there is no approval channel.
    if model is not None and init.get("model") != model:
        raise AntigravityAgentError("Antigravity Agent 未启用所选模型，请刷新后重新选择。")

    process.stdin.write(payload)
    await process.stdin.drain()
    process.stdin.close()
    answer = None
    async for event in events:
        kind = event.get("event")
        if answer is not None:
            raise ValueError("unexpected event after result")
        if kind == "step_update":
            step = event.get("step_update")
            if (not isinstance(step, dict) or step.get("conversation_id") != conversation
                    or step.get("step_type") == "tool" or step.get("tool_name") or step.get("tool_info")
                    or step.get("subagent_info")):
                raise AntigravityAgentError("Antigravity Agent 尝试使用工具，任务已停止。")
        elif kind == "result":
            result = event.get("result")
            if (not isinstance(result, dict) or result.get("conversation_id") != conversation
                    or result.get("status") != "SUCCESS" or result.get("error")
                    or type(result.get("num_turns")) is not int or result["num_turns"] != 1):
                raise ValueError("unsuccessful or invalid result")
            answer = result.get("response")
            if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > MAX_RESPONSE_BYTES:
                raise ValueError("invalid response")
        else:
            raise ValueError("unknown stream event")
    if await process.wait() != 0 or answer is None:
        raise ValueError("missing successful completion")
    return answer.strip()


async def run_antigravity_agent(prompt: str, *, model: str | None = None) -> str:
    """Return one verified free-text result, without reusing agent conversation state."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise AntigravityAgentError("请输入要交给 Antigravity Agent 的文字任务。")
    if model is not None and (not isinstance(model, str) or not MODEL_ID.fullmatch(model)):
        raise AntigravityAgentError("Antigravity Agent 模型 ID 无效，请重新选择模型。")
    try:
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise AntigravityAgentError("交给 Antigravity Agent 的任务过长，请缩短后重试。")
        payload = (json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n").encode()
        executable = find_cli()
        if executable is None:
            raise AntigravityAgentError("未找到官方 Antigravity CLI。请安装 agy 并在本机登录 Google。")
        with TemporaryDirectory(prefix="modeldesk-antigravity-") as directory:
            workspace = Path(directory).resolve()
            persona = "modeldesk-text-" + uuid.uuid4().hex
            write_persona(workspace, persona)
            model_args = ["--model", model] if model else []
            creation = asyncio.create_task(asyncio.create_subprocess_exec(
                str(executable), "--agent", persona, *model_args, "--settings", json.dumps(SESSION_SETTINGS),
                "--input-format", "stream-json", "--output-format", "stream-json", "--disable-slash-commands",
                "--log-file", str(workspace / "agy.log"), cwd=workspace, env=runtime_environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_EVENT_BYTES, start_new_session=os.name == "posix",
            ))
            try:
                process = await asyncio.shield(creation)
            except asyncio.CancelledError:
                with suppress(OSError):
                    await stop_process(await creation)
                raise
            finished = False
            try:
                async with asyncio.timeout(TURN_SECONDS):
                    answer = await exchange(process, workspace, persona, payload, model)
                finished = True
                return answer
            finally:
                if not finished:
                    await stop_process(process)
    except TimeoutError:
        raise AntigravityAgentError("Antigravity Agent 未在限定时间内完成。请检查 CLI 登录状态和网络。") from None
    except (OSError, ValueError, RecursionError):
        raise AntigravityAgentError("Antigravity Agent 没有返回有效回复。请检查官方 CLI 和登录状态。") from None
