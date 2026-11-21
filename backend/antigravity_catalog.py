"""The official agy text model directory, separate from the subscription gateway."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from . import antigravity_agent
from .gateway_catalog import atomic_write
from .model_catalog import model_capability

DEFAULT_ID = "agent:antigravity"
PROVIDER_ID = "antigravity-agent"
LABEL = "Antigravity Agent"
CACHE = Path(__file__).resolve().parents[1] / "data" / "antigravity-agent-catalog.json"
MAX_BYTES = 128 * 1024
REFRESH_LOCK = asyncio.Lock()
GEMINI_SLUG = re.compile(r"gemini-[a-z0-9][a-z0-9._-]{0,94}")


def is_agent_id(identifier):
    return isinstance(identifier, str) and (identifier == DEFAULT_ID or model_slug(identifier) is not None)


def model_slug(identifier):
    prefix = DEFAULT_ID + ":"
    if isinstance(identifier, str) and identifier.startswith(prefix):
        slug = identifier[len(prefix):]
        if GEMINI_SLUG.fullmatch(slug) and model_capability(slug) == "text":
            return slug
    return None


def valid_rows(rows):
    if not isinstance(rows, list):
        return []
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug, label = row.get("id"), row.get("label")
        if (isinstance(slug, str) and model_slug(f"{DEFAULT_ID}:{slug}") and isinstance(label, str)
                and 0 < len(label) <= 140 and label.isprintable()):
            result.setdefault(slug, {"id": slug, "label": label})
    return list(result.values())


def read_cache():
    try:
        with CACHE.open("rb") as file:
            value = json.loads(file.read(MAX_BYTES + 1))
        if isinstance(value, dict) and isinstance(value.get("checked_at"), str):
            return {"checked_at": value["checked_at"], "models": valid_rows(value.get("models"))}
    except (OSError, ValueError, RecursionError):
        pass
    return {"checked_at": None, "models": []}


def native_catalog(verified_ids=()):
    installed = antigravity_agent.find_cli() is not None
    cache = read_cache()
    common = {"provider": PROVIDER_ID, "provider_label": "Antigravity Agent · Google CLI", "group_only": True,
              "source": "native_agent", "capability": "text", "installed": installed, "unavailable": not installed,
              "catalog_checked_at": cache["checked_at"]}
    entries = [{"id": DEFAULT_ID, "label": LABEL}, *[
        {"id": f"{DEFAULT_ID}:{row['id']}", "model_id": row["id"], "label": row["label"]}
        for row in cache["models"]
    ]]
    return [{**common, **row, "verified": installed and row["id"] in verified_ids} for row in entries]


async def list_cli_models(executable):
    with TemporaryDirectory(prefix="modeldesk-agy-catalog-") as workspace:
        process = await asyncio.create_subprocess_exec(
            str(executable), "models", cwd=workspace, env=antigravity_agent.runtime_environment(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                output = bytearray()
                while chunk := await process.stdout.read(8192):
                    output.extend(chunk)
                    if len(output) > MAX_BYTES:
                        raise ValueError("catalog too large")
                if await process.wait() != 0:
                    raise ValueError("CLI did not list models")
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()
    models = []
    listed = False
    for line in output.decode("utf-8").splitlines():
        slug, separator, label = line.strip().partition("\t")
        if separator and re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,99}", slug) and label.strip():
            listed = True
            models.append({"id": slug, "label": label.strip()})
    if not listed:
        raise ValueError("unrecognized CLI directory")
    return valid_rows(models)


async def refresh_native_catalog():
    async with REFRESH_LOCK:
        now = datetime.now(timezone.utc).isoformat()
        executable = antigravity_agent.find_cli()
        error = None
        if executable is None:
            error = "主机未检测到 Google 官方 Antigravity CLI，请先安装并登录。"
        else:
            try:
                rows = await list_cli_models(executable)
                cached = {"checked_at": now, "models": rows}
                CACHE.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
                atomic_write(CACHE, json.dumps(cached, ensure_ascii=False).encode())
            except (OSError, TimeoutError, ValueError):
                error = "未能读取 Antigravity Agent 型号。请在主机检查 agy models 的登录和网络状态。"
        if error:
            cached = read_cache()
        return {"checked_at": now, "catalog_checked_at": cached["checked_at"],
                "listed": [f"{DEFAULT_ID}:{row['id']}" for row in cached["models"]],
                **({"stale": True, "error": error} if error else {})}
