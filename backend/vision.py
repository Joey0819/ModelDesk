"""Attach verified local images at the native SDK's HTTP transport boundary.

The original framework still plans, runs agents, and invokes tools. Only the
OpenAI-compatible request body is enriched; no URL is fetched by this module.
"""
import copy
import json
import threading
import io
import re
import hashlib

GATEWAY = "http://127.0.0.1:8317/v1"
_REPORT_LOCK = threading.Lock()
_IMAGE_DATA = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[a-zA-Z0-9+/=]+")
_PRIVATE_IMAGE_PATH = re.compile(r"[^\s\"'<>]*?/data/workbench/attachment-inputs/[^\s\"'<>]+")


def public_text(value):
    text = str(value)
    text = _IMAGE_DATA.sub(lambda match: "[图片数据已省略，内容指纹 " + hashlib.sha256(match.group().encode()).hexdigest()[:16] + "]", text)
    return _PRIVATE_IMAGE_PATH.sub("[已选图片副本路径已省略]", text)


def public_summary(value):
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, bytes):
        return "[二进制附件内容不写入运行记录]"
    if isinstance(value, list):
        return [public_summary(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image"}:
            return {"type": "image_reference", "note": "图片内容不写入运行记录"}
        return {public_text(key) if isinstance(key, str) else key: public_summary(item) for key, item in value.items()}
    return value


def visible_text(value):
    if isinstance(value, list):
        return "\n".join(public_text(part.get("text", "")) if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"} else "[图片附件内容不写入运行记录]" for part in value)
    return public_text(value or "")


class SafeWorkerStream(io.TextIOBase):
    """Redact complete log lines, including data URLs split across print writes."""
    def __init__(self, stream):
        self.stream, self.pending, self.dropping = stream, "", False
        self.lock = threading.RLock()

    def write(self, value):
        with self.lock:
            for index, segment in enumerate(str(value).split("\n")):
                if index:
                    self.stream.write("[过长原生日志已省略]\n" if self.dropping else public_text(self.pending) + "\n")
                    self.pending, self.dropping = "", False
                if not self.dropping:
                    self.pending += segment
                    if len(self.pending.encode('utf-8')) > 64000:
                        self.pending, self.dropping = "", True
        return len(value)

    def flush(self):
        # Keep incomplete lines private so a split data URL cannot escape on flush.
        self.stream.flush()

    def fileno(self):
        return self.stream.fileno()

    def isatty(self):
        return False


def user_content(text, image_parts=None):
    if not image_parts:
        return text
    if not isinstance(text, str):
        raise ValueError("视觉请求的文字内容必须为文本")
    for part in image_parts:
        if not isinstance(part, dict) or part.get("type") != "image_url" or not isinstance(part.get("image_url"), dict):
            raise ValueError("视觉输入不是已校验的图片内容")
        url = part["image_url"].get("url", "")
        if not isinstance(url, str) or not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
            raise ValueError("视觉输入只接受已校验本地图片的 data URL")
    return [{"type": "text", "text": text}, *copy.deepcopy(image_parts)]


def with_images(messages, image_parts):
    """Preserve all tool/assistant fields and attach once to a real user turn."""
    if not image_parts:
        return copy.deepcopy(messages)
    user_content("", image_parts)
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise ValueError("当前原生模型请求不支持多模态消息结构")
    updated = copy.deepcopy(messages)
    urls = {part["image_url"]["url"] for part in image_parts}
    present = {part.get("image_url", {}).get("url")
               for message in updated if isinstance(message.get("content"), list)
               for part in message["content"] if isinstance(part, dict) and isinstance(part.get("image_url"), dict)}
    missing = [part for part in image_parts if part["image_url"]["url"] not in present]
    if not missing:
        return updated
    target = next((message for message in reversed(updated) if message.get("role") == "user"), None)
    if target is None:
        raise ValueError("当前原生模型请求缺少可携带图片的用户消息")
    content = target.get("content")
    if isinstance(content, str):
        target["content"] = user_content(content, missing)
    elif isinstance(content, list) and all(isinstance(part, dict) for part in content):
        target["content"] = [*content, *copy.deepcopy(missing)]
    else:
        raise ValueError("当前原生模型消息无法安全携带图片")
    # Every selected image must still be present after enrichment.
    actual = {part.get("image_url", {}).get("url")
              for message in updated if isinstance(message.get("content"), list)
              for part in message["content"] if isinstance(part, dict) and isinstance(part.get("image_url"), dict)}
    if not urls <= actual:
        raise ValueError("视觉输入没有完整进入模型请求")
    return updated


def prepare_vision(data):
    if "_image_parts" not in data:
        from .attachment_io import image_content_parts
        parts = image_content_parts({"_attachments": data.get("attachments") or data.get("_attachments") or []})
        data["_image_parts"] = parts
        data["_vision_report"] = {"images_available": len(parts), "image_requests": 0, "requests": []}
    return data["_image_parts"]


def rewrite_request(request, image_parts, report=None):
    """Return a new public httpx Request; reject unsupported endpoints outright."""
    if not image_parts:
        return request
    import httpx
    expected = httpx.URL(GATEWAY + "/chat/completions")
    if request.method != "POST" or request.url != expected:
        raise ValueError("所选图片不支持此原生模型端点；未降级为纯文本")
    try:
        payload = json.loads(request.read())
        payload["messages"] = with_images(payload["messages"], image_parts)
    except (KeyError, TypeError, json.JSONDecodeError) as failure:
        raise ValueError("所选图片无法进入当前原生模型请求；未降级为纯文本") from failure
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = request.headers.copy()
    headers.pop("content-length", None)
    headers.pop("transfer-encoding", None)
    enriched = httpx.Request(request.method, request.url, headers=headers, content=body, extensions=request.extensions)
    if report is not None:
        with _REPORT_LOCK:
            report["image_requests"] += 1
            # No paths, pixels, data URLs or runtime credentials enter the report.
            report["requests"].append({"model": payload.get("model"), "image_count": len(image_parts)})
    return enriched


def vision_transports(image_parts, report=None, sync_transport=None, async_transport=None):
    import httpx

    class SyncVisionTransport(httpx.BaseTransport):
        def __init__(self):
            self.inner = sync_transport or httpx.HTTPTransport(trust_env=False)

        def handle_request(self, request):
            request.read()
            return self.inner.handle_request(rewrite_request(request, image_parts, report))

        def close(self):
            self.inner.close()

    class AsyncVisionTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = async_transport or httpx.AsyncHTTPTransport(trust_env=False)

        async def handle_async_request(self, request):
            await request.aread()
            return await self.inner.handle_async_request(rewrite_request(request, image_parts, report))

        async def aclose(self):
            await self.inner.aclose()

    return SyncVisionTransport(), AsyncVisionTransport()


def crew_vision_interceptor(image_parts, report=None):
    from crewai.llms.hooks.base import BaseInterceptor

    class LocalVisionInterceptor(BaseInterceptor):
        def on_outbound(self, request):
            return rewrite_request(request, image_parts, report)

        def on_inbound(self, response):
            return response

        async def aon_outbound(self, request):
            await request.aread()
            return self.on_outbound(request)

        async def aon_inbound(self, response):
            return response

    return LocalVisionInterceptor()


def crew_vision_llm(name, image_parts, report=None, max_tokens=3072, timeout=180):
    from crewai import LLM
    options = {"model": "openai/" + name, "base_url": GATEWAY, "api_key": "local-oauth",
               "max_tokens": max_tokens, "timeout": timeout, "max_retries": 2}
    if image_parts:
        options["interceptor"] = crew_vision_interceptor(image_parts, report)
    return LLM(**options)
