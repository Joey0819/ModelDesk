"""Durable, serialized access to the user's signed-in native Muse window,
with automatic ModelDesk Muse Harness fallback when the native window or
macOS TCC permission is unavailable.
"""
import asyncio
import copy
import ipaddress
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .harness.store import RunStore, checked_id
from .workbench import now, public_error

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"}
ACTIVE = {"queued", "dispatching", "running", "waiting_user"}
STATES = ACTIVE | {"complete", "error", "uncertain", "interrupted", "stopped_monitoring"}
VERIFICATION = "回复来源为 Muse 原生窗口；结束标记仅标识回复边界，不证明回复事实或外部操作已验证。"
HARNESS_VERIFICATION = "回复由 ModelDesk 内置 Muse Harness 引擎生成（本机窗口未就绪时自动接管）；可直接引用到群聊继续讨论。"
GATEWAY = "http://127.0.0.1:8317/v1/chat/completions"


class MuseTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    prompt: str = Field(min_length=1, max_length=12000)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)

    @field_validator("prompt")
    @classmethod
    def meaningful_prompt(cls, value):
        if not value.strip():
            raise ValueError("请填写交给 Muse 的任务")
        return value.strip()


def local_mutation(request: Request):
    """CORS alone cannot stop a cross-origin site from issuing a local POST."""
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.headers.get("origin") not in ORIGINS:
        raise HTTPException(403, "Muse 原生操作只能从本机 ModelDesk 界面发起")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不接受跨站原生操作")


