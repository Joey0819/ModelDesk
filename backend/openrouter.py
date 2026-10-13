"""OpenRouter API client for making LLM requests."""

import httpx
import asyncio
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL
from .vision import public_text

_clock = time.monotonic


async def query_model(
    model: str,
    messages: List[Dict[str, Any]],
    timeout: float = 180.0,
    max_tokens: Optional[int] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    # Model generation has no tools or task side effects here. Retry only
    # transient transport/service failures, at most twice within one shared
    # wall-clock budget; never retry a valid/partial model answer.
    deadline = _clock() + timeout
    local = urlsplit(OPENROUTER_API_URL).hostname in {"127.0.0.1", "localhost", "::1"}
    error = None
    retries = 0
    for attempt in range(3):
        remaining = deadline - _clock()
        if remaining <= 0:
            error = httpx.ReadTimeout("council_phase_timeout")
            break
        try:
            # Remote configurations retain their existing proxy behavior.
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(timeout=httpx.Timeout(remaining, connect=min(5, remaining)), trust_env=not local) as client:
                    response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                if data.get("error"):
                    raise ValueError("gateway_response_error")
                choice = data['choices'][0]
                message = choice['message']
                content = message.get('content')
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty_model_response")
                if choice.get("finish_reason") in {"error", "content_filter"}:
                    raise ValueError("incomplete_model_response")
                return {
                    'content': public_text(content),
                    'reasoning_details': message.get('reasoning_details'),
                    'incomplete': choice.get("finish_reason") == "length",
                    **({'transport_retries': retries} if retries else {}),
                }
        except Exception as caught:
            error = caught
            retryable = isinstance(error, (httpx.RequestError, TimeoutError)) or (isinstance(error, httpx.HTTPStatusError) and (error.response.status_code in {408, 409, 429} or error.response.status_code >= 500))
            delay = 0.5 * (2 ** attempt)
            if not retryable or attempt == 2 or deadline - _clock() <= delay:
                break
            retries += 1
            print(f"Council model transport retry {retries}/2: {model}")
            await asyncio.sleep(delay)

    if error is not None:
        if isinstance(error, httpx.HTTPStatusError):
            detail = {"kind": "http_error", "status": error.response.status_code,
                      "message": f"模型网关 HTTP {error.response.status_code}"}
        elif isinstance(error, (httpx.TimeoutException, TimeoutError)):
            detail = {"kind": "timeout", "message": f"模型连接或响应超时（本阶段总时限 {timeout:g} 秒）"}
        elif isinstance(error, httpx.RequestError):
            detail = {"kind": "connection", "message": "无法连接模型网关"}
        else:
            detail = {"kind": "invalid_response", "message": "模型网关未返回有效完整的文本回答"}
        if diagnostics is not None:
            diagnostics[model] = {**detail, "transport_retries": retries}
        # Never log upstream bodies, credentials, or caller-provided text.
        print(f"Error querying model {model}: {detail['message']}")
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages, max_tokens=max_tokens, diagnostics=diagnostics) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
