"""Allowlisted Feishu control of the existing group runtime and verified delivery."""
import asyncio
import copy
import json
import os
import re
import sqlite3
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .feishu_sdk import FeishuAPIError, FeishuSDK, safe_error
from .feishu_store import ConfigInput, FeishuStore, ROOT, now
from .task_context import feishu_task_ancestors
from . import workbench


CONTINUE_USAGE = "用 /modeldesk status 查看最近任务 ID，然后发送 /modeldesk continue 完整任务ID 修改要求，也可回复原任务回执。"
TASK_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
TASK_ID_LIKE = re.compile(r"(?:[a-z0-9]{8}(?:-[a-z0-9]{4}){3}(?:-[a-z0-9]*)?|[0-9a-f]{32})", re.IGNORECASE)
QUOTED_SELECTOR = re.compile(r'^(?:"([^"]+)"|\'([^\']+)\'|「([^」]+)」|“([^”]+)”)\s*(.*)$', re.DOTALL)


def list_all_rooms():
    from .groups import store as group_store
    from .projects import store as project_store
    registry = project_store.read_registry()
    project_names = {pid: item.get("name") or pid for pid, item in registry.items()}
    raw_rooms = group_store._rooms()
    rooms = []
    for room in raw_rooms:
        project_id = room.get("project_id")
        if project_id is not None and project_id not in project_names:
            continue
        rooms.append({
            "id": room["id"],
            "project_id": project_id,
            "project_name": project_names.get(project_id, "独立项目") if project_id else "独立项目",
            "title": room["title"],
            "member_count": len((room.get("team_snapshot") or {}).get("members") or []),
            "updated_at": room.get("updated_at"),
        })
    return rooms


def _format_room_disambiguation(selector, matches, rooms):
    index_map = {room["id"]: idx for idx, room in enumerate(rooms, 1)}
    lines = [f"找到多个匹配「{selector}」的项目群，请改用序号或完整名称："]
    for room in matches[:10]:
        lines.append(f"{index_map.get(room['id'], '?')}. {room['project_name']} · {room['title']}")
    return "\n".join(lines)


def resolve_room_selector(selector, rooms):
    cleaned = (selector or "").strip().lstrip("#").strip()
    if not cleaned:
        return None, "请指定项目群序号或名称；可先发送 /modeldesk groups 查看所有项目群。"
    if not rooms:
        return None, "当前 ModelDesk 中还没有可用的项目群。"
    if cleaned.isdigit():
        index = int(cleaned)
        if 1 <= index <= len(rooms):
            return rooms[index - 1], None
    folded = cleaned.casefold()
    exact_id = [room for room in rooms if room["id"].casefold() == folded]
    if len(exact_id) == 1:
        return exact_id[0], None
    exact_name = [
        room for room in rooms
        if folded in {
            room["title"].casefold(),
            f"{room['project_name']}/{room['title']}".casefold(),
            f"{room['project_name']} · {room['title']}".casefold(),
        }
    ]
    if len(exact_name) == 1:
        return exact_name[0], None
    if len(exact_name) > 1:
        return None, _format_room_disambiguation(cleaned, exact_name, rooms)
    partial = [
        room for room in rooms
        if folded in room["title"].casefold() or folded in f"{room['project_name']} {room['title']}".casefold()
    ]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, _format_room_disambiguation(cleaned, partial, rooms)
    return None, f"未找到匹配「{cleaned}」的项目群。请发送 /modeldesk groups 查看可用序号和名称。"


def parse_to_command(prompt, rooms):
    text = (prompt or "").strip()
    if not text:
        return None, "", "请填写目标项目群和任务内容，例如：/modeldesk to 序号或群名 任务内容"
    quoted = QUOTED_SELECTOR.match(text)
    if quoted:
        selector = next(group for group in quoted.groups()[:4] if group is not None)
        task_prompt = (quoted.group(5) or "").strip()
        chosen, error = resolve_room_selector(selector, rooms)
        if not chosen:
            return None, "", error
        if not task_prompt:
            return None, "", "请在项目群名称后填写任务内容，例如：/modeldesk to 序号或群名 任务内容"
        return chosen, task_prompt, None
    first_token, *rest = text.split(maxsplit=1)
    first_rest = rest[0].strip() if rest else ""
    stripped_first = first_token.lstrip("#")
    if (stripped_first.isdigit() and 1 <= int(stripped_first) <= len(rooms)) or TASK_ID.fullmatch(first_token):
        chosen, error = resolve_room_selector(first_token, rooms)
        if not chosen:
            return None, "", error
        if not first_rest:
            return None, "", "请在项目群序号或 ID 后填写任务内容，例如：/modeldesk to 1 任务内容"
        return chosen, first_rest, None
    candidates = []
    for room in rooms:
        for label in (room["title"], f"{room['project_name']}/{room['title']}", f"{room['project_name']} · {room['title']}"):
            if label:
                candidates.append((label, room))
    folded_text = text.casefold()
    matched_label = None
    matched_rooms = {}
    for label, room in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if matched_label is not None and len(label) < len(matched_label):
            break
        if folded_text.startswith(label.casefold()) and (len(text) == len(label) or text[len(label)].isspace()):
            if matched_label is None or label.casefold() == matched_label.casefold():
                matched_label = label
                matched_rooms[room["id"]] = room
    if matched_label is not None:
        if len(matched_rooms) > 1:
            return None, "", _format_room_disambiguation(matched_label, list(matched_rooms.values()), rooms)
        chosen = next(iter(matched_rooms.values()))
        task_prompt = text[len(matched_label):].strip()
        if not task_prompt:
            return None, "", "请在项目群名称后填写任务内容，例如：/modeldesk to 序号或群名 任务内容"
        return chosen, task_prompt, None
    chosen, error = resolve_room_selector(first_token, rooms)
    if not chosen:
        return None, "", error
    if not first_rest:
        return None, "", "请在项目群名称后填写任务内容，例如：/modeldesk to 序号或群名 任务内容"
    return chosen, first_rest, None


