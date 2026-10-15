"""Image capability adapters backed by existing local accounts or saved APIs.

Catalog entries are only candidates. A provider becomes verified after actual
image bytes have passed the delivery store's signature and size checks. Neither
model prose, Markdown URLs, nor a successful text completion count as an image.
"""
from __future__ import annotations

import base64
import asyncio
import binascii
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit

import httpx

from . import model_connections

GATEWAY = "http://127.0.0.1:8317/v1"
EVIDENCE_PATH = Path(__file__).resolve().parents[1] / "data/media-provider-status.json"
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGES = 4


class MediaProviderError(RuntimeError):
    """A safe public error; never include the upstream body or auth headers."""


def _family(model: str) -> str | None:
    leaf = model.rsplit("/", 1)[-1].lower()
    if leaf.startswith(("gpt-image-", "dall-e-")):
        return "openai"
    if leaf.startswith("gemini-") and "image" in leaf:
        return "gemini"
    if leaf.startswith("grok-") and "image" in leaf:
        return "xai"
    return None


def _evidence() -> dict:
    try:
        if EVIDENCE_PATH.is_symlink():
            return {}
        records = json.loads(EVIDENCE_PATH.read_text())
        return records if isinstance(records, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_verified(entry: dict, manifest: dict):
    # A diagnostic receipt is optional; a read-only data directory must not turn
    # a successfully saved real image into a failed generation.
    try:
        if EVIDENCE_PATH.is_symlink() or EVIDENCE_PATH.parent.is_symlink():
            return
        records = _evidence()
        records[entry["id"]] = {"checked_at": datetime.now(timezone.utc).isoformat(),
                                "protocol": entry["protocol"], "sha256": manifest.get("sha256", ""),
                                "media_id": manifest.get("id", ""),
                                "connection_updated_at": entry.get("connection_updated_at")}
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".media-status-", dir=EVIDENCE_PATH.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(records, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, EVIDENCE_PATH)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError:
        pass


def _protocol(family: str, *, base_url: str = "") -> str:
    if family == "gemini":
        if urlsplit(base_url).hostname == "generativelanguage.googleapis.com":
            return "gemini_generate_content"
        return "chat_completions_images"
    return "images_generations"


async def capabilities() -> list[dict]:
    """Return non-secret candidates from the live catalog and enabled APIs.

    available means the catalog/connection is reachable or configured. verified
    means this exact alias previously returned a stored, verified image. The
    latter is deliberately independent of ordinary model-connection text tests.
    """
    candidates = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), trust_env=False,
                                     follow_redirects=False) as client:
            response = await client.get(GATEWAY + "/models")
            response.raise_for_status()
            catalog = response.json().get("data", [])
        for item in catalog[:1000]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            model = item["id"]
            family = _family(model)
            if family:
                candidates.append({"id": model, "provider": family, "model": model,
                                   "kind": "image", "protocol": _protocol(family),
                                   "available": True, "label": model, "source": "existing_login"})
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    try:
        connections = model_connections.store.list()
    except (OSError, RuntimeError, ValueError):
        connections = []
    for connection in connections:
        if not connection.get("enabled"):
            continue
        for spec in connection.get("models", []):
            family = _family(spec["id"])
            if family:
                candidates.append({"id": spec["alias"], "provider": family, "model": spec["id"],
                                   "kind": "image", "protocol": _protocol(family, base_url=connection["base_url"]),
                                   "available": True, "label": spec.get("label") or spec["id"],
                                   "source": "saved_connection", "connection_id": connection["id"],
                                   "connection_updated_at": connection.get("updated_at")})
    receipts = _evidence()
    unique = {item["id"]: item for item in candidates}
    for item in unique.values():
        receipt = receipts.get(item["id"], {})
        if not isinstance(receipt, dict):
            receipt = {}
        item["verified"] = (receipt.get("protocol") == item["protocol"] and bool(receipt.get("sha256"))
                            and receipt.get("connection_updated_at") == item.get("connection_updated_at"))
        item["status"] = "verified" if item["verified"] else "candidate"
        if item["verified"]:
            item["checked_at"] = receipt.get("checked_at")
    return list(unique.values())


