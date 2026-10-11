"""Fixed upstream-app catalog and startup endpoints; no arbitrary command/URL input."""
import asyncio
import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/platform/originals", tags=["original-apps"])
source = Path(__file__).resolve().parents[1] / "scripts/original_apps.py"
spec = importlib.util.spec_from_file_location("modeldesk_original_app_control", source)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
start_lock = asyncio.Lock()


@router.get("")
async def originals():
    return await asyncio.to_thread(control.catalog)


@router.post("/{identifier}/start")
async def start_original(identifier: str):
    if identifier not in {"multibot", "chathub"}:
        raise HTTPException(404, "未知原版应用")
    async with start_lock:
        try:
            return await asyncio.to_thread(control.start, identifier)
        except Exception:
            raise HTTPException(503, "原版应用启动失败，请检查本机独立环境与服务状态")