def match_at_room(prompt, rooms):
    if not prompt.startswith("@"):
        return None, prompt
    body = prompt[1:]
    candidates = []
    for room in rooms:
        for label in (room["title"], f"{room['project_name']}/{room['title']}", f"{room['project_name']} · {room['title']}"):
            if label:
                candidates.append((label, room))
    matched_len = 0
    matched_rooms = {}
    folded_body = body.casefold()
    for label, room in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if len(label) < matched_len:
            break
        if folded_body.startswith(label.casefold()) and len(body) > len(label) and body[len(label)].isspace():
            rest = body[len(label):].strip()
            if rest:
                matched_len = len(label)
                matched_rooms[room["id"]] = (room, rest)
    if len(matched_rooms) == 1:
        chosen, stripped = next(iter(matched_rooms.values()))
        return chosen, stripped
    return None, prompt


class FeishuController:
    def __init__(self, store, jobs, *, sdk_factory=FeishuSDK):
        self.store, self.jobs, self.sdk_factory = store, jobs, sdk_factory
        self.task = None
        self.worker = None
        self.reader = None
        self.lock = asyncio.Lock()
        self.config_lock = asyncio.Lock()
        self._pushed_keys = set()
        self.status = {"state": "not_configured", "reason": "请先配置飞书企业自建应用", "last_event_at": None, "last_connected_at": None}

    def public(self):
        try:
            config = self.store.public_config()
        except (HTTPException, OSError):
            return {"config": {"enabled": False, "app_id": "", "has_app_secret": False, "bindings": [], "updated_at": None},
                    "status": {**self.status, "state": "error", "reason": "飞书本地配置无法读取，原文件已保留；请修复 data/feishu/config.json 后重新保存设置"},
                    "recent": [], "observed_chats": []}
        status = copy.deepcopy(self.status)
        if not config["app_id"] or not config["has_app_secret"]:
            status.update(state="not_configured", reason="尚未配置飞书应用凭据；可先按接入指南创建应用")
        elif not config["enabled"]:
            status.update(state="disabled", reason="连接已停用，已有 ModelDesk 任务会继续运行")
        try:
            rows = [row for row in self.store.events() if row["app_id"] == config["app_id"]]
            recent = []
            for row in rows[-20:][::-1]:
                item = {key: row.get(key) for key in ["id", "message_id", "chat_id", "state", "reason", "created_at", "updated_at"]}
                job = self.jobs.jobs.get(row.get("job_id"))
                # Status/help/cancel messages reserve an ingress identity but
                # never create that candidate job. Do not present it as real.
                item["job_id"] = job["id"] if job and self.belongs(job, row) else None
                recent.append(item)
            observed = self.store.observed(config["app_id"])
        except (HTTPException, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            recent, observed = [], []
            status.update(state="error", reason="飞书本地收发记录无法读取，请在本机检查；应用配置和其他项目仍可使用")
        return {"config": config, "status": status, "recent": recent, "observed_chats": observed}

    async def start(self):
        try:
            await self.restart_worker()
        except Exception:
            # An optional channel's corrupt credentials or worker startup must
            # never take down ModelDesk's core API and existing project jobs.
            self.status.update(state="error", reason="飞书连接未启动，请检查本机配置；其他 ModelDesk 功能仍可使用")
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.loop())

    async def stop_worker(self):
        worker, self.worker = self.worker, None
        if worker and worker.returncode is None:
            worker.terminate()
            try:
                await asyncio.wait_for(worker.wait(), 3)
            except asyncio.TimeoutError:
                worker.kill()
                await worker.wait()
        reader, self.reader = self.reader, None
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def restart_worker(self):
        await self.stop_worker()
        config = self.store.config()
        if not config["enabled"] or not config["app_id"] or not config["app_secret"]:
            return
        self.status.update(state="connecting", reason="正在建立飞书官方长连接；无须公网地址")
        self.worker = await asyncio.create_subprocess_exec(sys.executable, "-m", "backend.feishu_worker", str(self.store.directory), str(os.getpid()),
                                                          cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.reader = asyncio.create_task(self.read_worker(self.worker))

    async def read_worker(self, worker):
        async for raw in worker.stdout:
            try:
                state = json.loads(raw).get("state")
            except (ValueError, AttributeError):
                continue
            if state not in {"connected", "connecting", "error", "event"}:
                continue
            if state == "event":
                self.status["last_event_at"] = now()
                continue
            reasons = {"connected": "飞书长连接在线，等待已绑定会话中的任务或控制消息", "connecting": "正在连接或重连飞书",
                       "error": "飞书连接失败，请检查应用凭据、网络及长连接配置"}
            self.status.update(state=state, reason=reasons[state])
            if state == "connected":
                self.status["last_connected_at"] = now()
        if self.worker is worker:
            self.status.update(state="reconnecting", reason="飞书长连接已退出，正在等待自动重连；已有任务和消息回执保留")

    async def shutdown(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.stop_worker()

    async def loop(self):
        while True:
            try:
                await self.ensure_worker()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status.update(state="error", reason="飞书本地记录暂时无法处理，请检查设置")
            await asyncio.sleep(2)

    async def ensure_worker(self):
        if self.worker and self.worker.returncode is None:
            return
        async with self.config_lock:
            config = self.store.config()
            if not config["enabled"] or not config["app_id"] or not config["app_secret"]:
                return
            from .feishu_supervision import reserve
            reservation = reserve(self.store.directory, config["revision"])
            self.status.update(restart_attempts=reservation["attempts"], next_restart_at=reservation["next_at"])
            if not reservation["allowed"]:
                self.status.update(state="reconnecting", reason="飞书接收进程已退出，正在等待自动重连；已有任务与收发回执保留")
                return
            try:
                await self.restart_worker()
            except Exception:
                self.status.update(state="error", reason="飞书接收进程暂未恢复，将按退避间隔重试；不会重复派发或发送已保留的消息")

    async def tick(self):
        async with self.lock:
            for record in self.store.events(["received", "dispatching", "monitoring", "attention", "rejected"]):
                if record['state'] == 'rejected' and 'rejected' in record['notifications']:
                    continue
                if not self.store.current(record):
                    self.store.update(record, state="needs_attention", reason="飞书应用或绑定配置已改变；历史任务不会向新配置自动回传")
                    continue
                try:
                    if record['state'] == 'rejected':
                        await self.reply_once(record, 'rejected', record['reason'])
                        self.store.update(record, notifications=[*record['notifications'], 'rejected'])
                        continue
                    if record["state"] in {"received", "dispatching"}:
                        await self.accept(record)
                    if record["state"] in {"monitoring", "attention"}:
                        await self.deliver(record)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.store.update(record, state="needs_attention", reason=safe_error(error))
            await self.push_completed_group_jobs()

    def active_target(self, record):
        binding = record["binding"]
        if binding.get("allow_all_groups"):
            route = self.store.get_chat_route(record["app_id"], record["chat_id"], record["config_revision"])
            if route:
                from .groups import store as group_store
                try:
                    group_store.get(route["room_id"], route["project_id"])
                    return route["project_id"], route["room_id"]
                except HTTPException:
                    pass
        return binding["project_id"], binding["room_id"]

    async def accept(self, record):
        from .groups import MessageRequest, submit_message
        command = record["command"]
        if record.get("input_mode") == "bootstrap":
            await self.run_bootstrap(record)
            return
        if record["state"] == "dispatching":
            existing = self.jobs.jobs.get(record["job_id"])
            if existing:
                if not self.belongs(existing, record) or existing.get("external_source", {}).get("message_id") != record["message_id"]:
                    raise RuntimeError("job identity conflict")
                self.store.update(record, state="monitoring", reason="已恢复已有任务记录，没有重复创建")
                return
            self.store.update(record, state="needs_attention", reason="上次任务创建结果未确认；不会自动重复创建，请检查本机任务")
            return
        binding = record["binding"]
        parts = command.split(maxsplit=1)
        action, prompt = (parts + ["", ""])[:2]
        action, prompt = action.casefold(), prompt.strip()
        if action in {"unlink", "解除绑定"}:
            config = self.store.config()
            owners = config.get("owner_sender_ids") or []
            if record["sender_id"] not in owners:
                await self.reply_once(record, "unlink", "只有配置的管理员 Open ID 可以使用 /modeldesk unlink。")
                self.store.update(record, state="delivered", reason="解绑指令被拒绝：发起人不是管理员")
                return
            if self.store.remove_binding(config, record["chat_id"]):
                await self.reply_once(record, "unlink", "已解除当前会话的绑定。项目、团队和项目群保持不变；可重新用 /modeldesk setup 开通。", relaxed=True)
                self.store.update(record, state="delivered", reason="解绑指令已处理")
            else:
                await self.reply_once(record, "unlink", "当前会话没有绑定，无需解绑。")
                self.store.update(record, state="delivered", reason="解绑指令已处理（无绑定）")
            return
        if action in {"groups", "群列表"}:
            if binding.get("allow_all_groups"):
                rooms = list_all_rooms()
                active_project_id, active_room_id = self.active_target(record)
                if not rooms:
                    text = "当前 ModelDesk 中暂无可用项目群。"
                else:
                    lines = ["可用项目群列表（★ 为当前默认执行群）："]
                    for idx, room in enumerate(rooms, 1):
                        mark = "★ " if room["id"] == active_room_id and room["project_id"] == active_project_id else ""
                        lines.append(f"{idx}. {mark}{room['project_name']} · {room['title']}")
                    lines.append("")
                    lines.append("切换默认群：/modeldesk use 序号或群名")
                    lines.append("单次指定群：/modeldesk to 序号或群名 任务内容")
                    text = "\n".join(lines)
            else:
                room_title = binding["room_id"]
                project_name = "独立项目"
                try:
                    from .groups import store as group_store
                    room = group_store.get(binding["room_id"], binding["project_id"])
                    room_title = room.get("title") or room_title
                except Exception:
                    pass
                if binding.get("project_id"):
                    try:
                        from .projects import store as project_store
                        reg = project_store.read_registry()
                        project_name = (reg.get(binding["project_id"]) or {}).get("name") or binding["project_id"]
                    except Exception:
                        project_name = binding["project_id"]
                text = (
                    f"当前飞书会话仅绑定到以下项目群：\n1. ★ {project_name} · {room_title}\n\n"
                    "如需在飞书中查看与切换其他项目群，请在 ModelDesk 群聊顶部「飞书」或「Agent 与连接 → 飞书远程」中开启「允许此会话访问与切换全部项目群」。"
                )
            await self.reply_once(record, "groups", text)
            self.store.update(record, state="delivered", reason="群列表已回传")
            return
        if action in {"use", "切换群", "切换项目群"}:
            if not binding.get("allow_all_groups"):
                await self.reply_once(
                    record,
                    "use",
                    "当前飞书会话未开启「访问全部项目群」权限。请在 ModelDesk 群聊顶部「飞书」或「Agent 与连接 → 飞书远程」中开启后再切换。",
                )
                self.store.update(record, state="delivered", reason="未开启访问全部项目群权限")
                return
            if not prompt:
                await self.reply_once(record, "use", "请指定要切换的项目群序号或名称，例如：/modeldesk use 1\n可先发送 /modeldesk groups 查看群列表。")
                self.store.update(record, state="delivered", reason="切换群缺少目标参数")
                return
            chosen, error = resolve_room_selector(prompt, list_all_rooms())
            if not chosen:
                await self.reply_once(record, "use", error)
                self.store.update(record, state="delivered", reason="切换群未匹配目标")
                return
            self.store.set_chat_route(record["app_id"], record["chat_id"], record["config_revision"], chosen["project_id"], chosen["id"])
            await self.reply_once(
                record,
                "use",
                f"已切换默认项目群为：{chosen['project_name']} · {chosen['title']}\n接下来发送的任务都将交给该群执行。",
            )
            self.store.update(record, state="delivered", reason=f"已切换默认群为 {chosen['title']}")
            return
        if command in {"status", "状态"}:
            if binding.get("allow_all_groups"):
                related = [job for job in self.jobs.jobs.values() if self.chat_owns(job, record)]
                latest = sorted(related, key=lambda job: job.get("created_at", ""))[-5:]
                room_titles = {}
                try:
                    room_titles = {room["id"]: f"{room['project_name']} · {room['title']}" for room in list_all_rooms()}
                except Exception:
                    room_titles = {}
                receipts = {row["job_id"]: row for row in self.store.events() if row["state"] not in {"received", "dispatching"}}
                if not latest:
                    text = "当前飞书会话暂无任务。"
                else:
                    lines = []
                    for job in latest:
                        room_label = room_titles.get(job.get("group_room_id"))
                        header = f"{job['id']}（{room_label}）" if room_label else job["id"]
                        lines.append(
                            header + "\n执行：" + (job.get("autonomy") or {}).get("reason", job["status"])
                            + "\n飞书回传：" + receipts.get(job["id"], {}).get("reason", "等待回传")
                        )
                    text = "\n".join(lines)
            else:
                related = [job for job in self.jobs.jobs.values() if self.belongs(job, record)]
                latest = sorted(related, key=lambda job: job.get("created_at", ""))[-3:]
                receipts = {row["job_id"]: row for row in self.store.events() if row["state"] not in {"received", "dispatching"}}
                text = "当前绑定群暂无飞书任务。" if not latest else "\n".join(
                    job["id"] + "\n执行：" + (job.get("autonomy") or {}).get("reason", job["status"])
                    + "\n飞书回传：" + receipts.get(job["id"], {}).get("reason", "等待回传") for job in latest)
            await self.reply_once(record, "status", text)
            self.store.update(record, state="delivered", reason="状态已回传")
            return
        if action in {"cancel", "停止"} and prompt:
            target = prompt
            job = self.jobs.jobs.get(target)
            if not job or not self.chat_owns(job, record):
                await self.reply_once(record, "cancel", "当前飞书聊天没有此任务，请使用 /modeldesk status 查看任务 ID。")
            else:
                await self.jobs.cancel(target)
                await self.reply_once(record, "cancel", "已停止任务 " + target + "；已有成果保留。")
            self.store.update(record, state="delivered", reason="停止指令已处理")
            return
        explicit_target = None
        if action == "to":
            if not binding.get("allow_all_groups"):
                await self.reply_once(
                    record,
                    "to",
                    "当前飞书会话未开启「访问全部项目群」权限。请在 ModelDesk 群聊顶部「飞书」或「Agent 与连接 → 飞书远程」中开启后再指定其他项目群。",
                )
                self.store.update(record, state="delivered", reason="未开启访问全部项目群权限")
                return
            chosen, task_prompt, error = parse_to_command(prompt, list_all_rooms())
            if error:
                await self.reply_once(record, "to", error)
                self.store.update(record, state="delivered", reason="指定群指令未匹配目标")
                return
            explicit_target = (chosen["project_id"], chosen["id"])
            prompt = task_prompt
            action = "task"
        if action not in {"task", "continue"} or not prompt:
            direct_help = ('直接描述需求可开始新任务。回复任务回执，或用“继续：完整任务ID 修改要求”继续该任务。\n'
                           if binding.get('natural_language_tasks') else '')
            multi_help = ("\n/modeldesk groups（查看全部项目群）\n/modeldesk use 序号或群名（切换默认群）\n/modeldesk to 序号或群名 任务内容（单次指定群）"
                          if binding.get("allow_all_groups") else "")
            await self.reply_once(record, "usage", direct_help + "使用方法：\n/modeldesk task 完整任务（新任务）\n/modeldesk continue 完整任务ID 修改要求\n回复任务回执：/modeldesk continue 修改要求\n/modeldesk status\n/modeldesk cancel 完整任务ID" + multi_help)
            self.store.update(record, state="delivered", reason="已回传指令用法，没有创建任务")
            return
        job = self.jobs.jobs.get(record["job_id"])
        if job:
            if not self.belongs(job, record) or job.get("external_source", {}).get("message_id") != record["message_id"]:
                raise RuntimeError("job identity conflict")
            self.store.update(record, state="monitoring", reason="已恢复已有任务记录，没有重复创建")
            return
        if action == "task" and explicit_target is None and binding.get("allow_all_groups") and prompt.startswith("@"):
            chosen, stripped = match_at_room(prompt, list_all_rooms())
            if chosen:
                explicit_target = (chosen["project_id"], chosen["id"])
                prompt = stripped
        target = None
        if action == "continue":
            try:
                target, prompt = self.continuation_target(record, prompt)
            except HTTPException as error:
                await self.reply_once(record, "continue_target", f"{error.detail}\n{CONTINUE_USAGE}")
                self.store.update(record, state="delivered", reason=error.detail)
                return
        if sum(job.get("status") not in workbench.TERMINAL for job in self.jobs.jobs.values()) >= 4:
            return
        if target:
            parent_job = self.jobs.jobs[target]
            target_project_id, target_room_id = parent_job["project_id"], parent_job["group_room_id"]
        elif explicit_target is not None:
            target_project_id, target_room_id = explicit_target
        else:
            target_project_id, target_room_id = self.active_target(record)
        # Commit the deterministic job ID and resolved room target before any awaited
        # validation or model dispatch. Restart reconciles an existing job, never guesses absence.
        self.store.update(
            record,
            state="dispatching",
            target_project_id=target_project_id,
            target_room_id=target_room_id,
            reason="正在创建已授权的后台任务",
        )
        source = {"platform": "feishu", "app_id": record["app_id"], "chat_id": record["chat_id"],
                  "message_id": record["message_id"], "sender_id": record["sender_id"], "config_revision": record["config_revision"]}
        if target:
            source["parent_job_id"] = target

        def assert_admission():
            self.assert_current(record)
            if target:
                self.checked_continuation_job(record, target)

        await submit_message(target_room_id, MessageRequest(prompt=prompt, context_mode="continue" if target else "fresh",
                             delivery_mode="auto", background_mode=True),
                             target_project_id, native_allowed=binding["allow_native"], job_id=record["job_id"], external_source=source,
                             admission_check=assert_admission)
        self.store.update(record, state="monitoring", reason="任务已交给 ModelDesk 后台执行")
        try:
            from .platform_support import machine_short
            room_line = ""
            if binding.get("allow_all_groups"):
                try:
                    matched = next((room for room in list_all_rooms() if room["id"] == target_room_id and room["project_id"] == target_project_id), None)
                    if matched:
                        room_line = f"\n执行群：{matched['project_name']} · {matched['title']}"
                except Exception:
                    pass
            await self.reply_once(record, "accepted", "已收到任务，ModelDesk 正在后台协作。" + room_line
                                  + "\n任务 ID：" + record["job_id"]
                                  + "\n可回复这条回执并发送 /modeldesk continue 修改要求。"
                                  + f"\n完成或需要处理时会回传到本条消息；{machine_short()} 和 ModelDesk 服务需保持运行。")
        except Exception:
            # An uncertain acknowledgement must not lose a successfully
            # created job or prevent its separate completion notification.
            self.store.update(record, reason="任务已创建；接收确认消息未确认，不会重复发送，仍会等待最终成果")

    def continuation_target(self, record, prompt):
        quoted = self.store.task_for_reply(record) if "reply_message_id" in record else None
        first, *rest = prompt.split(maxsplit=1)
        if TASK_ID.fullmatch(first):
            if not rest:
                raise HTTPException(422, "请在完整任务 ID 后写上修改要求。")
            target, prompt = first.lower(), rest[0]
            if "reply_message_id" in record and quoted != target:
                raise HTTPException(409, "回复的消息与所填任务 ID 无法对应。请直接回复要修改的成果，或另发一条带任务 ID 的消息。")
        elif TASK_ID_LIKE.fullmatch(first):
            raise HTTPException(422, "任务 ID 格式无效，请复制回执里的完整 ID。")
        elif "reply_message_id" in record:
            target = quoted
        else:
            owned = {job["id"]: job for job in self.jobs.jobs.values() if self.can_continue(job, record)}
            prior = {job["external_source"].get("parent_job_id") for job in owned.values()}
            heads = owned.keys() - prior
            if len(heads) > 1:
                raise HTTPException(409, "这里有多个可选的任务，请明确选择要继续的任务。")
            target = next(iter(heads), None)
        self.checked_continuation_job(record, target)
        return target, prompt

    def can_continue(self, job, record):
        source = job.get("external_source") or {}
        binding = record["binding"]
        if not (source.get("platform") == "feishu" and source.get("app_id") == record["app_id"]
                and source.get("chat_id") == record["chat_id"] and source.get("config_revision") == record["config_revision"]
                and job.get("mode") == "group" and source.get("sender_id") == record["sender_id"]):
            return False
        if binding.get("allow_all_groups"):
            return bool(job.get("group_room_id")) and job.get("conversation_id") == job.get("group_room_id")
        return (job.get("project_id") == binding["project_id"] and job.get("group_room_id") == binding["room_id"]
                and job.get("conversation_id") == binding["room_id"])

    def checked_continuation_job(self, record, target):
        job = self.jobs.jobs.get(target)
        if job is None or not self.can_continue(job, record):
            raise HTTPException(404, "找不到你在当前飞书会话发起的对应任务。")
        feishu_task_ancestors(self.jobs.jobs, job)

    def bootstrap_service(self):
        """Construct the bootstrap service; tests patch this for scratch stores."""
        from .feishu_bootstrap import Bootstrap
        return Bootstrap(self.store)

    async def run_bootstrap(self, record):
        """Handle an admitted owner setup/confirm directive end-to-end."""
        from .feishu_bootstrap import BootstrapError
        bootstrap = self.bootstrap_service()
        config = self.store.config()
        try:
            # setup and confirm validate against this revision; executing a
            # plan rotates the revision via the binding write.
            reply, binding = await bootstrap.handle(record, config)
            if reply is None:
                self.store.update(record, state="delivered", reason="指令不在自助开通范围内，未执行")
                return
            if binding:
                # The plan was executed and the config revision rotated. Adopt
                # the new revision and the just-written binding so the receipt
                # reply is validated against the configuration this run made.
                current = self.store.config()
                if current["enabled"] and current["app_id"] == record["app_id"]:
                    self.store.update(record, config_revision=current["revision"], binding=binding)
            await self.reply_once(record, "bootstrap", reply)
            self.store.update(record, state="delivered", reason="自助开通指令已处理")
        except BootstrapError as error:
            try:
                await self.reply_once(record, "bootstrap", str(error))
                self.store.update(record, state="delivered", reason="自助开通指令被拒绝：" + str(error)[:200])
            except Exception:
                self.store.update(record, state="needs_attention", reason="开通结果未能回传；请在本机检查")
        except Exception as error:
            self.store.update(record, state="needs_attention", reason=safe_error(error))

    @staticmethod
    def belongs(job, record):
        source = job.get("external_source") or {}
        binding = record.get("binding") or {}
        expected_project = record["target_project_id"] if "target_project_id" in record else binding.get("project_id")
        expected_room = record.get("target_room_id") or binding.get("room_id")
        return (source.get("platform") == "feishu" and source.get("app_id") == record["app_id"] and source.get("chat_id") == record["chat_id"]
                and source.get("config_revision") == record["config_revision"]
                and job.get("project_id") == expected_project and job.get("group_room_id") == expected_room)

    @staticmethod
    def chat_owns(job, record):
        source = job.get("external_source") or {}
        binding = record.get("binding") or {}
        if not (source.get("platform") == "feishu" and source.get("app_id") == record["app_id"]
                and source.get("chat_id") == record["chat_id"] and source.get("config_revision") == record["config_revision"]):
            return False
        if binding.get("allow_all_groups"):
            return bool(job.get("group_room_id"))
        return FeishuController.belongs(job, record)

    def assert_current(self, record):
        if not self.store.current(record):
            raise FeishuAPIError("飞书连接已停用或授权配置已改变，未继续派发或回传")

    async def operation(self, record, key, function, *args, relaxed=False):
        """Reserve one idempotent outbound send.

        relaxed skips the config-revision guard for receipts whose own action
        legitimately rotated the configuration (binding written or removed).
        The message dedupe and sent-uncertain accounting still apply.
        """
        if not relaxed:
            self.assert_current(record)
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu-out:" + record["id"] + ":" + key))
        reserved, receipt = self.store.reserve_outgoing(identifier, record["id"], {"kind": key})
        if not reserved:
            if receipt["state"] == "sent":
                return receipt["response_id"]
            raise RuntimeError("outbound result unconfirmed")
        try:
            def dispatch():
                if not relaxed:
                    self.assert_current(record)
                return function(*args, identifier)
            result = await asyncio.to_thread(dispatch)
        except asyncio.CancelledError:
            # The network thread may have sent successfully. Its reservation is
            # left uncertain across shutdown, so startup cannot send it again.
            raise
        except Exception as error:
            self.store.finish_outgoing(identifier, state="uncertain", reason=safe_error(error))
            raise
        self.store.finish_outgoing(identifier, state="sent", response_id=result)
        return result

    async def reply_once(self, record, key, text, relaxed=False):
        sdk = self.sdk_factory(self.store.config())
        return await self.operation(record, key, lambda value, dispatch: sdk.reply(record["message_id"], "text", {"text": value}, dispatch), text, relaxed=relaxed)

    async def deliver(self, record):
        from .autonomy import completion_problem
        from .feishu_assets import verified_artifacts, verified_text_attachment
        job = self.jobs.jobs.get(record["job_id"])
        if not job or not self.belongs(job, record):
            self.store.update(record, state="needs_attention", reason="找不到匹配的原始项目任务，请在本机检查")
            return
        meta = job.get("autonomy") or {}
        state, notification = meta.get("state"), meta.get("notification_id")
        if state not in {"complete", "needs_attention", "cancelled"} or not notification or notification in record["notifications"]:
            return
        delivery = (job.get("result") or {}).get("delivery") or {}
        artifacts = []
        if state == "complete":
            problem = completion_problem(job)
            if problem:
                self.store.update(record, state="needs_attention", reason=problem)
                try:
                    await self.reply_once(record, notification + ":integrity", "本机任务已结束，但成果完整性校验未通过，尚未回传成果。\n" + problem)
                except Exception:
                    pass
                return
            try:
                artifacts = await asyncio.to_thread(verified_artifacts, job, delivery)
            except FeishuAPIError as error:
                self.store.update(record, state="needs_attention", reason=str(error))
                try:
                    await self.reply_once(record, notification + ":preflight", "成果已保存，但飞书文件未回传。\n" + str(error))
                except Exception:
                    pass
                return
        partial = state == "needs_attention" and delivery.get("status") in {"partial", "blocked"}
        if not artifacts and (state == "complete" or partial):
            saved_text = verified_text_attachment(job, delivery, partial=partial)
            if saved_text is not None:
                artifacts.append(saved_text)
        title = {"complete": "任务已完成", "needs_attention": "任务需要你处理", "cancelled": "任务已停止"}[state]
        text = title + "\n任务 ID：" + job["id"] + "\n" + meta.get("reason", "")
        if artifacts:
            text += ("\n附上已保存的部分成果，任务仍需要处理。" if partial
                     else f"\n将回传完整正文和 {len(artifacts)} 份成果附件。")
        await self.reply_once(record, notification + ":summary", text)
        if state == "complete":
            content = delivery["content"]
            chunks = [content[index:index + 4500] for index in range(0, len(content), 4500)]
            for index, chunk in enumerate(chunks):
                await self.reply_once(record, notification + f":body:{index}", f"完整正文 {index + 1}/{len(chunks)}\n" + chunk)
        if artifacts:
            sdk = self.sdk_factory(self.store.config())
            for key, manifest, raw in artifacts:
                file_key = await self.operation(record, notification + ":upload:" + key,
                    lambda data, dispatch: sdk.upload(manifest, data), raw)
                await self.operation(record, notification + ":file:" + key,
                    lambda key, dispatch: sdk.reply(record["message_id"], "file", {"file_key": key}, dispatch), file_key)
        self.store.update(record, state="attention" if state == "needs_attention" else "delivered",
                          reason=title + "，已回传飞书", notifications=[*record["notifications"], notification])

    async def _push_operation(self, identifier, event_id, metadata, is_authorized, binding, function):
        if not is_authorized(binding):
            raise FeishuAPIError("飞书连接已停用或授权配置已改变，未继续推送")
        reserved, receipt = self.store.reserve_outgoing(identifier, event_id, metadata)
        if not reserved:
            if receipt["state"] == "sent":
                return receipt["response_id"]
            raise RuntimeError("outbound push result unconfirmed")
        try:
            def dispatch():
                if not is_authorized(binding):
                    raise FeishuAPIError("飞书连接已停用或授权配置已改变，未继续推送")
                return function(identifier)
            result = await asyncio.to_thread(dispatch)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.store.finish_outgoing(identifier, state="uncertain", reason=safe_error(error))
            raise
        self.store.finish_outgoing(identifier, state="sent", response_id=result)
        return result

    async def push_completed_group_jobs(self):
        try:
            config = self.store.config()
        except HTTPException:
            return
        if not config["enabled"] or not config.get("app_id") or not config.get("app_secret"):
            return
        push_bindings = [item for item in config["bindings"] if item.get("push_group_results")]
        if not push_bindings:
            return
        from .autonomy import completion_problem
        from .feishu_assets import verified_artifacts
        app_id = config["app_id"]
        expected_revision = config["revision"]
        cutoff = config.get("updated_at") or ""

        def push_still_authorized(binding_snapshot):
            live = self.store.config()
            return (
                live["enabled"]
                and live["app_id"] == app_id
                and live["revision"] == expected_revision
                and binding_snapshot in live["bindings"]
            )

        room_titles = {}
        try:
            room_titles = {room["id"]: room["title"] for room in list_all_rooms()}
        except Exception:
            room_titles = {}
        jobs_snapshot = sorted(self.jobs.jobs.values(), key=lambda item: item.get("created_at") or "")
        for binding in push_bindings:
            if not push_still_authorized(binding):
                return
            sdk = self.sdk_factory(config)
            for job in jobs_snapshot:
                if not push_still_authorized(binding):
                    return
                if (job.get("mode") != "group"
                        or job.get("project_id") != binding["project_id"]
                        or job.get("group_room_id") != binding["room_id"]):
                    continue
                if (job.get("external_source") or {}).get("chat_id") == binding["chat_id"]:
                    continue
                if (job.get("created_at") or "") < cutoff:
                    continue
                autonomy = job.get("autonomy") or {}
                delivery = (job.get("result") or {}).get("delivery") or {}
                state = autonomy.get("state")
                notification = autonomy.get("notification_id")
                if state in {"complete", "needs_attention"} and notification:
                    pass
                elif job.get("status") == "complete" and delivery.get("status") == "complete":
                    state = "complete"
                    notification = "complete:" + job["id"]
                else:
                    continue
                cache_key = (app_id, expected_revision, binding["chat_id"], job["id"], notification)
                if cache_key in self._pushed_keys:
                    continue
                event_id = "push:" + job["id"]
                base_key = f"modeldesk:feishu-push:{app_id}:{binding['chat_id']}:{job['id']}:{notification}"
                summary_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base_key + ":summary"))
                try:
                    artifacts = []
                    artifact_note = ""
                    integrity_problem = None
                    if state == "complete":
                        integrity_problem = completion_problem(job)
                        if not integrity_problem:
                            try:
                                artifacts = await asyncio.to_thread(verified_artifacts, job, delivery)
                            except FeishuAPIError as error:
                                artifact_note = f"成果文件未自动推送：{error}"
                    room_title = room_titles.get(binding["room_id"])
                    if integrity_problem:
                        header = f"项目群「{room_title}」任务已结束，但成果完整性校验未通过" if room_title else "项目群任务已结束，但成果完整性校验未通过"
                        summary_text = f"{header}\n任务 ID：{job['id']}\n{integrity_problem}\n未自动推送未校验附件，请在本机 ModelDesk 检查。"
                    else:
                        if state == "complete":
                            header = f"项目群「{room_title}」任务已完成" if room_title else "项目群任务已完成"
                        else:
                            header = f"项目群「{room_title}」任务需要你处理" if room_title else "项目群任务需要你处理"
                        lines = [header, f"任务 ID：{job['id']}"]
                        delivery_title = (delivery.get("title") or "").strip()
                        if delivery_title:
                            lines.append(f"成果：{delivery_title}")
                        reason = (autonomy.get("reason") or "").strip()
                        if reason:
                            lines.append(reason)
                        content = (delivery.get("content") or "").strip()
                        if state == "complete" and content:
                            excerpt = content if len(content) <= 1500 else content[:1500].rstrip() + "\n…（完整正文请在 ModelDesk 查看）"
                            lines.append("\n" + excerpt)
                        if artifacts:
                            lines.append(f"\n将推送 {len(artifacts)} 份已校验的成果文件。")
                        elif artifact_note:
                            lines.append("\n" + artifact_note)
                        summary_text = "\n".join(lines)
                    await self._push_operation(
                        summary_id,
                        event_id,
                        {"kind": f"push:{notification}:summary", "chat_id": binding["chat_id"], "job_id": job["id"]},
                        push_still_authorized,
                        binding,
                        lambda dispatch, text=summary_text, chat=binding["chat_id"]: sdk.send(chat, "text", {"text": text}, dispatch),
                    )
                    if not integrity_problem and artifacts:
                        for art_key, manifest, raw in artifacts:
                            if not push_still_authorized(binding):
                                return
                            upload_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base_key + ":upload:" + art_key))
                            file_key = await self._push_operation(
                                upload_id,
                                event_id,
                                {"kind": f"push:{notification}:upload:{art_key}", "chat_id": binding["chat_id"], "job_id": job["id"]},
                                push_still_authorized,
                                binding,
                                lambda dispatch, m=manifest, data=raw: sdk.upload(m, data),
                            )
                            if not push_still_authorized(binding):
                                return
                            file_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base_key + ":file:" + art_key))
                            await self._push_operation(
                                file_id,
                                event_id,
                                {"kind": f"push:{notification}:file:{art_key}", "chat_id": binding["chat_id"], "job_id": job["id"]},
                                push_still_authorized,
                                binding,
                                lambda dispatch, fk=file_key, chat=binding["chat_id"]: sdk.send(chat, "file", {"file_key": fk}, dispatch),
                            )
                    self._pushed_keys.add(cache_key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if not push_still_authorized(binding):
                        return
                    continue


store = FeishuStore()
controller = FeishuController(store, workbench.manager)


def local_admin(request: Request):
    from .remote_access import require_local
    require_local(request)
    if request.headers.get("x-modeldesk-remote"):
        from .platform_support import machine_label
        raise HTTPException(403, f"飞书凭据和聊天授权只能在{machine_label()}管理")


router = APIRouter(prefix="/api/feishu", tags=["feishu"], dependencies=[Depends(local_admin)])


@router.get("")
async def get_config():
    return controller.public()


@router.put("")
async def configure(body: ConfigInput):
    # Configuration revocation must not queue behind a whole outbound batch.
    # The next operation and the final job-admission check read this revision;
    # an already dispatched HTTP request may still finish, but is not repeated.
    async with controller.config_lock:
        store.save_config(body)
        try:
            await controller.restart_worker()
        except Exception:
            controller.status.update(state="error", reason="配置已保存，但飞书连接未能启动，请检查本机环境后重试")
        return controller.public()


@router.post("/test")
async def test_credentials():
    config = store.config()
    if not config["app_id"] or not config["app_secret"]:
        raise HTTPException(422, "请先保存应用凭据")
    try:
        await asyncio.to_thread(controller.sdk_factory(config).bot_info)
        return {"ok": True, "message": "已通过飞书机器人身份只读验证；消息订阅和实际收发仍需配置后验证，本次没有发送消息"}
    except Exception as error:
        return {"ok": False, "message": safe_error(error)}


@router.get("/chats")
async def chats(page_token: str | None = Query(default=None, max_length=2000)):
    config = store.config()
    if not config["app_id"] or not config["app_secret"]:
        raise HTTPException(422, "请先保存应用凭据")
    try:
        result = await asyncio.to_thread(controller.sdk_factory(config).chats, page_token)
    except Exception as error:
        raise HTTPException(502, safe_error(error)) from None
    existing = {item["chat_id"] for item in result["items"]}
    if not page_token:
        result["items"].extend({"chat_id": item["chat_id"], "name": ("最近收到的私聊" if item["chat_type"] == "p2p" else "最近收到的群聊") + " · " + item["chat_id"]}
                               for item in store.observed(config["app_id"]) if item["chat_id"] not in existing)
    return result


class RoomBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(pattern=r"^oc_[A-Za-z0-9_-]{1,180}$")
    name: str = Field(default="", max_length=100)
    project_id: uuid.UUID | None = None
    allowed_sender_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_native: bool = False
    natural_language_tasks: bool = False
    allow_all_groups: bool = False
    push_group_results: bool = False
    replace_existing: bool = False
    enable_connection: bool = False

    @field_validator("allowed_sender_ids")
    @classmethod
    def senders(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r"ou_[A-Za-z0-9_-]{1,180}", item) for item in values):
            raise ValueError("成员 Open ID 无效或重复")
        return values


