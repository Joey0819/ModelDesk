"""Allowlisted Feishu control of the existing group runtime and verified delivery."""
import asyncio
import copy
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .feishu_sdk import FeishuAPIError, FeishuSDK, safe_error
from .feishu_store import ConfigInput, FeishuStore, ROOT, binding_is_current, now
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


ROOMS_PAGE_SIZE = 10


def _room_labels(room):
    return room["title"], f"{room['project_name']}/{room['title']}", f"{room['project_name']} · {room['title']}"


def _selector_key(selector):
    return " ".join(selector.casefold().split())


def _format_room_disambiguation(selector, matches):
    lines = [f"找到多个匹配「{selector}」的项目群，请复制其中一个完整群 ID："]
    for room in matches[:10]:
        lines.append(f"{room['project_name']} · {room['title']}\n{room['id']}")
    if len(matches) > 10:
        lines.append("发送 /modeldesk groups 可分页查看全部项目群。")
    return "\n".join(lines)


def _matches_query(haystack, query):
    if not query:
        return False
    if query in haystack:
        return True
    tokens = query.split()
    return len(tokens) > 1 and all(token in haystack for token in tokens)


def _fuzzy_match_rooms(selector, rooms):
    folded = _selector_key(selector)
    stripped = re.sub(r"(?:项目群|群聊|群)$", "", folded).strip()
    queries = [folded] if not stripped or stripped == folded else [folded, stripped]
    for query in queries:
        title_matches = [room for room in rooms if _matches_query(_selector_key(room["title"]), query)]
        if title_matches:
            return title_matches
    for query in queries:
        combined_matches = [
            room for room in rooms
            if _matches_query(_selector_key(f"{room['project_name']} {room['title']}"), query)
            or any(_matches_query(_selector_key(label), query) for label in _room_labels(room)[1:])
        ]
        if combined_matches:
            return combined_matches
    return []


def resolve_room_selector(selector, rooms, numbers, *, allow_fuzzy=False):
    selector = selector.strip()
    quoted = QUOTED_SELECTOR.fullmatch(selector)
    if quoted and not quoted[5]:
        selector = next(group for group in quoted.groups()[:4] if group is not None)
    if not selector:
        return None, "请指定项目群 ID、名称或已显示的编号。发送 /modeldesk groups 查看项目群。"
    identifier = selector.removeprefix("#").lower()
    if TASK_ID.fullmatch(identifier):
        matches = [room for room in rooms if room["id"] == identifier]
    elif identifier.isdecimal() and not quoted:
        matches = [room for room in rooms if room["id"] == numbers.get(identifier)]
        if not matches:
            return None, "该编号未在此会话为你显示，或对应群已不可用。发送 /modeldesk groups 查看群 ID。"
    else:
        folded = _selector_key(selector)
        matches = [room for room in rooms if any(_selector_key(label) == folded for label in _room_labels(room))]
        if not matches and allow_fuzzy:
            matches = _fuzzy_match_rooms(selector, rooms)
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, _format_room_disambiguation(selector, matches)
    return None, f"未找到匹配「{selector[:160]}」的项目群。发送 /modeldesk groups 查看群 ID 和完整名称。"


