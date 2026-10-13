"""Local API connections and a scoped native-worker inference bridge.

Only this API process reads provider keys. Public records contain stable model
aliases; native SDK workers receive a short-lived, model-scoped bridge token.
Existing subscription model IDs continue to use their original gateway.
"""
import asyncio
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "connection:"
LOCAL_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"}
SUBSCRIPTION_URL = "http://127.0.0.1:8317/v1/chat/completions"
RUNTIME_BASE = "http://127.0.0.1:8001/api/model-runtime/v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def require_local_admin(request: Request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    if (not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "模型连接只能在这台 Mac 的 ModelDesk 中管理")
    origin = request.headers.get("origin")
    if origin not in LOCAL_ORIGINS and (origin or request.method not in {"GET", "HEAD", "OPTIONS"}):
        raise HTTPException(403, "请从本机 ModelDesk 设置模型连接")


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=140, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    label: str = Field(default="", max_length=100)


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(pattern=r"^(xai|openai_compatible)$")
    base_url: str = Field(min_length=8, max_length=300)
    models: list[ModelSpec] = Field(default_factory=list, max_length=40)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    enabled: bool = True
    allow_local: bool = False

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def key_header_safe(cls, value):
        if value is not None and any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("API Key 不得包含空格或控制字符")
        return value

    @model_validator(mode="after")
    def valid_endpoint(self):
        self.base_url = checked_endpoint(self.base_url, self.allow_local)
        if self.provider == "xai" and self.base_url != "https://api.x.ai/v1":
            raise ValueError("xAI 预设必须使用官方 API 地址")
        if len({item.id for item in self.models}) != len(self.models):
            raise ValueError("模型 ID 不得重复")
        return self


def checked_endpoint(value, allow_local=False):
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError("API 地址无效") from error
    if (parts.username or parts.password or parts.query or parts.fragment or not parts.hostname
            or any(char.isspace() for char in value) or "\\" in value or "%" in value
            or any(segment in {".", ".."} for segment in parts.path.split("/"))):
        raise ValueError("API 地址不得包含凭据、查询参数、片段或特殊路径")
    host = parts.hostname.lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if loopback:
        if not allow_local or parts.scheme not in {"http", "https"} or port in {8001, 5173, 8766}:
            raise ValueError("本机 API 需明确启用，且不能指向 ModelDesk 管理端口")
    else:
        if parts.scheme != "https" or not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
            raise ValueError("远程 API 必须使用 HTTPS 域名")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host.endswith((".localhost", ".local", ".internal")):
                raise ValueError("仅支持公开 HTTPS 服务或明确启用的本机 API")
        else:
            if not address.is_global:
                raise ValueError("不能使用私有、保留或元数据 IP 地址")
    return value.rstrip("/")


class ConnectionStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / "data/model-connections")
        self.lock = threading.RLock()

    def _read(self):
        path = self.directory / "connections.json"
        if self.directory.is_symlink() or path.is_symlink():
            raise RuntimeError("模型连接存储路径不安全")
        if not path.exists():
            return {}
        try:
            path.chmod(0o600)
            records = json.loads(path.read_text())
            if not isinstance(records, dict):
                raise ValueError()
            for identifier, record in records.items():
                if str(uuid.UUID(identifier)) != identifier or record.get("id") != identifier:
                    raise ValueError()
                ConnectionInput.model_validate({field: record[field] for field in ConnectionInput.model_fields if field in record})
        except (OSError, ValueError, TypeError, AttributeError):
            # Neither parser diagnostics nor a malformed credential are public.
            raise RuntimeError("模型连接配置无法读取，请检查本机连接设置") from None
        return records

    def _write(self, records):
        if self.directory.is_symlink():
            raise RuntimeError("模型连接存储路径不安全")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".connections-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(records, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.directory / "connections.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def public(self, record):
        result = {key: copy.deepcopy(value) for key, value in record.items() if key != "api_key"}
        result["has_api_key"] = bool(record.get("api_key"))
        result["models"] = [{**item, "alias": PREFIX + record["id"] + ":" + item["id"]} for item in record["models"]]
        return result

    def list(self):
        with self.lock:
            return [self.public(record) for record in self._read().values()]

    def get_private(self, identifier):
        with self.lock:
            record = self._read().get(identifier)
            if not record:
                raise HTTPException(404, "模型连接不存在")
            return record

    def save(self, body, identifier=None):
        with self.lock:
            records = self._read()
            previous = records.get(identifier) if identifier else None
            if identifier and previous is None:
                raise HTTPException(404, "模型连接不存在")
            data = body.model_dump()
            key = data.pop("api_key", None)
            if previous and previous["base_url"] != data["base_url"] and previous.get("api_key") and not key:
                raise HTTPException(422, "修改 API 地址时必须重新输入 API Key，避免把旧密钥发送到新地址")
            key = key or (previous or {}).get("api_key", "")
            if not key and not data["allow_local"]:
                raise HTTPException(422, "请输入该服务的 API Key")
            identifier = identifier or str(uuid.uuid4())
            changed = not previous or any(previous.get(field) != data.get(field) for field in ("base_url", "models", "provider")) or key != previous.get("api_key")
            records[identifier] = {**data, "id": identifier, "api_key": key, "last_test": None if changed else previous.get("last_test"), "last_test_model": None if changed else previous.get("last_test_model"), "updated_at": now()}
            self._write(records)
            return self.public(records[identifier])

    def delete(self, identifier):
        with self.lock:
            records = self._read()
            if records.pop(identifier, None) is None:
                raise HTTPException(404, "模型连接不存在")
            self._write(records)

    def record_test(self, identifier, result, expected, model):
        with self.lock:
            records = self._read()
            if records.get(identifier) == expected:
                records[identifier]["last_test"] = result
                records[identifier]["last_test_model"] = model
                self._write(records)

    def resolve(self, alias):
        if not alias.startswith(PREFIX):
            return None
        try:
            _, identifier, model = alias.split(":", 2)
            identifier = str(uuid.UUID(identifier))
            record = self.get_private(identifier)
        except (ValueError, HTTPException) as error:
            raise ValueError("所选模型连接已删除或不存在，请重新选择模型") from error
        if not record["enabled"] or model not in {item["id"] for item in record["models"]}:
            raise ValueError("所选模型连接已停用或模型已移除，请重新选择模型")
        return record, model


store = ConnectionStore()


def model_catalog():
    return [{"id": model["alias"], "label": model.get("label") or model["id"],
             "provider": "connection:" + connection["id"], "connection_id": connection["id"],
             "connection_name": connection["name"], "verified": bool((connection.get("last_test") or {}).get("ok")) and connection.get("last_test_model") == model["id"]}
            for connection in store.list() if connection["enabled"] for model in connection["models"]]


def request_options(payload, default_url=SUBSCRIPTION_URL, default_headers=None):
    """Resolve once per request. Never mutate saved alias or caller's messages."""
    resolved = store.resolve(payload.get("model", ""))
    if resolved is None:
        return {"url": default_url, "headers": default_headers or {}, "json": payload}
    record, model = resolved
    body = {**payload, "model": model}
    headers = {"Content-Type": "application/json"}
    if record.get("api_key"):
        headers["Authorization"] = "Bearer " + record["api_key"]
    return {"url": record["base_url"] + "/chat/completions", "headers": headers, "json": body}


def safe_failure(error):
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return f"服务返回 HTTP {status}；请检查 API Key、模型权限、配额和地址。"
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "连接或模型响应超时，请检查服务状态。"
    if isinstance(error, httpx.RequestError):
        return "无法连接模型服务，请检查地址和网络。"
    return "服务未返回有效的模型响应；请确认兼容 Chat Completions API。"


router = APIRouter(prefix="/api/model-connections", tags=["model-connections"], dependencies=[Depends(require_local_admin)])


async def input_body(request):
    # Parse explicitly: FastAPI's default validation errors echo rejected input,
    # which is inappropriate for a credential-bearing request.
    try:
        data = await request.body()
        if len(data) > 65536:
            raise ValueError("body limit")
        return ConnectionInput.model_validate_json(data)
    except (ValueError, ValidationError):
        raise HTTPException(422, "连接设置无效：检查名称、HTTPS API 地址、模型 ID 和密钥格式；本机服务需勾选允许本机 API") from None


@router.get("")
async def list_connections():
    try:
        records = store.list()
    except (OSError, RuntimeError):
        raise HTTPException(503, "本机模型连接配置无法读取；已有订阅登录未被修改") from None
    return {"connections": records, "presets": [{"id": "xai", "name": "Grok / xAI", "base_url": "https://api.x.ai/v1"}, {"id": "openai_compatible", "name": "其他兼容 API", "base_url": ""}]}