def rooms_payload():
    public = controller.public()
    try:
        config = store.config()
        routes = store.active_routes(config["app_id"], config["revision"]) if config.get("app_id") else {}
    except (HTTPException, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        routes = {}
    try:
        rooms = list_all_rooms()
    except Exception:
        rooms = []
    return {**public, "rooms": rooms, "active_routes": routes}


@router.get("/rooms")
async def get_rooms():
    return rooms_payload()


@router.put("/rooms/{room_id}/binding")
async def upsert_room_binding(room_id: uuid.UUID, body: RoomBindingInput):
    async with controller.config_lock:
        before_enabled = store.config().get("enabled", False)
        store.upsert_room_binding(
            str(room_id),
            chat_id=body.chat_id,
            name=body.name,
            project_id=str(body.project_id) if body.project_id else None,
            allowed_sender_ids=body.allowed_sender_ids,
            allow_native=body.allow_native,
            natural_language_tasks=body.natural_language_tasks,
            allow_all_groups=body.allow_all_groups,
            push_group_results=body.push_group_results,
            replace_existing=body.replace_existing,
            enable_connection=body.enable_connection,
        )
        after_enabled = store.config().get("enabled", False)
        worker_running = bool(controller.worker and controller.worker.returncode is None)
        if after_enabled != before_enabled or (after_enabled and not worker_running):
            try:
                await controller.restart_worker()
            except Exception:
                controller.status.update(state="error", reason="配置已保存，但飞书连接未能启动，请检查本机环境后重试")
        return rooms_payload()


@router.delete("/rooms/{room_id}/binding/{chat_id}")
async def delete_room_binding(
    room_id: uuid.UUID,
    chat_id: str,
    project_id: uuid.UUID | None = Query(default=None),
):
    if not re.fullmatch(r"oc_[A-Za-z0-9_-]{1,180}", chat_id):
        raise HTTPException(422, "飞书会话 ID 格式无效")
    async with controller.config_lock:
        before_enabled = store.config().get("enabled", False)
        store.remove_room_binding(str(room_id), chat_id, str(project_id) if project_id else None)
        after_enabled = store.config().get("enabled", False)
        worker_running = bool(controller.worker and controller.worker.returncode is None)
        if after_enabled != before_enabled or (after_enabled and not worker_running):
            try:
                await controller.restart_worker()
            except Exception:
                controller.status.update(state="error", reason="配置已保存，但飞书连接未能启动，请检查本机环境后重试")
        return rooms_payload()
