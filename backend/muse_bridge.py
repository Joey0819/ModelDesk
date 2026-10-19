"""Durable, serialized access to the user's signed-in native Muse window.

Only matching native receipts and observed replies establish delivery. Model
responses and local video renderers never stand in for the Muse application.
"""
import asyncio
import copy
import ipaddress
import json
import os
import re
import stat
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .harness.store import RunStore, checked_id
from .workbench import now, public_error

ROOT = Path(__file__).resolve().parents[1]
NATIVE_STAGING = ROOT / "data/muse-native-staging"
ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"}
ACTIVE = {"queued", "dispatching", "running", "waiting_user"}
STATES = ACTIVE | {"complete", "error", "uncertain", "interrupted", "stopped_monitoring"}
VERIFICATION = "回复来源为 Muse 原生窗口；结束标记仅标识回复边界，不证明回复事实或外部操作已验证。"
HARNESS_VERIFICATION = "回复由 ModelDesk 内置 Muse Harness 引擎生成（本机窗口未就绪时自动接管）；可直接引用到群聊继续讨论。"
UNVERIFIED_GENERATION = "历史 ModelDesk 本地生成内容；没有 Muse 原生送达或回复证据，不能作为 Muse 已完成交付。"
# Only fixed helper codes are surfaced. Native diagnostics must never include
# clipboard contents, another conversation, or arbitrary helper output.
NATIVE_FAILURES = {
    "accessibility_permission_required": "尚未授予原生桥接的辅助功能权限。",
    "muse_not_running": "Muse 应用尚未运行。",
    "window_focus_lost": "无法确认 Muse 主窗口焦点，请关闭弹窗并保持主窗口可用。",
    "muse_busy_or_waiting_user": "Muse 正在生成内容或等待权限确认。",
    "new_chat_unavailable": "未找到可操作的新建会话按钮。",
    "new_chat_not_confirmed": "未能确认新建的空白会话和输入框。",
    "composer_has_draft": "输入框已有草稿，为避免覆盖而停止。",
    "draft_not_verified": "未能核对输入框中的完整任务，未确认发送。",
    "send_outcome_unknown": "已尝试发送但没有取得匹配回执，请先在 Muse 核对。",
    "reference_upload_unconfirmed": "上游图片未能确认附加到 Muse 输入框，任务未发送；图片仍保存在 ModelDesk 成果中。",
    "thread_changed": "Muse 当前会话已变化，无法继续核对原任务。",
    "request_not_visible": "当前会话中未找到本任务的请求标记。",
}


def native_failure(result):
    code = result.get("error") if isinstance(result, dict) else None
    if not isinstance(code, str) or code not in NATIVE_FAILURES:
        return "", {}
    return " " + NATIVE_FAILURES[code], {"native_error_code": code}


class MuseTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    member_id: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    prompt: str = Field(min_length=1, max_length=22000)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)

    @field_validator("prompt")
    @classmethod
    def meaningful_prompt(cls, value):
        if not value.strip():
            raise ValueError("请填写交给 Muse 的任务")
        return value.strip()

    @model_validator(mode="after")
    def member_has_room(self):
        if self.member_id and not self.room_id:
            raise ValueError("指定 Muse 群成员必须选择所属项目群")
        return self


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
            "\n如有媒体附件，请先用简短文字说明本次实际产物，再附上媒体，最后输出上述结束标记。"
            "此标记只表示这条回复已经结束，不表示外部动作已成功。若需要我确认权限或补充资料，请明确说明并等待；不要虚构完成。")


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


def safe_native_resources(value):
    """Keep actual HTTPS AX resources private until their bytes are imported.

    The native adapter establishes reply ownership. DNS and byte validation
    happen again in delivery_media.import_url; a URL alone proves no artifact.
    """
    if not isinstance(value, list):
        return []
    resources, seen = [], set()
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        url, label = item.get("url"), item.get("label", "")
        if (not isinstance(url, str) or not 0 < len(url) <= 8192
                or any(character.isspace() or ord(character) < 32 for character in url)
                or "\\" in url or not isinstance(label, str)):
            continue
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                    or parsed.fragment or parsed.port not in {None, 443}
                    or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
                continue
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
                    continue
            else:
                if not address.is_global:
                    continue
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        # AX labels sometimes repeat the signed URL; a caption must not carry
        # that grant into a public media manifest after the download.
        label = re.sub(r"https://\S+", "[Muse 媒体资源]", label.replace("\x00", ""))[:200]
        resources.append({"url": url, "label": label})
    return resources