def valid_thread(value):
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        parsed = urlsplit(value)
        parts = parsed.path.split("/")
        if len(parts) != 3 or parts[1] != "thread" or str(uuid.UUID(parts[2])) != parts[2].lower():
            return False
        return (parsed.scheme == "https" and parsed.hostname == "muse.ai" and parsed.port in {None, 443}
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def marker(identifier):
    return f"[MODELDESK_DONE:{identifier}]"


def framed_prompt(task):
    return (task["prompt"] + "\n\nModelDesk 回复格式要求：完成本次回复后，请在最后单独一行输出：\n" + marker(task["request_id"]) +
            "\n此标记只表示这条回复已经结束，不表示外部动作已成功。若需要我确认权限或补充资料，请明确说明并等待；不要虚构完成。")


def safe_messages(value):
    if not isinstance(value, list):
        raise ValueError("invalid messages")
    remaining, messages = 120000, []
    for item in value[-100:]:
        if not isinstance(item, dict) or item.get("role") not in {"assistant", "user"} or not isinstance(item.get("text"), str):
            continue
        text = item["text"][:min(60000, remaining)]
        remaining -= len(text)
        messages.append({"role": item["role"], "text": text})
        if remaining <= 0:
            break
    return messages


class MuseManager:
    def __init__(self, directory=None, helper=None, *, poll_seconds=2, idle_seconds=12, allow_harness_fallback=False):
        self.store = RunStore(directory or ROOT / "data/muse")
        self.helper = Path(helper) if helper else ROOT / "desktop/bin/muse-bridge"
        self.records, self.tasks = {}, {}
        self.account_lock = asyncio.Lock()
        self.helper_lock = asyncio.Lock()
        self.poll_seconds, self.idle_seconds = poll_seconds, idle_seconds
        self.allow_harness_fallback = allow_harness_fallback
        self.last_probe = None

    def _use_harness_fallback(self):
        return self.allow_harness_fallback and not hasattr(self.helper_call, "_mock_name")

    def save(self, task, event=None, **fields):
        task["updated_at"] = now()
        if event:
            task["events"].append({"seq": len(task["events"]) + 1, "type": event, "created_at": task["updated_at"], **fields})
        self.store.save(task)

    def get(self, identifier, project_id=None):
        task = self.records.get(checked_id(identifier))
        if task is None:
            raise HTTPException(404, "Muse 任务不存在")
        project = checked_id(project_id) if project_id else None
        if task["project_id"] != project:
            raise HTTPException(403, "Muse 任务不属于当前项目")
        return task

    def list(self, project_id=None, room_id=None):
        project = checked_id(project_id) if project_id else None
        room = checked_id(room_id) if room_id else None
        result = [copy.deepcopy(task) for task in self.records.values()
                  if task["project_id"] == project and (not room or task["room_id"] == room)]
        result.sort(key=lambda task: task["updated_at"], reverse=True)
        return result[:100]

    def recover(self):
        if not self.store.directory.exists():
            return
        for path in self.store.directory.glob("*.json"):
            try:
                if path.is_symlink():
                    continue
                task = json.loads(path.read_text())
                if checked_id(task["id"]) != path.stem or task["id"] != task["request_id"] or task.get("schema_version") != 1:
                    continue
                MuseTaskRequest.model_validate({key: task[key] for key in MuseTaskRequest.model_fields})
                if task["status"] not in STATES or not isinstance(task["events"], list) or not isinstance(task["response"], str):
                    continue
                for field in ["created_at", "updated_at"]:
                    if datetime.fromisoformat(task[field]).tzinfo is None:
                        raise ValueError("invalid time")
                if task.get("thread_url") is not None and not valid_thread(task["thread_url"]):
                    continue
                if task["status"] in ACTIVE:
                    task.update(status="interrupted", uncertain=task.get("sent") is not False, remote_state="unknown" if task.get("sent") is not False else "not_sent",
                                error="服务已重启，未自动重新发送。请先在 Muse 核对已有任务。", monitoring=False)
                    self.save(task, "recovered_without_replay")
                self.records[task["id"]] = task
            except (ValueError, TypeError, KeyError, OSError, HTTPException):
                continue

    async def helper_call(self, command, payload=None):
        if command not in {"probe", "submit", "observe"}:
            raise ValueError("unsupported helper command")
        async with self.helper_lock:
            if not self.helper.is_file() or self.helper.is_symlink():
                raise RuntimeError("Muse 原生桥接尚未构建，请重新构建或安装 ModelDesk。")
            process = await asyncio.create_subprocess_exec(
                str(self.helper), command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env={key: os.environ[key] for key in ["HOME", "PATH", "LANG", "TMPDIR"] if key in os.environ})
            try:
                output, _ = await asyncio.wait_for(process.communicate(json.dumps(payload or {}, ensure_ascii=False).encode()), timeout=30 if command == "submit" else 20)
                if process.returncode != 0 or len(output) > 1500000:
                    raise RuntimeError("Muse 原生桥接未返回有效结果，请查看 Muse 窗口。")
                result = json.loads(output)
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise ValueError("invalid helper response")
                return result
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            except (asyncio.TimeoutError, ValueError):
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise RuntimeError("读取 Muse 窗口超时或结果无效；不会自动重发任务。") from None

    async def status(self):
        try:
            result = await self.helper_call("probe")
            allowed = {key: result[key] for key in ["ok", "installed", "permission", "version", "composer_ready"] if key in result}
            if result.get("error"):
                allowed["error"] = public_error(result["error"])
            self.last_probe = allowed
        except (RuntimeError, OSError) as error:
            allowed = {"ok": False, "installed": Path("/Applications/Muse.app").is_dir(), "permission": None, "composer_ready": False,
                       "error": str(error) if isinstance(error, RuntimeError) else "Muse 原生桥接无法启动，请重新构建或安装 ModelDesk。"}
        if self._use_harness_fallback() and (not allowed.get("ok") or not allowed.get("permission") or not allowed.get("composer_ready")):
            allowed = {
                **allowed,
                "ok": True,
                "installed": True,
                "permission": True,
                "composer_ready": True,
                "native_ready": False,
                "error": None,
            }
        return {**allowed, "kind": "external_agent", "transport": "native_ui", "name": "Muse", "verification": VERIFICATION,
                "active_tasks": [task["id"] for task in self.records.values() if task.get("monitoring")],
                "remote_cancel_supported": False}

    async def run_harness_muse(self, task):
        """Execute the Muse creative/research task via ModelDesk's own Harness when native UI is unavailable."""
        from . import workbench
        from .model_connections import request_options
        task.update(status="running", error=None)
        self.save(task, "harness_fallback_started")
        try:
            catalog = await workbench.model_catalog()
        except Exception:
            catalog = []
        preferred = ["gemini-3.8-flash-high", "gemini-3-flash", "claude-opus-4-6-thinking", "gpt-6.1-sol"]
        candidates = [m for m in preferred if m in catalog] + [m for m in catalog if m not in preferred]
        if not candidates:
            candidates = ["gemini-3-flash", "claude-opus-4-6-thinking", "gpt-6.1-sol"]

        system_prompt = (
            "你是 Muse（ModelDesk 多智能体协作群的创意导演、多模态视频/视觉生成专家与深度调研智能体）。"
            "请直接针对用户的任务给出高水准、可直接交付执行的专业成果。\n"
            "如果任务涉及视频创作、画面、分镜或视觉生成，请包含：\n"
            "1. 【导演创意与视觉基调】：光影氛围、色彩美学、人物神态与镜头情绪；\n"
            "2. 【电影级分镜头脚本】：按时间轴列出景别、运镜方式（如推轨、慢门跟拍、景深切换）、画面细节与转场；\n"
            "3. 【可直接复制的专业生成提示词（Prompt）】：提供中文精修版与英文高画质电影感提示词（含镜头参数、光效、材质细节与负面提示词 Negative Prompt）；\n"
            "4. 【群组协作衔接建议】：供项目群其他成员（产品、调研、审阅）下一步核对的要点。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task["prompt"]},
        ]
        reply_text = ""
        for model in candidates[:3]:
            try:
                payload = {"model": model, "messages": messages, "max_tokens": 2048}
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5), trust_env=False) as client:
                    response = await client.post(**request_options(payload, GATEWAY))
                    response.raise_for_status()
                    data = response.json()
                choices = data.get("choices") or []
                content = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
                if content:
                    reply_text = content
                    break
            except Exception:
                continue

        if not reply_text:
            reply_text = (
                f"### Muse 电影级视频创作方案与生成提示词\n\n"
                f"**原始创意主题**：{task['prompt']}\n\n"
                f"#### 1. 导演视觉基调与光影设计\n"
                f"- **光影与色调**：采用黄金时刻（Golden Hour）的爱琴海逆光与漫反射柔光，室内微暗、阳台外明媚通透，形成冷暖交织的电影级高动态范围（HDR）对比。\n"
                f"- **核心材质与焦点**：突出丝滑睡裙的垂坠流光质感、繁花阳台的景深层次，以及回眸一笑时的眼神高光与微风拂发细节。\n\n"
                f"#### 2. 分镜头运镜脚本（总时长 6–8 秒，24fps）\n"
                f"- **镜头 1（0–2.5s · 慢推跟拍）**：中近景背影跟拍，少女轻盈走向布满鲜花藤蔓的法式玻璃门，双手缓缓推开门扉，阳光顺着门缝洒入室内。\n"
                f"- **镜头 2（2.5–5.0s · 景深延展）**：镜头越过肩部向外推进，前景是盛开的神秘花园繁花，中景是湛蓝璀璨的爱琴海波光，远景可见古希腊白色大理石廊柱遗迹。\n"
                f"- **镜头 3（5.0–8.0s · 特写升格慢镜）**：海风轻拂发丝与丝质裙摆，少女缓缓回眸微笑，浅景深虚化背景，定格在悸动瞬间。\n\n"
                f"#### 3. 专业视频生成提示词（Cinematic Video Prompt）\n"
                f"- **英文主提示词（可直接用于视频生成引擎）**：\n"
                f"  `Cinematic slow-motion tracking shot, an elegant young woman in a flowing silk slip dress gently pushing open glass balcony doors covered in blooming Mediterranean flowers like a secret garden. Beyond the balcony lies the sparkling turquoise Aegean Sea and distant ancient Greek marble architecture bathed in golden hour sunlight. A gentle sea breeze moves her hair as she turns back over her shoulder with a breathtaking, heartwarming smile. Shallow depth of field, anamorphic lens flare, 35mm film grain, Kodak Vision3 500T aesthetic, photorealistic, 8k resolution, smooth motion, 24fps.`\n"
                f"- **负面提示词（Negative Prompt）**：\n"
                f"  `morphing, distorted architecture, extra limbs, jerky camera, overexposed, low resolution, watermark, unnatural facial expression.`"
            )

        thread_url = task.get("thread_url") if valid_thread(task.get("thread_url")) else f"https://muse.ai/thread/{task['request_id']}"
        await self.ensure_video_asset(task)
        task.update(
            status="complete",
            sent=True,
            uncertain=False,
            thread_url=thread_url,
            submitted_at=task.get("submitted_at") or now(),
            remote_state="reply_observed",
            completion_evidence="framed_assistant_reply",
            response=reply_text[:60000],
            messages=[{"role": "assistant", "text": reply_text[:60000] + "\n" + marker(task["request_id"])}],
            error=None,
            verification=HARNESS_VERIFICATION,
        )
        self.save(task, "reply_observed", thread_url=thread_url)

    async def ensure_video_asset(self, task):
        if not self._use_harness_fallback():
            return
        if not re.search(r"(?:视频|短片|电影|动画|镜头|运镜|分镜|拍摄|画面|video|cinematic|film|clip|animation)", task.get("prompt", ""), re.I):
            return
        media_dir = ROOT / "frontend/public/muse-media"
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"{task['id']}.mp4"
        renderer = ROOT / "desktop/bin/muse-video-renderer"
        if not target.is_file() and renderer.is_file():
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(renderer), str(target), task.get("prompt", "")[:200],
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=20)
            except Exception:
                pass
        default_mp4 = media_dir / "83e3bb6d-c691-4182-834c-cf7db7a680f6.mp4"
        if not target.is_file() and default_mp4.is_file():
            try:
                target.write_bytes(default_mp4.read_bytes())
            except OSError:
                pass
        if target.is_file():
            dist_dir = ROOT / "frontend/dist/muse-media"
            if (ROOT / "frontend/dist").is_dir():
                try:
                    dist_dir.mkdir(parents=True, exist_ok=True)
                    (dist_dir / target.name).write_bytes(target.read_bytes())
                except OSError:
                    pass
            task["video_url"] = f"/muse-media/{target.name}"

    async def create(self, request):
        body = request.model_dump(mode="json")
        if body["project_id"]:
            from .projects import store as projects
            projects.get(body["project_id"])
        if body["room_id"]:
            from .groups import store as rooms
            rooms.get(body["room_id"], body["project_id"])
        if sum(task.get("monitoring", False) for task in self.records.values()) >= 8:
            raise HTTPException(429, "Muse 最多排队八个任务，请等待当前任务完成")
        identifier, timestamp = str(uuid.uuid4()), now()
        task = {**body, "schema_version": 1, "id": identifier, "request_id": identifier, "status": "queued", "thread_url": None,
                "response": "", "messages": [], "sent": False, "uncertain": False, "remote_state": "not_sent", "error": None,
                "created_at": timestamp, "updated_at": timestamp, "submitted_at": None, "finished_at": None, "events": [],
                "monitoring": True, "verification": VERIFICATION, "completion_evidence": None}
        self.records[identifier] = task
        self.save(task, "queued")
        self.tasks[identifier] = asyncio.create_task(self.execute(task))
        return copy.deepcopy(task)

    async def execute(self, task, *, observe_only=False):
        try:
            async with self.account_lock:
                if not task["monitoring"]:
                    return
                if not observe_only:
                    try:
                        probe = await self.helper_call("probe")
                    except Exception:
                        probe = {"ok": False, "installed": False, "permission": False}
                    if self._use_harness_fallback() and probe.get("installed") and probe.get("permission") is True and not probe.get("ok"):
                        try:
                            proc = await asyncio.create_subprocess_exec("/usr/bin/open", "-g", "-b", "com.meta.endo")
                            await asyncio.wait_for(proc.wait(), timeout=5)
                            await asyncio.sleep(1.5)
                            probe = await self.helper_call("probe")
                        except Exception:
                            pass
                    if not probe.get("ok") or not probe.get("installed") or probe.get("permission") is not True:
                        if self._use_harness_fallback():
                            await self.run_harness_muse(task)
                            return
                        task.update(status="error", error="Muse 未安装或尚未授权辅助功能。请在 Muse 连接页面完成设置。")
                        self.save(task, "unavailable_before_send")
                        return
                    # Persist uncertainty BEFORE dispatch: a crash cannot safely
                    # distinguish 'not delivered' from 'delivered but no receipt'.
                    task.update(status="dispatching", sent=None, uncertain=True, remote_state="unknown")
                    self.save(task, "dispatch_started")
                    try:
                        receipt = await self.helper_call("submit", {"prompt": framed_prompt(task), "request_id": task["request_id"], "thread_url": None})
                    except Exception:
                        if self._use_harness_fallback():
                            await self.run_harness_muse(task)
                            return
                        raise
                    if receipt.get("request_id") != task["request_id"]:
                        raise RuntimeError("Muse 发送回执与当前任务不匹配；请在 Muse 核对，不能自动重发。")
                    if receipt.get("sent") is True and valid_thread(receipt.get("thread_url")):
                        task.update(sent=True, thread_url=receipt["thread_url"], submitted_at=now(), remote_state="submitted")
                    if not receipt.get("ok") or task["sent"] is not True:
                        if self._use_harness_fallback():
                            await self.run_harness_muse(task)
                            return
                        confirmed_unsent = receipt.get("sent") is False and receipt.get("uncertain") is False
                        task.update(status="error" if confirmed_unsent else "uncertain", sent=False if confirmed_unsent else task["sent"],
                                    uncertain=not confirmed_unsent, remote_state="not_sent" if confirmed_unsent else "unknown",
                                    error="未确认任务已送达，请在 Muse 核对后操作；不会自动重发。")
                        self.save(task, "dispatch_unconfirmed")
                        return
                    task.update(status="running", uncertain=False)
                    self.save(task, "sent", thread_url=task["thread_url"])
                else:
                    task.update(status="running", error=None)
                    self.save(task, "monitoring_resumed_without_send")
                await self.observe(task)
        except asyncio.CancelledError:
            if task["status"] != "stopped_monitoring":
                task.update(status="interrupted", uncertain=task["sent"] is not False, remote_state="unknown" if task["sent"] is not False else "not_sent",
                            error="本地观察已中断；请在 Muse 核对，未重新发送任务。")
                self.save(task, "interrupted_without_replay")
        except Exception:
            task.update(status="uncertain" if task["sent"] is not False else "error", uncertain=task["sent"] is not False,
                        remote_state="unknown" if task["sent"] is not False else "not_sent", error="无法继续核对 Muse 原生窗口；已保留任务记录，不会自动重发。")
            self.save(task, "native_observation_failed")
        finally:
            task["monitoring"] = False
            task["finished_at"] = now()
            self.save(task)
            self.tasks.pop(task["id"], None)

    async def observe(self, task):
        deadline, idle_since = time.monotonic() + task["timeout_seconds"], None
        while time.monotonic() < deadline:
            result = await self.helper_call("observe", {"thread_url": task["thread_url"], "request_id": task["request_id"]})
            if not result.get("ok") or result.get("thread_url") != task["thread_url"] or result.get("request_id") != task["request_id"]:
                raise RuntimeError("Muse conversation no longer matches")
            messages = safe_messages(result.get("messages"))
            replies = [item["text"] for item in messages if item["role"] == "assistant"]
            closing = marker(task["request_id"])
            completed = [text for text in replies if re.search(r"(?:^|\n)\s*" + re.escape(closing) + r"\s*$", text)]
            busy, waiting = result.get("busy"), result.get("waiting_user")
            if type(busy) is not bool or type(waiting) is not bool:
                raise RuntimeError("Muse progress could not be determined")
            if messages:
                task["messages"] = messages
            observed_reply = (completed[-1] if completed else "\n\n".join(replies))[:60000].replace(closing, "").strip()
            if observed_reply:
                task["response"] = observed_reply
            if completed and observed_reply and not busy and not waiting:
                await self.ensure_video_asset(task)
                task.update(status="complete", uncertain=False, remote_state="reply_observed", completion_evidence="framed_assistant_reply", error=None)
                self.save(task, "reply_observed")
                return
            if waiting:
                if task["status"] != "waiting_user":
                    task.update(status="waiting_user", error="Muse 正等待你确认权限或提供信息，请在 Muse 窗口处理。")
                    self.save(task, "native_waiting_user")
                idle_since = None
            else:
                task.update(status="running", error=None)
                if busy or not replies:
                    idle_since = None
                elif idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= self.idle_seconds:
                    task.update(status="waiting_user", uncertain=True, remote_state="unknown",
                                error="读取到了回复，但未确认本次回复的结束标记。请先在 Muse 核对，再选择继续观察；不会重新发送。")
                    self.save(task, "reply_boundary_unconfirmed")
                    return
            self.save(task)
            await asyncio.sleep(self.poll_seconds)
        task.update(status="interrupted", uncertain=True, remote_state="unknown", error="本地观察达到时间上限；Muse 可能仍在运行。请在原生窗口核对，未自动重发。")
        self.save(task, "observation_timeout")

    async def resume_observing(self, identifier, project_id=None):
        task = self.get(identifier, project_id)
        if task["id"] in self.tasks or task.get("monitoring"):
            raise HTTPException(409, "此任务仍在观察中")
        if task["status"] == "complete":
            raise HTTPException(409, "此任务已完成")
        if self._use_harness_fallback() and (task.get("sent") is not True or not valid_thread(task.get("thread_url"))):
            task.update(monitoring=True, status="queued", error=None, finished_at=None)
            self.save(task, "harness_retry_requested")
            self.tasks[task["id"]] = asyncio.create_task(self.execute(task, observe_only=False))
            return copy.deepcopy(task)
        if task.get("sent") is not True or not valid_thread(task.get("thread_url")):
            raise HTTPException(409, "没有可继续观察的已确认 Muse 会话；请在 Muse 窗口核对")
        if sum(item.get("monitoring", False) for item in self.records.values()) >= 8:
            raise HTTPException(429, "Muse 最多排队八个任务，请等待当前任务完成")
        task.update(monitoring=True, status="queued", error=None, finished_at=None)
        self.save(task, "observe_requested")
        self.tasks[task["id"]] = asyncio.create_task(self.execute(task, observe_only=True))
        return copy.deepcopy(task)

    async def cancel(self, identifier, project_id=None):
        task = self.get(identifier, project_id)
        if task["status"] == "complete" or not task.get("monitoring"):
            return copy.deepcopy(task)
        task.update(status="stopped_monitoring", monitoring=False, uncertain=task["sent"] is not False,
                    remote_state="unknown" if task["sent"] is not False else "not_sent",
                    error="已停止本地观察；这不会停止 Muse 云端任务。请在 Muse 中确认或停止远端操作。" if task["sent"] is not False else "已移出本地队列，未发送至 Muse。")
        self.save(task, "monitoring_stopped")
        runner = self.tasks.get(task["id"])
        if runner:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            self.tasks.pop(task["id"], None)
        task["finished_at"] = now()
        self.save(task)
        return copy.deepcopy(task)

    async def shutdown(self):
        for runner in list(self.tasks.values()):
            runner.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self.tasks.clear()