@router.post("", status_code=201)
async def create_connection(request: Request):
    return store.save(await input_body(request))


@router.put("/{identifier}")
async def update_connection(identifier: uuid.UUID, request: Request):
    return store.save(await input_body(request), str(identifier))


@router.delete("/{identifier}")
async def delete_connection(identifier: uuid.UUID):
    store.delete(str(identifier))
    return {"deleted": True}


@router.post("/{identifier}/test")
async def test_connection(identifier: uuid.UUID, request: Request):
    record = store.get_private(str(identifier))
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if len(raw) > 1024 or not isinstance(body, dict) or set(body) - {"model"}:
            raise ValueError()
        model = body.get("model") or (record["models"][0]["id"] if record["models"] else None)
        if model not in {item["id"] for item in record["models"]}:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "请先保存并选择一个模型，再测试实际回复") from None
    if not record["enabled"]:
        raise HTTPException(422, "请先启用连接")
    alias = PREFIX + record["id"] + ":" + model
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8), trust_env=False, follow_redirects=False) as client:
            response = await client.post(**request_options({"model": alias, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16}))
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            if data.get("error") or not isinstance(text, str) or not text.strip():
                raise ValueError()
        result = {"ok": True, "message": "已收到该模型的实际回复。", "checked_at": now()}
    except Exception as error:
        result = {"ok": False, "message": safe_failure(error), "checked_at": now()}
    store.record_test(record["id"], result, record, model)
    return result


@router.post("/{identifier}/discover")
async def discover_models(identifier: uuid.UUID):
    record = store.get_private(str(identifier))
    headers = {"Authorization": "Bearer " + record["api_key"]} if record.get("api_key") else {}
    endpoint = "/language-models" if record["provider"] == "xai" else "/models"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False) as client:
            response = await client.get(record["base_url"] + endpoint, headers=headers)
            response.raise_for_status()
            data = response.json()
        found = []
        for item in data.get("models", data.get("data", []))[:1000]:
            if not isinstance(item, dict):
                continue
            try:
                specification = ModelSpec(id=item.get("id"), label="")
            except ValidationError:
                continue
            found.append(specification.model_dump())
        return {"models": list({item["id"]: item for item in found}.values())[:200]}
    except Exception as error:
        raise HTTPException(502, safe_failure(error)) from None


# Native SDK calls are permitted only for a live, explicitly selected model set.
# Tokens are never persisted, forwarded to the provider, or returned to browsers.
_worker_grants = {}


def grant_worker(models):
    token = secrets.token_urlsafe(32)
    _worker_grants[token] = {"models": frozenset(models), "expires": time.monotonic() + 7200}
    return token, {"MODELDESK_MODEL_BASE": RUNTIME_BASE, "MODELDESK_MODEL_TOKEN": token}


def revoke_worker(token):
    _worker_grants.pop(token, None)


runtime_router = APIRouter(prefix="/api/model-runtime/v1", tags=["internal-model-runtime"])


@runtime_router.post("/chat/completions")
async def native_completion(request: Request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    grant = _worker_grants.get(token)
    if (not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or request.headers.get("origin") or not grant or grant["expires"] <= time.monotonic()):
        raise HTTPException(403, "原生模型调用授权无效或已过期")
    raw = await request.body()
    if len(raw) > 64 * 1024 * 1024:
        raise HTTPException(413, "模型输入过大")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("model") not in grant["models"]:
            raise ValueError()
        options = request_options(payload)
    except (ValueError, TypeError):
        raise HTTPException(403, "模型不在本次任务授权范围内，或连接已停用") from None
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=8), trust_env=False, follow_redirects=False)
    try:
        response = await client.send(client.build_request("POST", **options), stream=True)
        response.raise_for_status()
    except Exception as error:
        await client.aclose()
        raise HTTPException(502, safe_failure(error)) from None
    if payload.get("stream"):
        async def stream():
            try:
                async for chunk in response.aiter_bytes():
                    if token not in _worker_grants:
                        break
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
    try:
        data = json.loads(await response.aread())
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError()
        return JSONResponse(data)
    except Exception:
        raise HTTPException(502, "模型服务未返回有效响应") from None
    finally:
        await response.aclose()
        await client.aclose()