def parse_to_command(prompt, rooms, numbers):
    text = prompt.strip()
    if not text:
        return None, "", "请填写目标群和任务，例如 /modeldesk to 群ID 任务内容。"
    quoted = QUOTED_SELECTOR.fullmatch(text)
    if quoted:
        selector = text[:quoted.start(5)].strip()
        task_prompt = quoted[5].strip()
    else:
        first_token, *rest = text.split(maxsplit=1)
        selector, task_prompt = first_token, rest[0].strip() if rest else ""
        identifier = first_token.removeprefix("#")
        if not (identifier.isdecimal() or TASK_ID.fullmatch(identifier)):
            labels = {_selector_key(label) for room in rooms for label in _room_labels(room)}
            max_label_length = max(map(len, labels), default=0)
            folded = ""
            for word in re.finditer(r"\S+", text):
                folded = (folded + " " + word[0].casefold()).lstrip()
                if len(folded) > max_label_length:
                    break
                if folded in labels:
                    selector, task_prompt = text[:word.end()], text[word.end():].strip()
    chosen, error = resolve_room_selector(selector, rooms, numbers)
    if error:
        return None, "", error
    if not task_prompt:
        return None, "", "请在项目群 ID 或名称后填写任务，例如 /modeldesk to 群ID 任务内容。"
    return chosen, task_prompt, None


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
            route = self.store.get_chat_route(record["app_id"], record["chat_id"], record["config_revision"], record["sender_id"])
            if not route:
                route = self.store.get_chat_route(record["app_id"], record["chat_id"], record["config_revision"], "*")
            if route:
                return route["project_id"], route["room_id"]
        return binding["project_id"], binding["room_id"]

    def _describe_active_room(self, record):
        binding = record["binding"]
        active_project_id, active_room_id = self.active_target(record)
        title = ""
        project_name = "独立项目" if active_project_id is None else ""
        member_names = []
        member_count = 0

        if binding.get("allow_all_groups"):
            try:
                matched = next(
                    (
                        room for room in list_all_rooms()
                        if room["id"] == active_room_id and room["project_id"] == active_project_id
                    ),
                    None,
                )
            except Exception:
                matched = None
            if matched:
                title = matched.get("title") or ""
                project_name = matched.get("project_name") or project_name
                member_count = int(matched.get("member_count") or 0)

        try:
            from .groups import store as group_store
            room = group_store.get(active_room_id, active_project_id)
            if isinstance(room, dict):
                title = title or room.get("title") or ""
                members = (room.get("team_snapshot") or {}).get("members") or []
                if members:
                    member_count = len(members)
                    member_names = [
                        str(member.get("name") or member.get("id")).strip()
                        for member in members
                        if isinstance(member, dict) and (member.get("name") or member.get("id"))
                    ]
        except Exception:
            pass

        if active_project_id and not project_name:
            try:
                from .projects import store as project_store
                reg = project_store.read_registry()
                project_name = (reg.get(active_project_id) or {}).get("name") or ""
            except Exception:
                project_name = ""

        label = ""
        if title:
            label = f"{project_name} · {title}" if project_name else title

        member_summary = ""
        if member_names:
            shown = "、".join(member_names[:6])
            suffix = " 等" if len(member_names) > 6 else ""
            member_summary = f"{member_count} 位成员：{shown}{suffix}"
        elif member_count > 0:
            member_summary = f"{member_count} 位成员"

        return {
            "project_id": active_project_id,
            "room_id": active_room_id,
            "title": title,
            "project_name": project_name,
            "label": label,
            "member_summary": member_summary,
        }

    async def send_find_rooms(self, record, prompt):
        binding = record["binding"]
        if not binding.get("allow_all_groups"):
            active_info = self._describe_active_room(record)
            bound_label = active_info["label"] or binding["room_id"]
            text = (
                f"当前飞书会话仅绑定到项目群：{bound_label}\n"
                "如需按名称搜索或切换全部项目群，请在本机项目群顶部的「飞书机器人」中为此会话开启全部项目群接入。"
            )
            await self.reply_once(record, "find", text)
            self.store.update(record, state="delivered", reason="未开启访问全部项目群权限")
            return

        if "find_result" not in record:
            rooms = list_all_rooms()
            active_project_id, active_room_id = self.active_target(record)
            active_info = self._describe_active_room(record)
            active_label = active_info["label"] or active_room_id
            raw_query = prompt.strip()
            folded = _selector_key(raw_query)
            stripped = re.sub(r"(?:项目群|群聊|群)$", "", folded).strip()
            effective_query = stripped or folded
            tokens = effective_query.split() if effective_query else []

            if not tokens:
                recent_rooms = sorted(
                    rooms,
                    key=lambda room: (room.get("updated_at") or "", room["id"]),
                    reverse=True,
                )[:5]
                room_ids = [room["id"] for room in recent_rooms]
                numbers = self.store.reserve_room_numbers(record, room_ids) if room_ids else {}
                lines = [
                    "请附带关键词搜索项目群，例如「找群 project」或「找群 研发」。",
                    f"当前默认群：{active_label}",
                ]
                if recent_rooms:
                    lines.append("最近活跃的项目群：")
                    for room in recent_rooms:
                        mark = "★ " if (room["project_id"], room["id"]) == (active_project_id, active_room_id) else ""
                        lines.append(f"{numbers[room['id']]}. {mark}{room['project_name']} · {room['title']}\n{room['id']}")
                    lines.append("回复「切换群 编号」可立即切换，或发送「群列表」查看全部项目群。")
                self.store.update(record, find_result={"room_ids": room_ids, "text": "\n".join(lines)})
            else:
                def room_matches(room):
                    haystack = _selector_key(f"{room['project_name']} {room['title']} {room['id']}")
                    return all(token in haystack for token in tokens)

                def title_matches(room):
                    title_key = _selector_key(room["title"])
                    return all(token in title_key for token in tokens)

                matches = [room for room in rooms if room_matches(room)]
                matches.sort(key=lambda room: (room["project_name"].casefold(), room["title"].casefold(), room["id"]))
                matches.sort(key=lambda room: room.get("updated_at") or "", reverse=True)
                matches.sort(key=lambda room: 0 if title_matches(room) else 1)

                if matches:
                    visible = matches[:10]
                    room_ids = [room["id"] for room in visible]
                    numbers = self.store.reserve_room_numbers(record, room_ids)
                    header_suffix = "（显示前 10 个，★ 为当前默认群）" if len(matches) > 10 else "（★ 为当前默认群）"
                    lines = [f"找到 {len(matches)} 个与「{raw_query[:80]}」匹配的项目群{header_suffix}"]
                    for room in visible:
                        mark = "★ " if (room["project_id"], room["id"]) == (active_project_id, active_room_id) else ""
                        lines.append(f"{numbers[room['id']]}. {mark}{room['project_name']} · {room['title']}\n{room['id']}")
                    if len(matches) == 1:
                        only = visible[0]
                        num = numbers[only["id"]]
                        lines.append(
                            f"回复「切换群 {num}」或「切换群 {only['title']}」设为当前默认群，之后直接发你的需求即可执行。\n"
                            f"或发送「@{only['title']} 你的任务」单次派发。"
                        )
                    else:
                        lines.append("回复「切换群 编号」设为当前默认群，之后直接发需求即可执行。")
                    self.store.update(record, find_result={"room_ids": room_ids, "text": "\n".join(lines)})
                else:
                    recent_rooms = sorted(
                        rooms,
                        key=lambda room: (room.get("updated_at") or "", room["id"]),
                        reverse=True,
                    )[:5]
                    room_ids = [room["id"] for room in recent_rooms]
                    numbers = self.store.reserve_room_numbers(record, room_ids) if room_ids else {}
                    lines = [
                        f"未找到包含「{raw_query[:80]}」的项目群。",
                        f"当前默认群：{active_label}",
                    ]
                    if recent_rooms:
                        lines.append("最近活跃的项目群：")
                        for room in recent_rooms:
                            mark = "★ " if (room["project_id"], room["id"]) == (active_project_id, active_room_id) else ""
                            lines.append(f"{numbers[room['id']]}. {mark}{room['project_name']} · {room['title']}\n{room['id']}")
                        lines.append("回复「切换群 编号」可立即切换，或发送「群列表」查看全部项目群。")
                    else:
                        lines.append("当前 ModelDesk 中暂无可用项目群。")
                    self.store.update(record, find_result={"room_ids": room_ids, "text": "\n".join(lines)})

        snapshot = record["find_result"]
        await self.reply_once(record, "find", snapshot["text"])
        if snapshot["room_ids"]:
            self.store.mark_room_numbers_shown(record, snapshot["room_ids"])
        self.store.update(record, state="delivered", reason="群搜索结果已回传")

    async def send_current_room(self, record):
        binding = record["binding"]
        info = self._describe_active_room(record)
        if info["label"]:
            suffix = f"（{info['member_summary']}）" if info["member_summary"] else ""
            header = f"当前默认项目群：{info['label']}{suffix}"
        else:
            header = f"当前默认项目群（{info['room_id']}）暂不可用。"

        first_line = (
            "1. 直接发送你的需求，立即交给这个群执行"
            if binding.get("natural_language_tasks")
            else "1. 发送 /modeldesk task 你的需求，立即交给这个群执行"
        )
        if binding.get("allow_all_groups"):
            lines = [
                header,
                "你可以：",
                first_line,
                "2. 发送「找群 关键词」按名称搜索其他群",
                "3. 发送「切换群 群名或编号」切换默认群",
                "4. 发送「群列表」查看全部项目群",
            ]
        else:
            lines = [
                header,
                "你可以：",
                first_line,
                "2. 发送「状态」或 /modeldesk status 查看最近任务进度",
                "如需搜索或切换其他项目群，请在本机项目群顶部的「飞书机器人」中开启全部项目群接入。",
            ]
        await self.reply_once(record, "current", "\n".join(lines))
        self.store.update(record, state="delivered", reason="当前默认群已回传")

    async def send_rooms_page(self, record, prompt):
        if "rooms_page" not in record:
            rooms = sorted(list_all_rooms(), key=lambda room: (
                room["project_name"].casefold(), room["title"].casefold(), room["id"]
            ))
            if not rooms:
                await self.reply_once(record, "groups", "当前 ModelDesk 中暂无可用项目群。")
                return
            page_count = (len(rooms) + ROOMS_PAGE_SIZE - 1) // ROOMS_PAGE_SIZE
            requested_page = prompt or "1"
            page = int(requested_page) if re.fullmatch(r"[0-9]{1,6}", requested_page) else 0
            if not 1 <= page <= page_count:
                await self.reply_once(record, "groups", f"请用 /modeldesk groups 页码 查看第 1 至 {page_count} 页。")
                return
            start = (page - 1) * ROOMS_PAGE_SIZE
            visible = rooms[start:start + ROOMS_PAGE_SIZE]
            room_ids = [room["id"] for room in visible]
            numbers = self.store.reserve_room_numbers(record, room_ids)
            active_project_id, active_room_id = self.active_target(record)
            lines = [f"可用项目群（共 {len(rooms)} 个，第 {page}/{page_count} 页，★ 为你的默认群）"]
            for room in visible:
                mark = "★ " if (room["project_id"], room["id"]) == (active_project_id, active_room_id) else ""
                lines.append(f"{numbers[room['id']]}. {mark}{room['project_name']} · {room['title']}\n{room['id']}")
            if page < page_count:
                lines.append(f"下一页 /modeldesk groups {page + 1}")
            elif page > 1:
                lines.append("首页 /modeldesk groups")
            lines.extend(["切换 /modeldesk use 群ID", "单次派发 /modeldesk to 群ID 任务内容"])
            self.store.update(record, rooms_page={"room_ids": room_ids, "text": "\n".join(lines)})
        snapshot = record["rooms_page"]
        await self.reply_once(record, "groups", snapshot["text"])
        self.store.mark_room_numbers_shown(record, snapshot["room_ids"])

    async def accept(self, record):
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
        if record.get("task_prompt"):
            await self.dispatch_task(record)
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
        if action in {"current", "当前群", "当前项目群", "默认群"}:
            await self.send_current_room(record)
            return
        if action in {"find", "search", "找群", "搜群"}:
            await self.send_find_rooms(record, prompt)
            return
        if action in {"groups", "群列表"}:
            if binding.get("allow_all_groups"):
                await self.send_rooms_page(record, prompt)
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
                    "如需切换项目群，请在本机项目群顶部的「飞书机器人」中为此会话开启全部项目群接入。"
                )
                await self.reply_once(record, "groups", text)
            self.store.update(record, state="delivered", reason="群列表已回传")
            return
        if action in {"use", "切换群", "切换项目群"}:
            if not binding.get("allow_all_groups"):
                await self.reply_once(
                    record,
                    "use",
                    "当前飞书会话未开启「访问全部项目群」权限。请在本机项目群顶部的「飞书机器人」中开启后再切换。",
                )
                self.store.update(record, state="delivered", reason="未开启访问全部项目群权限")
                return
            chosen, error = resolve_room_selector(
                prompt,
                list_all_rooms(),
                self.store.shown_room_numbers(record),
                allow_fuzzy=True,
            )
            if not chosen:
                await self.reply_once(record, "use", error)
                self.store.update(record, state="delivered", reason="切换群未匹配目标")
                return
            self.store.set_chat_route(record["app_id"], record["chat_id"], record["config_revision"], record["sender_id"],
                                      chosen["project_id"], chosen["id"])
            await self.reply_once(
                record,
                "use",
                f"你在此会话的默认项目群已切换为 {chosen['project_name']} · {chosen['title']}\n{chosen['id']}",
            )
            self.store.update(record, state="delivered", reason=f"已切换默认群为 {chosen['title']}")
            return
        if action in {"status", "状态"} and not prompt:
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
                    text = "你在当前飞书会话暂无任务。"
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
        room_mention = action == "task" and prompt.startswith("@") and not record.get("leading_user_mention")
        if action == "to" or room_mention:
            if not binding.get("allow_all_groups"):
                await self.reply_once(
                    record,
                    "to",
                    "当前飞书会话未开启「访问全部项目群」权限。请在本机项目群顶部的「飞书机器人」中开启后再指定其他项目群。",
                )
                self.store.update(record, state="delivered", reason="未开启访问全部项目群权限")
                return
            selector_and_task = prompt[1:] if room_mention else prompt
            chosen, task_prompt, error = parse_to_command(
                selector_and_task, list_all_rooms(), self.store.shown_room_numbers(record)
            )
            if error:
                await self.reply_once(record, "to", error)
                self.store.update(record, state="delivered", reason="指定群指令未匹配目标")
                return
            explicit_target = (chosen["project_id"], chosen["id"])
            prompt = task_prompt
            action = "task"
        if action not in {"task", "continue"} or not prompt:
            active_info = self._describe_active_room(record)
            active_line = f"当前默认项目群：{active_info['label']}\n" if active_info["label"] else ""
            direct_help = ('直接描述需求可开始新任务。回复任务回执，或用“继续：完整任务ID 修改要求”继续该任务。\n'
                           if binding.get('natural_language_tasks') else '')
            shortcut_help = ('常用快捷语：当前群 / 找群 关键词 / 切换群 群名或编号 / 群列表\n'
                             if binding.get('natural_language_tasks') and binding.get('allow_all_groups') else '')
            multi_help = ("\n/modeldesk current（查看当前默认群）\n/modeldesk find 关键词（搜索项目群）\n/modeldesk groups（查看全部项目群）\n/modeldesk use 群ID（切换默认群）\n/modeldesk to 群ID 任务内容（单次指定群）"
                          if binding.get("allow_all_groups") else "")
            await self.reply_once(record, "usage", active_line + direct_help + shortcut_help + "使用方法：\n/modeldesk task 完整任务（新任务）\n/modeldesk continue 完整任务ID 修改要求\n回复任务回执：/modeldesk continue 修改要求\n/modeldesk status\n/modeldesk cancel 完整任务ID" + multi_help)
            self.store.update(record, state="delivered", reason="已回传指令用法，没有创建任务")
            return
        job = self.jobs.jobs.get(record["job_id"])
        if job:
            if not self.belongs(job, record) or job.get("external_source", {}).get("message_id") != record["message_id"]:
                raise RuntimeError("job identity conflict")
            self.store.update(record, state="monitoring", reason="已恢复已有任务记录，没有重复创建")
            return
        target = None
        if action == "continue":
            try:
                target, prompt = self.continuation_target(record, prompt)
            except HTTPException as error:
                await self.reply_once(record, "continue_target", f"{error.detail}\n{CONTINUE_USAGE}")
                self.store.update(record, state="delivered", reason=error.detail)
                return
        if target:
            parent_job = self.jobs.jobs[target]
            target_project_id, target_room_id = parent_job["project_id"], parent_job["group_room_id"]
        elif explicit_target is not None:
            target_project_id, target_room_id = explicit_target
        else:
            target_project_id, target_room_id = self.active_target(record)
        # Freeze the destination before waiting for capacity or processing a later switch.
        self.store.update(
            record,
            target_project_id=target_project_id,
            target_room_id=target_room_id,
            task_prompt=prompt,
            parent_job_id=target,
        )
        await self.dispatch_task(record)

    async def dispatch_task(self, record):
        from .groups import MessageRequest, store as group_store, submit_message

        if sum(job.get("status") not in workbench.TERMINAL for job in self.jobs.jobs.values()) >= 4:
            return
        binding = record["binding"]
        target = record.get("parent_job_id")
        target_project_id, target_room_id = record["target_project_id"], record["target_room_id"]
        if binding.get("allow_all_groups"):
            try:
                group_store.get(target_room_id, target_project_id)
            except HTTPException:
                text = "选定的项目群已不可用。发送 /modeldesk groups 重新选择项目群。"
                await self.reply_once(record, "target_unavailable", text)
                self.store.update(record, state="delivered", reason=text)
                return
        # Restart reconciles a job in this state without guessing dispatch success.
        self.store.update(record, state="dispatching", reason="正在创建已授权的后台任务")
        source = {"platform": "feishu", "app_id": record["app_id"], "chat_id": record["chat_id"],
                  "message_id": record["message_id"], "sender_id": record["sender_id"], "config_revision": record["config_revision"]}
        if target:
            source["parent_job_id"] = target

        def assert_admission():
            self.assert_current(record)
            if target:
                self.checked_continuation_job(record, target)

        await submit_message(target_room_id, MessageRequest(prompt=record["task_prompt"], context_mode="continue" if target else "fresh",
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
            return (source.get("sender_id") == record["sender_id"] and job.get("mode") == "group"
                    and bool(job.get("group_room_id")) and job.get("conversation_id") == job.get("group_room_id"))
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
                and binding_is_current(binding_snapshot, live["bindings"])
            )

        rooms = {(binding["project_id"], binding["room_id"]) for binding in push_bindings}
        candidates = defaultdict(list)
        for job in self.jobs.jobs.values():
            scope = (job.get("project_id"), job.get("group_room_id"))
            if job.get("mode") != "group" or scope not in rooms or (job.get("created_at") or "") < cutoff:
                continue
            # Feishu requests deliver to their origin, regardless of subscriptions.
            source = job.get("external_source")
            if source and source.get("platform") != "routine":
                continue
            candidates[scope].append(job)

        room_titles = None
        for binding in push_bindings:
            sdk = None
            queued = candidates.get((binding["project_id"], binding["room_id"]), ())
            for job in sorted(queued, key=lambda candidate: candidate.get("created_at") or ""):
                autonomy = job.get("autonomy") or {}
                delivery = (job.get("result") or {}).get("delivery") or {}
                state = autonomy.get("state")
                notification = autonomy.get("notification_id")
                if not (state in {"complete", "needs_attention"} and notification):
                    if job.get("status") != "complete" or delivery.get("status") != "complete":
                        continue
                    state = "complete"
                    notification = "complete:" + job["id"]
                cache_key = (app_id, expected_revision, binding["chat_id"], job["id"], notification)
                if cache_key in self._pushed_keys:
                    continue
                if not push_still_authorized(binding):
                    return
                if room_titles is None:
                    try:
                        room_titles = {room["id"]: room["title"] for room in list_all_rooms()}
                    except Exception:
                        room_titles = {}
                if sdk is None:
                    sdk = self.sdk_factory(config)
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


def room_summary(room_id, project_id):
    result = {"room_id": room_id, "project_id": project_id, "mode": "none", "status": "disconnected",
              "destination": None, "switch_command": None, "bot_url": None}
    try:
        config = store.config(readonly=True)
    except (HTTPException, OSError):
        return result
    if not config["app_id"] or not config["app_secret"]:
        result["status"] = "not_configured"
    elif not config["enabled"]:
        result["status"] = "disabled"
    else:
        result["status"] = {"connected": "connected", "connecting": "connecting", "reconnecting": "connecting"}.get(
            controller.status.get("state"), "disconnected"
        )

    applicable = [binding for binding in config["bindings"]
                  if binding.get("allow_all_groups") or (binding["room_id"], binding.get("project_id")) == (room_id, project_id)]
    if not applicable:
        return result
    try:
        types = store.observed_chat_types(config["app_id"], [binding["chat_id"] for binding in applicable])
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        types = {}
    # Keep the command and destination on the same route. Prefer a known chat
    # the user can open with an AppLink, then a known group or unclassified chat.
    binding = min(applicable, key=lambda item: (
        {"p2p": 0, "group": 1}.get(types.get(item["chat_id"]), 2), bool(item.get("allow_all_groups")),
    ))
    all_groups = binding.get("allow_all_groups")
    destination = {"p2p": "private", "group": "group"}.get(types.get(binding["chat_id"]), "unknown")
    result.update(mode="all_groups" if all_groups else "direct", destination=destination)
    if result["status"] == "connected":
        if all_groups:
            result["switch_command"] = f"/modeldesk use {room_id}"
        if destination == "private":
            result["bot_url"] = "https://applink.feishu.cn/client/bot/open?" + urlencode({"appId": config["app_id"]})
    return result


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


class AllowAllRoomGroupsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID | None = None


class ActivateRoomInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID | None = None
    chat_id: str | None = Field(default=None, pattern=r"^oc_[A-Za-z0-9_-]{1,180}$")
    notify_feishu: bool = True


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


@router.put("/rooms/{room_id}/binding/{chat_id}/allow-all-groups")
async def allow_all_room_groups(room_id: uuid.UUID, chat_id: str, body: AllowAllRoomGroupsInput):
    if not re.fullmatch(r"oc_[A-Za-z0-9_-]{1,180}", chat_id):
        raise HTTPException(422, "飞书会话 ID 格式无效")
    async with controller.config_lock:
        store.allow_all_room_groups(str(room_id), chat_id, str(body.project_id) if body.project_id else None)
    return {"allow_all_groups": True}


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


@router.post("/rooms/{room_id}/activate")
async def activate_room(room_id: uuid.UUID, body: ActivateRoomInput = Body(default_factory=ActivateRoomInput)):
    from .groups import store as group_store
    from .projects import store as project_store

    room_id_str = str(room_id)
    pid = str(body.project_id) if body.project_id else None
    room = group_store.get(room_id_str, pid)
    project = project_store.get(pid) if pid else None
    project_name = (project.get("name") if isinstance(project, dict) else None) or (pid if pid else "独立项目")
    room_title = (room.get("title") if isinstance(room, dict) else None) or room_id_str
    room_label = f"{project_name} · {room_title}"

    async with controller.config_lock:
        config = store.config()
        if not config.get("app_id") or not config.get("app_secret"):
            raise HTTPException(422, "请先填写并保存飞书应用的 App ID 和 App Secret")
        if not config.get("enabled"):
            raise HTTPException(422, "飞书连接尚未启用，请先启用飞书连接")
        bindings = config.get("bindings") or []
        already_direct = False
        if body.chat_id:
            target = next((item for item in bindings if item["chat_id"] == body.chat_id), None)
            if not target or not target.get("allow_all_groups"):
                raise HTTPException(409, "指定的飞书会话不存在或未开启全部项目群接入。")
            targets = [target]
        else:
            targets = [item for item in bindings if item.get("allow_all_groups")]
            if not targets:
                direct = [
                    item for item in bindings
                    if item["room_id"] == room_id_str and item.get("project_id") == pid
                ]
                if direct:
                    already_direct = True
                    targets = direct
                else:
                    raise HTTPException(
                        409,
                        "当前没有支持全部项目群的飞书会话，也没有直接绑定到此群的会话。请先开启全群接入或绑定飞书会话。",
                    )
        app_id = config["app_id"]
        revision = config["revision"]
        updated_chats = []
        if already_direct:
            updated_chats = [item["chat_id"] for item in targets]
        else:
            owners = list(config.get("owner_sender_ids") or [])
            for binding in targets:
                chat_id = binding["chat_id"]
                allowed = list(binding.get("allowed_sender_ids") or [])
                if allowed:
                    sender_targets = allowed
                else:
                    known = store.known_senders_for_chat(app_id, chat_id)
                    sender_targets = sorted(set(known) | set(owners)) + ["*"]
                for sender_id in sender_targets:
                    store.set_chat_route(app_id, chat_id, revision, sender_id, pid, room_id_str)
                updated_chats.append(chat_id)
        active_routes = store.active_routes(app_id, revision)
        sdk_config = copy.deepcopy(config)

    feishu_notified = False
    notify_error = None
    if body.notify_feishu and updated_chats:
        if already_direct:
            notify_text = (
                f"当前飞书会话已直接绑定到项目群：{room_label}\n"
                "现在可以直接在这里发你的需求，由这个群为你执行。\n"
                "提示：发送「当前群」可随时确认当前项目群。"
            )
        else:
            notify_text = (
                f"已将你的当前默认项目群切换为：{room_label}\n"
                "现在可以直接在这里发你的需求，由这个群为你执行。\n"
                "提示：发送「当前群」「找群 关键词」或「群列表」可随时确认或切换项目群。"
            )
        sdk = controller.sdk_factory(sdk_config)
        notified_count = 0
        errors = []
        for chat_id in updated_chats:
            try:
                await asyncio.to_thread(sdk.send, chat_id, "text", {"text": notify_text}, str(uuid.uuid4()))
                notified_count += 1
            except Exception as error:
                errors.append(safe_error(error))
        feishu_notified = notified_count == len(updated_chats)
        notify_error = errors[0] if errors else None

    return {
        "ok": True,
        "already_direct": already_direct,
        "room_id": room_id_str,
        "project_id": pid,
        "room_label": room_label,
        "updated_chats": updated_chats,
        "feishu_notified": feishu_notified,
        "notify_error": notify_error,
        "active_routes": active_routes,
    }
