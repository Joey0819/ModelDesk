"""Cheap, dated readiness evidence. No model generation or tool dispatch here."""
import asyncio
from datetime import datetime, timezone
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from .model_connections import require_local_admin

RECENT_SECONDS = 86400
router = APIRouter(prefix="/api/readiness", tags=["readiness"], dependencies=[Depends(require_local_admin)])
_cached = None
_cached_at = 0
_lock = asyncio.Lock()


def evidence_age(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return age if age >= 0 else None
    except (ValueError, TypeError):
        return None


def recent_test(value):
    age = evidence_age((value or {}).get("checked_at"))
    return (value or {}).get("ok") is True and age is not None and age <= RECENT_SECONDS


def model_evidence(jobs):
    """Only dated complete text steps, never the mere presence of an alias."""
    evidence = {}
    for job in jobs.values():
        delivery = (job.get("result") or {}).get("delivery") or {}
        for step in delivery.get("steps", []):
            if step.get("kind") != "model" or step.get("status") != "complete" or not step.get("output") or step.get("truncated"):
                continue
            member = next((member for member in job.get("group_team_snapshot", {}).get("members", [])
                           if member.get("id") == step.get("member_id")), {})
            model = member.get("model")
            stamp = (step.get("attempts") or [{}])[-1].get("finished_at") or job.get("updated_at")
            age = evidence_age(stamp)
            if model and age is not None and (model not in evidence or age < evidence[model]["age_seconds"]):
                evidence[model] = {"checked_at": stamp, "age_seconds": int(age), "job_id": job["id"],
                                   "capability": "text", "recent": age <= RECENT_SECONDS}
    return evidence


async def connection_health(connection):
    from .model_connections import store
    public = {"id": "connection:" + connection["id"], "label": connection["name"],
              "configured": True, "last_success_at": (connection.get("last_test") or {}).get("checked_at")
              if (connection.get("last_test") or {}).get("ok") else None}
    if not connection["enabled"]:
        return {**public, "state": "disabled", "reason": "连接已停用"}
    # The user configured this exact endpoint. No redirects or URLs chosen by a
    # model, and no model-generation request. /models can be unsupported even
    # when /chat/completions works: that is unknown, not a false failure.
    try:
        record = store.get_private(connection["id"])
        headers = {"Authorization": "Bearer " + record["api_key"]} if record.get("api_key") else {}
        async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = await client.get(record["base_url"] + "/models", headers=headers)
        if response.status_code in {401, 403}:
            state, reason = "unauthorized", "服务拒绝凭据，请更新 API Key 或权限"
        elif response.status_code in {404, 405}:
            state, reason = "unknown", "服务未提供模型目录；是否可调用需以实际任务为准"
        elif response.is_success:
            state, reason = "reachable", "模型目录可访问；未触发生图或模型生成"
        else:
            state, reason = "degraded", f"服务返回 HTTP {response.status_code}"
    except (httpx.HTTPError, OSError, ValueError):
        state, reason = "unreachable", "无法连接服务，请检查地址与服务进程"
    return {**public, "state": state, "reason": reason}


async def preflight_connections(models):
    """Check only selected custom text endpoints before admitting a new task.

    An unsupported model-directory route remains unknown and is allowed. Known
    connection/authentication failures are reported before any job is created.
    """
    from .model_connections import store
    identifiers = set()
    for alias in models:
        if alias.startswith("connection:"):
            try:
                record, _ = store.resolve(alias)
                identifiers.add(record["id"])
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
    selected = [item for item in store.list() if item["id"] in identifiers] if identifiers else []
    checks = await asyncio.gather(*(connection_health(item) for item in selected))
    failed = [item for item in checks if item["state"] in {"unauthorized", "unreachable", "degraded", "disabled"}]
    if failed:
        raise HTTPException(503, "任务尚未开始：" + "；".join(item["label"] + " — " + item["reason"] for item in failed))


async def inspect():
    from .group_tasks import engine_status
    from .muse_bridge import manager as muse
    from .model_connections import store
    from .workbench import subscription_catalog
    from scripts.modeldesk_worker import read_status

    async def bounded(call):
        try:
            async with asyncio.timeout(12):
                return await call()
        except Exception:
            return None

    models, engines, native = await asyncio.gather(bounded(subscription_catalog), bounded(engine_status), bounded(muse.status))
    rows = [{"id": "subscription", "label": "本机模型网关", "state": "reachable" if models is not None else "unreachable",
             "reason": "目录可访问；模型授权和生成能力以实际调用为准" if models is not None else "启动本机模型网关后重试",
             "capabilities": ["text"], "model_count": len(models or [])}]
    for engine in (engines or {}).get("engines", []):
        if engine["id"] == "member_model":
            continue
        state = "ready" if engine.get("available") and engine.get("authenticated") else "unauthorized" if engine.get("available") else "unavailable"
        rows.append({"id": engine["id"], "label": engine["label"], "state": state,
                     "reason": "执行器与登录检查通过" if state == "ready" else "请登录执行器" if state == "unauthorized" else "执行器尚未就绪",
                     "capabilities": engine.get("capabilities", [])})
    if engines is None:
        rows.append({"id": "code_agents", "label": "代码 Agent", "state": "unknown", "reason": "执行器检查暂未完成"})
    from .platform_support import is_linux
    native = native or {}
    if is_linux:
        muse_state, muse_reason = "unavailable", "Muse 原生桥接仅在 macOS 上可用，Linux 平台不支持此功能。"
    elif native.get("native_ready"):
        muse_state, muse_reason = "ready", "原生输入窗口已就绪"
    elif native.get("permission"):
        muse_state, muse_reason = "needs_attention", "打开 Muse 聊天输入框"
    else:
        muse_state, muse_reason = "needs_attention", "检查 Muse 安装与辅助功能授权"
    rows.append({"id": "muse", "label": "Muse", "state": muse_state, "reason": muse_reason,
                 "capabilities": ["text", "image", "video"], "requires_awake_desktop": True,
                 "remote_cancel_supported": False, **({"available": False} if is_linux else {})})
    try:
        rows.extend(await asyncio.gather(*(connection_health(item) for item in store.list() if not item.get("testing"))))
    except Exception:
        rows.append({"id": "connections", "label": "API 连接", "state": "unknown", "reason": "无法读取连接配置"})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "checks_generate_content": False,
            "agents": rows, "worker": read_status(), "cache_seconds": 30}


@router.get("")
async def status():
    global _cached, _cached_at
    async with _lock:
        if _cached is None or time.monotonic() - _cached_at >= 30:
            _cached = await inspect()
            _cached_at = time.monotonic()
        return _cached
