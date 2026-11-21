"""Refresh the local subscription gateway's routing directory without restarting it."""
import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone

import httpx
import yaml

WORKSPACE = Path(os.environ.get("MODEL_DESK_WORKSPACE") or Path(__file__).resolve().parents[2] / "llm-tools").resolve()
SOURCES = (
    "https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/models.json",
    "https://models.router-for.me/models.json",
)
MAX_BYTES = 8 << 20
REFRESH_LOCK = asyncio.Lock()
REQUIRED_PROVIDERS = (
    "codex-free", "codex-plus", "codex-pro", "codex-team", "antigravity",
    "claude", "gemini", "vertex", "aistudio", "kimi", "xai", "meta",
)


def checked(**fields):
    return {"checked_at": datetime.now(timezone.utc).isoformat(), **fields}


def parse_catalog(data):
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("invalid routing catalog")
    # Match the release's per-provider, per-plan directory. A partial HTTP 200
    # (an error document or a lone plan) must not replace other providers.
    if not all(isinstance(document.get(key), list) for key in REQUIRED_PROVIDERS):
        raise ValueError("incomplete routing catalog")
    for rows in document.values():
        if not isinstance(rows, list):
            continue
        ids = [row.get("id") for row in rows if isinstance(row, dict)]
        if (len(ids) != len(rows) or any(not isinstance(i, str) or not i.strip() for i in ids)
                or len({i.strip() for i in ids}) != len(ids)):
            raise ValueError("invalid model entry")
    if not any(document[key] for key in REQUIRED_PROVIDERS):
        raise ValueError("empty routing catalog")


async def fetch_catalog():
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), follow_redirects=False) as client:
        for url in SOURCES:
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_BYTES:
                            raise ValueError("catalog size exceeded")
                        body.extend(chunk)
                parse_catalog(body)
                return bytes(body)
            except (httpx.HTTPError, ValueError):
                continue
    raise ValueError("publisher catalog unavailable")


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".catalog-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(fd, mode)
            output.write(data)
            output.flush()
            os.fsync(fd)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def config_target(config_path, raw):
    cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict) or str(cfg.get("config-version")) != "8":
        return None
    server = cfg.get("server")
    if not isinstance(server, dict) or server.get("host") != "127.0.0.1" or server.get("port") != 8317:
        return None
    # A gateway operator's choice takes precedence, including custom catalogs
    # for installations sharing this workspace. Only rotate our own source.
    cache = config_path.parent / "modeldesk-catalog"
    first, second = cache / "routing-a.json", cache / "routing-b.json"
    if "models" not in cfg:
        return None, first
    settings = cfg["models"]
    if not isinstance(settings, dict):
        return None
    source = settings.get("catalog")
    for current, other in ((first, second), (second, first)):
        if source == str(current):
            return current, other
    return None


def install_catalog(path, data):
    """Rotate two cached catalogs. Publish the file before switching its path.

    CLIProxyAPI hot-reloads when the configured source changes. This preserves
    existing sessions, keeps the previous snapshot, and also works offline on
    the next startup. Only the model source line is edited.
    """
    raw = path.read_bytes()
    target = config_target(path, raw)
    if target is None:
        return None
    current, destination = target
    if current and current.is_file() and current.read_bytes() == data:
        return False
    newline = b"\r\n" if b"\r\n" in raw else b"\n"

    def line(item):
        return b"  catalog: " + json.dumps(str(item), ensure_ascii=False).encode()

    if current:
        lines = raw.splitlines(keepends=True)
        matches = [i for i, value in enumerate(lines) if value.rstrip(b"\r\n") == line(current)]
        if len(matches) != 1:
            return None
        lines[matches[0]] = line(destination) + newline
        updated = b"".join(lines)
    else:
        if raw.rstrip().endswith(b"..."):  # A terminated YAML document is operator-managed.
            return None
        updated = raw + newline + b"models:" + newline + line(destination) + newline
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(destination, data)
    if path.read_bytes() != raw:
        raise ValueError("config changed while refreshing")
    atomic_write(path, updated, stat.S_IMODE(path.stat().st_mode))
    return True


async def refresh_gateway_catalog():
    from .workbench import subscription_catalog

    path = WORKSPACE / ".private/config.yaml"
    if not (WORKSPACE / "runtime/cli-proxy-api").is_file() or not path.is_file() or path.is_symlink():
        return checked(skipped=True)
    stage = "读取订阅网关"
    try:
        async with REFRESH_LOCK:
            if config_target(path, path.read_bytes()) is None:
                return checked(skipped=True)
            before = set(await subscription_catalog())
            stage = "获取网关发布的模型目录"
            data = await fetch_catalog()
            stage = "保存模型目录"
            changed = await asyncio.to_thread(install_catalog, path, data)
            if changed is None:
                return checked(skipped=True)
            if changed:
                stage = "读取更新后的订阅网关"
                # The watcher applies the source asynchronously. The UI must
                # only advertise model IDs actually published by the gateway.
                for delay in (0.15, 0.25, 0.4, 0.6, 1, 1):
                    await asyncio.sleep(delay)
                    if set(await subscription_catalog()) != before:
                        return checked(updated=True)
            return checked(updated=False)
    except (OSError, ValueError, yaml.YAMLError, httpx.HTTPError):
        return checked(stale=True, error=f"{stage}失败。仍显示上次可读取的模型，可点“刷新列表”重试。")