def public_task(task):
    """Strip native download grants from every HTTP response and export.

    Signed asset links may also occur in observed prose. Only the server-side
    manager.get record is available to the media importer; public task text
    replaces those links rather than exposing reusable download credentials.
    """
    private_urls = {item["url"] for item in safe_native_resources(task.get("_native_resources"))}

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if not str(key).startswith("_")}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for url in private_urls:
                value = value.replace(url, "[Muse 媒体资源已转交成果收集]")

            def redact(match):
                url = match.group(0)
                try:
                    keys = {key.lower() for key, _ in parse_qsl(urlsplit(url).query)}
                except ValueError:
                    return "[Muse 私有资源链接]"
                if any(key.startswith(("x-amz-", "x-goog-")) or key in {
                    "token", "access_token", "refresh_token", "signature", "sig", "policy", "key-pair-id",
                    "googleaccessid", "authorization", "jwt", "credential", "api_key", "apikey",
                } for key in keys):
                    return "[Muse 私有资源链接]"
                return url

            return re.sub(r"https://[^\s<>\[\]{}\"']+", redact, value)
        return value

    result = clean(task)
    result['media_delivery'] = {'images': sum(a.get('kind') == 'image' for a in task.get('_native_media', [])),
        'videos': sum(a.get('kind') == 'video' for a in task.get('_native_media', [])), 'errors': task.get('_native_media_errors', [])}
    return result


def native_stage(identifier, *, create=False):
    """Open only this task's private directory, rejecting every symlink."""
    identifier = checked_id(identifier)
    path = NATIVE_STAGING.absolute() / identifier
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        details = os.fstat(descriptor)
        if details.st_uid != os.getuid():
            raise ValueError("private staging owner mismatch")
        if create:
            os.fchmod(descriptor, 0o700)
        return path, descriptor
    except Exception:
        os.close(descriptor)
        raise


def import_native_files(task, result):
    """Read exact helper-selected basenames; never inspect the user's Downloads."""
    from .delivery_media import store as media_store
    assets, errors = [], []
    if result.get("file_request_id") != task["request_id"]:
        return assets, errors
    files = result.get("downloaded_files")
    if not isinstance(files, list):
        files = []
    diagnostics = {
        'native_video_unavailable': 'Muse 原生附件显示 Video not found：视频资源不可用，文字回复不代表已交付。',
        'native_download_save_panel_unavailable': 'Muse 下载按钮未打开保存窗口，尚未取得附件。',
        'native_download_focus_unconfirmed': '附件下载时 Muse 窗口焦点未确认，请保持 Mac 解锁并打开对应会话。',
        'native_gallery_preview_unconfirmed': 'Muse 图片预览未就绪，尚未取得原图。',
    }
    if result.get("download_errors"):
        errors.extend(dict.fromkeys(diagnostics.get(code, 'Muse 附件下载未确认，实际文件尚未回收。') for code in result['download_errors'] if isinstance(code, str)))
    if not files:
        return assets, errors
    try:
        _, directory = native_stage(task["id"])
    except (OSError, ValueError):
        return [], ["Muse 私有附件目录无法安全读取，未导入媒体。"]
    try:
        for item in files[:5]:
            filename = item.get("name") if isinstance(item, dict) else None
            if not isinstance(filename, str) or not re.fullmatch(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}\.(?:mp4|webm|png|jpe?g|webp)", filename):
                errors.append("Muse 附件标识无效，未读取该文件。")
                continue
            descriptor = None
            try:
                descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                details = os.fstat(descriptor)
                if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_uid != os.getuid()
                        or not 0 < details.st_size <= 150_000_000):
                    raise ValueError("invalid private attachment")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    raw = source.read(150_000_001)
                if len(raw) != details.st_size or os.fstat(descriptor).st_size != details.st_size:
                    raise ValueError("attachment changed while reading")
                caption = re.sub(r"https://\S+", "[Muse 媒体资源]", str(item.get("label") or "Muse 本轮实际媒体"))[:200]
                caption = {"Download video": "Muse 生成的视频", "Download image": "Muse 生成的图片",
                           "Download picture": "Muse 生成的图片"}.get(caption, caption)
                media_job = {"id": task.get("parent_job_id") or task["id"], "project_id": task.get("project_id")}
                asset = media_store.save_bytes(media_job, raw, kind="auto", provider="muse", model="Muse native",
                                              slot_id=f"muse-native-{len(assets) + 1}", caption=caption, source_task_id=task["id"])
                assets.append(asset)
                # Only remove the exact regular file just imported, never a
                # path returned by the native app or a directory listing.
                if os.stat(filename, dir_fd=directory, follow_symlinks=False).st_ino == details.st_ino:
                    os.unlink(filename, dir_fd=directory)
            except Exception:
                errors.append("Muse 附件未通过真实图片或视频校验，未计入已完成媒体。")
            finally:
                if descriptor is not None:
                    os.close(descriptor)
    finally:
        os.close(directory)
    return assets, list(dict.fromkeys(errors))