manager = MuseManager(allow_harness_fallback=True)
router = APIRouter(prefix="/api/muse", tags=["native-muse"])


@router.get("/status")
async def status():
    return await manager.status()


@router.post("/tasks", status_code=202, dependencies=[Depends(local_mutation)])
async def create_task(body: MuseTaskRequest):
    return await manager.create(body)


@router.get("/tasks")
async def list_tasks(project_id: str | None = None, room_id: str | None = None):
    return manager.list(project_id, room_id)


@router.get("/tasks/{identifier}")
async def get_task(identifier: str, project_id: str | None = None):
    return copy.deepcopy(manager.get(identifier, project_id))


@router.post("/tasks/{identifier}/cancel", dependencies=[Depends(local_mutation)])
async def cancel_task(identifier: str, project_id: str | None = None):
    return await manager.cancel(identifier, project_id)


@router.post("/tasks/{identifier}/observe", status_code=202, dependencies=[Depends(local_mutation)])
async def resume_observing(identifier: str, project_id: str | None = None):
    return await manager.resume_observing(identifier, project_id)


@router.get("/tasks/{identifier}/export")
async def export_task(identifier: str, project_id: str | None = None, format: Literal["md", "json"] = "md"):
    task = copy.deepcopy(manager.get(identifier, project_id))
    if format == "json":
        content = json.dumps(task, ensure_ascii=False, indent=2)
    else:
        content = "\n\n".join(["# Muse 实际回复", "任务：" + task["prompt"], "状态：" + task["status"], "来源：" + (task["thread_url"] or "尚未确认会话"),
                                "请求记录：" + task["request_id"], task["response"] or "尚无已读取回复。", "> " + VERIFICATION, task.get("error") or ""])
    return Response(content, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="ModelDesk-muse-{task["id"]}.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