def _resolve(entry: dict, prompt: str) -> dict:
    """Resolve private credentials immediately before the request, once only."""
    resolved = model_connections.store.resolve(entry["id"])
    headers = {"Content-Type": "application/json"}
    if resolved:
        record, model = resolved
        base = record["base_url"]
        if record.get("api_key"):
            headers["Authorization"] = "Bearer " + record["api_key"]
    else:
        model, base = entry["model"], GATEWAY
    family = _family(model)
    if not family or family != entry["provider"]:
        raise MediaProviderError("所选模型没有可识别的图像生成能力，请选择图像模型。")
    protocol = _protocol(family, base_url=base)
    if protocol != entry["protocol"]:
        raise MediaProviderError("图像连接已更改，请重新选择生成服务。")
    if protocol == "chat_completions_images":
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "modalities": ["text", "image"], "stream": False}
        endpoint = base + "/chat/completions"
    elif protocol == "gemini_generate_content":
        base = base.removesuffix("/openai")
        if not re.fullmatch(r"https://generativelanguage\.googleapis\.com/v1(?:beta)?", base):
            raise MediaProviderError("Gemini 图像连接地址不受支持，请使用官方 v1beta 地址。")
        headers.pop("Authorization", None)
        if record.get("api_key"):
            headers["x-goog-api-key"] = record["api_key"]
        endpoint = base + "/models/" + quote(model, safe="-._") + ":generateContent"
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    else:
        endpoint = base + "/images/generations"
        payload = {"model": model, "prompt": prompt, "n": 1}
        if family == "openai" and model.rsplit("/", 1)[-1].startswith("gpt-image-"):
            payload.update(size="1024x1024", quality="low")
    return {"url": endpoint, "headers": headers, "json": payload}


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise MediaProviderError("图像数据为空或超过单张图片大小限制。")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise MediaProviderError("图像服务返回了无效的图片编码。") from None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise MediaProviderError("图像数据为空或超过单张图片大小限制。")
    return raw


def _image_value(value, mime_type=None):
    if not isinstance(value, str):
        return None
    if value.startswith("data:"):
        match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,(.+)", value, re.S)
        if not match:
            raise MediaProviderError("图像服务返回了不支持的图片格式。")
        return {"bytes": _decode(match[2]), "mime_type": match[1]}
    if value.startswith("https://"):
        return {"url": value, "mime_type": mime_type}
    return None


def extract_images(data: dict) -> list[dict]:
    """Extract typed image outputs from OpenAI, Gemini and proxy responses."""
    found = []
    if not isinstance(data, dict) or data.get("error"):
        raise MediaProviderError("图像服务未返回有效的图片结果。")
    for item in data.get("data", [])[:MAX_IMAGES]:
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            found.append({"bytes": _decode(item["b64_json"]), "mime_type": None})
        elif item.get("url"):
            output = _image_value(item["url"])
            if output:
                found.append(output)
    for output in data.get("output", [])[:64]:
        if isinstance(output, dict) and output.get("type") == "image_generation_call" and output.get("result"):
            found.append({"bytes": _decode(output["result"]), "mime_type": None})
    messages = [choice.get("message", {}) for choice in data.get("choices", [])[:4] if isinstance(choice, dict)]
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        parts = [*message.get("images", []), *(content if isinstance(content, list) else [])]
        for part in parts[:64]:
            if not isinstance(part, dict) or part.get("type") not in {"image_url", "image", "output_image"}:
                continue
            value = part.get("image_url", part.get("url"))
            value = value.get("url") if isinstance(value, dict) else value
            item = _image_value(value)
            if item:
                found.append(item)
    for candidate in data.get("candidates", [])[:4]:
        if not isinstance(candidate, dict):
            continue
        for part in candidate.get("content", {}).get("parts", [])[:64]:
            inline = part.get("inlineData", part.get("inline_data", {})) if isinstance(part, dict) else {}
            mime = inline.get("mimeType", inline.get("mime_type", ""))
            if mime.startswith("image/") and inline.get("data"):
                found.append({"bytes": _decode(inline["data"]), "mime_type": mime})
    if not found:
        raise MediaProviderError("模型只返回了文字或空结果，没有真实图片；本次图像任务未完成。")
    return found[:MAX_IMAGES]