def unverified_generation(task):
    return (task.get("harness_generated") is True or task.get("source_unverified") is True
            or task.get("verification") == HARNESS_VERIFICATION
            or any(event.get("type") in {"harness_fallback_started", "harness_retry_requested"}
                   for event in task.get("events", []) if isinstance(event, dict)))


def preserve_source_boundary(task):
    """Keep historical generated text while withdrawing unsupported receipts."""
    if not unverified_generation(task):
        return task
    task.pop("_native_resources", None)
    task.pop("_native_media", None)
    task.pop("_native_media_errors", None)
    task.update(source_unverified=True, source_kind="unverified_local_generation", harness_generated=True,
                verification=UNVERIFIED_GENERATION, completion_evidence="source_unverified")
    for field in ["thread_url", "video_url"]:
        if task.get(field):
            task.setdefault("unverified_" + field, task[field])
        task[field] = None
    if task.get("status") == "complete":
        task.update(status="uncertain", uncertain=True, sent=None, remote_state="unknown")
    return task


class MuseManager:
    def __init__(self, directory=None, helper=None, *, poll_seconds=2, idle_seconds=12, allow_harness_fallback=False):
        self.store = RunStore(directory or ROOT / "data/muse")
        self.helper = Path(helper) if helper else ROOT / "desktop/bin/muse-bridge"
        self.records, self.tasks = {}, {}
        self.account_lock = asyncio.Lock()
        self.helper_lock = asyncio.Lock()
        self.poll_seconds, self.idle_seconds = poll_seconds, idle_seconds
        # Kept as a constructor compatibility argument for older callers only.
        # Native failure must remain a failure, including when this was enabled.
        self.allow_harness_fallback = False
        self.last_probe = None

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
        return preserve_source_boundary(task)

    def list(self, project_id=None, room_id=None):
        project = checked_id(project_id) if project_id else None
        room = checked_id(room_id) if room_id else None
        result = [public_task(preserve_source_boundary(copy.deepcopy(task))) for task in self.records.values()
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
                MuseTaskRequest.model_validate({key: task[key] for key in MuseTaskRequest.model_fields if key in task})
                if task["status"] not in STATES or not isinstance(task["events"], list) or not isinstance(task["response"], str):
                    continue
                for field in ["created_at", "updated_at"]:
                    if datetime.fromisoformat(task[field]).tzinfo is None:
                        raise ValueError("invalid time")
                if task.get("thread_url") is not None and not valid_thread(task["thread_url"]):
                    continue
                if (task["status"] == "complete" and task.get("sent") is True
                        and task.get("completion_evidence") == "framed_assistant_reply"):
                    task["_native_resources"] = safe_native_resources(task.get("_native_resources"))
                else:
                    task.pop("_native_resources", None)
                if task["status"] in ACTIVE:
                    task.update(status="interrupted", uncertain=task.get("sent") is not False, remote_state="unknown" if task.get("sent") is not False else "not_sent",
                                error="服务已重启，未自动重新发送。请先在 Muse 核对已有任务。", monitoring=False)
                    self.save(task, "recovered_without_replay")
                self.records[task["id"]] = preserve_source_boundary(task)
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
                timeout = (90 if (payload or {}).get("attachment_names") else 30) if command == "submit" else 120 if (payload or {}).get("download_directory") else 20
                output, _ = await asyncio.wait_for(process.communicate(json.dumps(payload or {}, ensure_ascii=False).encode()), timeout=timeout)
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
        native_ready = all(allowed.get(field) is True for field in ["ok", "installed", "permission", "composer_ready"])
        return {**allowed, "native_ready": native_ready, "kind": "external_agent", "transport": "native_ui", "name": "Muse", "verification": VERIFICATION,
                "active_tasks": [task["id"] for task in self.records.values() if task.get("monitoring")],
                "remote_cancel_supported": False}

    def member_scope(self, body):
        if not body.get("room_id"):
            return None, None
        from .groups import store as rooms
        room = rooms.get(body["room_id"], body["project_id"])
        if not body.get("member_id"):
            return None, None
        member = next((item for item in room["team_snapshot"]["members"] if item["id"] == body["member_id"]), None)
        if not member or member.get("kind", "model") != "muse":
            raise HTTPException(422, "请选择当前项目群中已保存的 Muse Agent")
        return copy.deepcopy(member), room["team_revision"]

    def validate_member(self, task):
        if not task.get("member_id"):
            return
        member, revision = self.member_scope(task)
        if member != task.get("member") or revision != task.get("team_revision"):
            raise HTTPException(409, "群成员已变化，请确认 Muse 成员后重新派发；不会自动重发")

    async def task_helper_call(self, task, command, payload=None):
        # Native calls can wait on TCC or a busy account. Never execute or
        # attribute a reply to a member that changed during the wait.
        self.validate_member(task)
        result = await self.helper_call(command, payload)
        self.validate_member(task)
        return result

    async def refresh_media(self, identifier, project_id):
        task = self.get(identifier, project_id)
        canonical = self.records[identifier]
        if task.get('sent') is not True or task.get('status') != 'complete' or task.get('monitoring'):
            return task
        # Observe the same visible conversation; never submit a new generation.
        path, descriptor = native_stage(identifier, create=True)
        os.close(descriptor)
        result = await self.task_helper_call(canonical, 'observe', {'request_id': task['request_id'], 'thread_url': task['thread_url'], 'download_directory': str(path)})
        if result.get('ok') and result.get('thread_url') == task['thread_url'] and result.get('request_id') == task['request_id']:
            media, errors = await asyncio.to_thread(import_native_files, canonical, result)
            existing = canonical.setdefault('_native_media', [])
            for asset in media:
                if not any(saved['sha256'] == asset['sha256'] for saved in existing):
                    existing.append(asset)
            canonical['_native_media_errors'] = errors
            self.save(canonical, 'attachments_refreshed_without_send')
        else:
            raise HTTPException(409, '未确认原会话。请在 Muse 打开此任务的原会话，再重新回收附件；没有重新发送。')
        return self.get(identifier, project_id)

    async def create(self, request, *, parent_job_id=None, reference_assets=None):
        parent_link = {"parent_job_id": checked_id(parent_job_id)} if parent_job_id is not None else {}
        body = request.model_dump(mode="json")
        if body["project_id"]:
            from .projects import store as projects
            projects.get(body["project_id"])
        member, revision = self.member_scope(body)
        if sum(task.get("monitoring", False) for task in self.records.values()) >= 8:
            raise HTTPException(429, "Muse 最多排队八个任务，请等待当前任务完成")
        identifier, timestamp = str(uuid.uuid4()), now()
        task = {**body, **parent_link, "schema_version": 1, "id": identifier, "request_id": identifier, "status": "queued", "thread_url": None,
                "response": "", "messages": [], "sent": False, "uncertain": False, "remote_state": "not_sent", "error": None,
                "created_at": timestamp, "updated_at": timestamp, "submitted_at": None, "finished_at": None, "events": [],
                "monitoring": True, "verification": VERIFICATION, "completion_evidence": None,
                "source_kind": "native_muse", "member": member, "team_revision": revision}
        if reference_assets:
            if not parent_job_id or len(reference_assets) > 4:
                raise HTTPException(422, '图片交接必须关联当前父任务，最多四张')
            from .delivery_media import store as media_store
            folder, descriptor = native_stage(identifier, create=True)
            os.close(descriptor)
            names = []
            for value in reference_assets:
                asset, raw = media_store.read({'id': parent_job_id, 'project_id': body['project_id']}, value['id'])
                if asset['kind'] != 'image' or asset['sha256'] != value['sha256']:
                    raise HTTPException(409, '交接图片不匹配当前任务')
                target = folder / asset['name']
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(raw)
                names.append(asset['name'])
            task['_reference_names'] = names
            task['reference_images'] = [{'id': value['id'], 'sha256': value['sha256']} for value in reference_assets]
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
                        probe = await self.task_helper_call(task, "probe")
                    except HTTPException:
                        raise
                    except Exception:
                        probe = {"ok": False, "installed": False, "permission": False}
                    if (not probe.get("ok") or not probe.get("installed") or probe.get("permission") is not True
                            or probe.get("composer_ready") is False):
                        detail, diagnostic = native_failure(probe)
                        task.update(status="error", error="Muse 原生窗口尚未就绪。请打开 Muse、授权辅助功能并确认输入窗口可用。" + detail,
                                    **diagnostic)
                        self.save(task, "unavailable_before_send", **diagnostic)
                        return
                    # Persist uncertainty BEFORE dispatch: a crash cannot safely
                    # distinguish 'not delivered' from 'delivered but no receipt'.
                    task.update(status="dispatching", sent=None, uncertain=True, remote_state="unknown")
                    self.save(task, "dispatch_started")
                    submit_payload = {"prompt": framed_prompt(task), "request_id": task["request_id"], "thread_url": None}
                    if task.get('_reference_names'):
                        folder, descriptor = native_stage(task['id'])
                        os.close(descriptor)
                        submit_payload.update(download_directory=str(folder), attachment_names=task['_reference_names'])
                    receipt = await self.task_helper_call(task, "submit", submit_payload)
                    if receipt.get("request_id") != task["request_id"]:
                        raise RuntimeError("Muse 发送回执与当前任务不匹配；请在 Muse 核对，不能自动重发。")
                    if receipt.get("sent") is True and valid_thread(receipt.get("thread_url")):
                        task.update(sent=True, thread_url=receipt["thread_url"], submitted_at=now(), remote_state="submitted")
                    if not receipt.get("ok") or task["sent"] is not True:
                        confirmed_unsent = receipt.get("sent") is False and receipt.get("uncertain") is False
                        detail, diagnostic = native_failure(receipt)
                        task.update(status="error" if confirmed_unsent else "uncertain", sent=False if confirmed_unsent else task["sent"],
                                    uncertain=not confirmed_unsent, remote_state="not_sent" if confirmed_unsent else "unknown",
                                    error="未确认任务已送达，请在 Muse 核对后操作；不会自动重发。" + detail, **diagnostic)
                        self.save(task, "dispatch_unconfirmed", **diagnostic)
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
        except HTTPException as error:
            task.update(status="uncertain" if task["sent"] is not False else "error", uncertain=task["sent"] is not False,
                        remote_state="unknown" if task["sent"] is not False else "not_sent", error=str(error.detail))
            self.save(task, "member_scope_changed_without_replay")
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
            payload = {"thread_url": task["thread_url"], "request_id": task["request_id"]}
            try:
                path, descriptor = native_stage(task["id"], create=True)
                os.close(descriptor)
                payload["download_directory"] = str(path)
            except (OSError, ValueError):
                pass
            result = await self.task_helper_call(task, "observe", payload)
            if not result.get("ok") or result.get("thread_url") != task["thread_url"] or result.get("request_id") != task["request_id"]:
                raise RuntimeError("Muse conversation no longer matches")
            messages = safe_messages(result.get("messages"))
            replies = [item["text"] for item in messages if item["role"] == "assistant"]
            closing = marker(task["request_id"])
            # Accessibility text can collapse Markdown newlines into spaces.
            # Still require this task's exact, whitespace-delimited suffix.
            completed = [text for text in replies if re.search(r"(?:^|\s)" + re.escape(closing) + r"\s*$", text)]
            busy, waiting = result.get("busy"), result.get("waiting_user")
            if type(busy) is not bool or type(waiting) is not bool:
                raise RuntimeError("Muse progress could not be determined")
            # Keep the matched native receipt, not an inference from task prose.
            # The coordinator may renew observation of this same request when
            # its local window expires while Muse is demonstrably still busy.
            task["_last_native_observation"] = {
                "at": time.time(), "busy": busy, "waiting_user": waiting,
                "thread_url": result["thread_url"], "request_id": result["request_id"],
            }
            if messages:
                task["messages"] = messages
            observed_reply = (completed[-1] if completed else "\n\n".join(replies))[:60000].replace(closing, "").strip()
            if observed_reply:
                task["response"] = observed_reply
            if completed and observed_reply and not busy and not waiting:
                task["_native_resources"] = (safe_native_resources(result.get("resources"))
                                             if result.get("resource_request_id") == task["request_id"] else [])
                self.validate_member(task)
                media, errors = await asyncio.to_thread(import_native_files, task, result)
                self.validate_member(task)
                task["_native_media"] = media
                task["_native_media_errors"] = errors
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
        self.validate_member(task)
        if task["id"] in self.tasks or task.get("monitoring"):
            raise HTTPException(409, "此任务仍在观察中")
        if task["status"] == "complete":
            raise HTTPException(409, "此任务已完成")
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


manager = MuseManager()
router = APIRouter(prefix="/api/muse", tags=["native-muse"])


@router.get("/status")
async def status():
    return await manager.status()


@router.post("/tasks", status_code=202, dependencies=[Depends(local_mutation)])
async def create_task(body: MuseTaskRequest):
    return public_task(await manager.create(body))


@router.get("/tasks")
async def list_tasks(project_id: str | None = None, room_id: str | None = None):
    return manager.list(project_id, room_id)


@router.get("/tasks/{identifier}")
async def get_task(identifier: str, project_id: str | None = None):
    return public_task(manager.get(identifier, project_id))


@router.post("/tasks/{identifier}/cancel", dependencies=[Depends(local_mutation)])
async def cancel_task(identifier: str, project_id: str | None = None):
    return public_task(await manager.cancel(identifier, project_id))


@router.post("/tasks/{identifier}/observe", status_code=202, dependencies=[Depends(local_mutation)])
async def resume_observing(identifier: str, project_id: str | None = None):
    return public_task(await manager.resume_observing(identifier, project_id))


@router.post('/tasks/{identifier}/refresh-media', dependencies=[Depends(local_mutation)])
async def refresh_task_media(identifier: str, project_id: str | None = None):
    return public_task(await manager.refresh_media(identifier, project_id))


@router.get("/tasks/{identifier}/export")
async def export_task(identifier: str, project_id: str | None = None, format: Literal["md", "json"] = "md"):
    task = public_task(manager.get(identifier, project_id))
    if format == "json":
        content = json.dumps(task, ensure_ascii=False, indent=2)
    else:
        content = "\n\n".join(["# Muse 任务记录", "任务：" + task["prompt"], "状态：" + task["status"], "来源：" + (task["thread_url"] or "尚未确认会话"),
                                "请求记录：" + task["request_id"], task["response"] or "尚无已读取回复。", "> " + task.get("verification", VERIFICATION), task.get("error") or ""])
    return Response(content, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="ModelDesk-muse-{task["id"]}.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
