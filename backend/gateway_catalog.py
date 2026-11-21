"""Refresh the local subscription gateway's routing directory without restarting it."""
import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone
from functools import lru_cache

import httpx
import yaml

from .model_catalog import subscription_entry

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
SUBSCRIPTION_SECTIONS = {
    "antigravity": ("antigravity",),
    "codex": ("codex-free", "codex-plus", "codex-pro", "codex-team"),
}


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
    return document


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


def file_identity(info):
    if stat.S_ISREG(info.st_mode):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode


def read_matching_file(path, identity, limit=-1):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        if file_identity(os.fstat(source.fileno())) != identity:
            raise OSError("catalog file changed before reading")
        data = source.read(limit)
        if file_identity(os.fstat(source.fileno())) != identity:
            raise OSError("catalog file changed while reading")
        return data


@lru_cache(maxsize=2)
def managed_cache_source(config_path, identity):
    try:
        target = config_target(config_path, read_matching_file(config_path, identity))
        return target[0] if target else None
    except (ValueError, RecursionError, yaml.YAMLError):
        return None


@lru_cache(maxsize=2)
def read_cached_publication(path, identity):
    data = read_matching_file(path, identity, MAX_BYTES + 1)
    try:
        if len(data) <= MAX_BYTES:
            return published_models(parse_catalog(data), SUBSCRIPTION_SECTIONS)
    except (ValueError, RecursionError):
        return None
    return None


def read_managed_cache(config_path):
    """Read the active managed catalog's provider IDs for display metadata."""
    try:
        if config_path.parent.is_symlink():
            return None
        config_identity = file_identity(config_path.stat(follow_symlinks=False))
        if config_identity is None:
            return None
        current = managed_cache_source(config_path, config_identity)
        if current is None or current.parent.is_symlink():
            return None
        info = current.stat(follow_symlinks=False)
        identity = file_identity(info)
        if identity is None or info.st_size > MAX_BYTES:
            return None
        published = read_cached_publication(current, identity)
        if (file_identity(config_path.stat(follow_symlinks=False)) == config_identity
                and file_identity(current.stat(follow_symlinks=False)) == identity):
            return published
    except OSError:
        return None
    return None


async def listed_models(providers):
    from .workbench import subscription_catalog

    entries = await subscription_catalog(with_metadata=True)
    return group_listed_models((subscription_entry(entry) for entry in entries), providers)


def group_listed_models(entries, providers):
    listed = {provider: set() for provider in providers}
    for model in entries:
        if model["provider"] in listed:
            listed[model["provider"]].add(model["id"])
    return listed


def published_models(directory, providers):
    return {
        provider: {row["id"] for key in SUBSCRIPTION_SECTIONS[provider] for row in directory.get(key, [])}
        for provider in providers
    }


def provider_reports(listed, published):
    return {
        provider: {"listed": sorted(models), "catalog_only": sorted(published[provider] - models)}
        for provider, models in listed.items()
    }


def cached_publication(entries):
    try:
        if not (WORKSPACE / "runtime/cli-proxy-api").is_file():
            return None
    except OSError:
        return None
    published = read_managed_cache(WORKSPACE / ".private/config.yaml")
    if published is None:
        return None
    listed = group_listed_models(entries, SUBSCRIPTION_SECTIONS)
    return {provider: {"catalog_only": sorted(published[provider] - models)}
            for provider, models in listed.items()}


async def refresh_gateway_catalog(source=None):
    providers = (source,) if source else tuple(SUBSCRIPTION_SECTIONS)
    path = WORKSPACE / ".private/config.yaml"
    if not (WORKSPACE / "runtime/cli-proxy-api").is_file() or not path.is_file() or path.is_symlink():
        return checked(skipped=True)
    stage = "读取订阅网关"
    try:
        async with REFRESH_LOCK:
            if config_target(path, path.read_bytes()) is None:
                return checked(skipped=True)
            before = await listed_models(providers)
            try:
                data = await fetch_catalog()
            except (OSError, ValueError, RecursionError, httpx.HTTPError):
                cached = await asyncio.to_thread(read_managed_cache, path)
                warning = ("暂时无法更新发布方模型目录。已读取本机网关，并沿用已缓存的发布方目录。" if cached is not None
                           else "暂时无法更新发布方模型目录。已读取本机网关，暂无可用的发布方目录缓存。")
                published = cached if cached is not None else published_models({}, providers)
                return checked(updated=False, providers=provider_reports(before, published),
                               publisher_warning=warning)
            published = published_models(json.loads(data), providers)
            stage = "保存模型目录"
            changed = await asyncio.to_thread(install_catalog, path, data)
            if changed is None:
                return checked(skipped=True)
            stage = "读取更新后的订阅网关"
            current = await listed_models(providers)
            if changed:
                # Provider registrations arrive independently. A Codex update
                # must not finish a wait for the Google account's directory.
                wait_for = [p for p in providers if before[p] != published[p]]
                for delay in (0.15, 0.25, 0.4, 0.6, 1, 1):
                    if all(current[p] != before[p] for p in wait_for):
                        break
                    await asyncio.sleep(delay)
                    current = await listed_models(providers)
            return checked(updated=current != before, providers=provider_reports(current, published))
    except (OSError, ValueError, yaml.YAMLError, httpx.HTTPError):
        return checked(stale=True, error=f"{stage}失败。仍显示上次可读取的模型，可点“刷新列表”重试。")