async def _request(options: dict) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=8), trust_env=False,
                                 follow_redirects=False) as client:
        async with client.stream("POST", **options) as response:
            response.raise_for_status()
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                if length > MAX_RESPONSE_BYTES:
                    raise MediaProviderError("图像响应超过大小限制，本次生成未完成。")
                chunks.append(chunk)
    return json.loads(b"".join(chunks))


def _safe_error(error: Exception) -> str:
    if isinstance(error, MediaProviderError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return f"图像服务返回 HTTP {error.response.status_code}；请检查该图像模型的登录、权限和配额。"
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "图像生成超时，本次图像任务未完成。"
    if isinstance(error, httpx.RequestError):
        return "无法连接图像服务，请检查本机网关或模型连接。"
    return "图像生成或图片校验失败；未保存为已完成成果。"


async def generate_image(job: dict, prompt: str, *, provider="auto", model=None,
                         slot_id="", caption="") -> dict:
    """Generate and store one verified image, with bounded automatic fallback.

    Explicit provider/model selection is binding. Automatic selection prefers a
    verified existing connection, then Gemini's image-specific model. Only
    automatic selection without an explicit model may try one different
    provider after failure. Every attempt must produce verified image bytes.
    """
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise MediaProviderError("图片提示词为空或过长。")
    provider = {"chatgpt": "openai", "google": "gemini"}.get(provider, provider)
    if provider not in {"auto", "openai", "gemini", "xai"}:
        raise MediaProviderError("未找到所选图像服务，请先在模型连接中配置图像模型。")
    entries = await capabilities()
    selected = [item for item in entries if item["available"]
                and (provider == "auto" or item["provider"] == provider)
                and (model is None or item["id"] == model)]
    if not selected:
        raise MediaProviderError("没有可用的图像生成模型；请连接支持生图的已有账户或配置图像 API。")
    selected.sort(key=lambda item: (not item["verified"], item["provider"] != "gemini", item["id"]))
    attempts = [selected[0]]
    if provider == "auto" and model is None:
        alternate = next((entry for entry in selected if entry["provider"] != selected[0]["provider"]), None)
        if alternate:
            attempts.append(alternate)
    final_error = "图像生成未取得可校验的真实图片。"
    for entry in attempts:
        try:
            data = await _request(_resolve(entry, prompt))
            outputs = extract_images(data)
            from . import delivery_media
            metadata = {"kind": "image", "provider": entry["provider"], "model": entry["id"],
                        "slot_id": str(slot_id)[:100], "caption": str(caption)[:1000]}
            output = outputs[0]
            if "bytes" in output:
                manifest = await asyncio.to_thread(delivery_media.store.save_bytes, job, output["bytes"], **metadata,
                                                   mime_type=output.get("mime_type"))
            else:
                manifest = await delivery_media.import_url(job, output["url"], **metadata,
                                                           mime_type=output.get("mime_type"))
            if manifest.get("kind") != "image" or not manifest.get("sha256"):
                raise MediaProviderError("图片未通过成果存储校验，本次生成未完成。")
            _record_verified(entry, manifest)
            return manifest
        except Exception as error:
            # Keep only safe diagnostics, never bodies, headers or credential
            # material. CancelledError is a BaseException and propagates.
            final_error = _safe_error(error)
    prefix = "已尝试两个不同提供商，仍未取得真实图片；" if len(attempts) > 1 else ""
    raise MediaProviderError(prefix + final_error) from None
