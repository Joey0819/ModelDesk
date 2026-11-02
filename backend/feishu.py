"""Allowlisted Feishu control of the existing group runtime and verified delivery."""
import asyncio
import copy
import json
import os
import sqlite3
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .feishu_sdk import FeishuAPIError, FeishuSDK, safe_error
from .feishu_store import ConfigInput, FeishuStore, ROOT, now
from . import workbench


class FeishuController:
    def __init__(self, store, jobs, *, sdk_factory=FeishuSDK):
        self.store, self.jobs, self.sdk_factory = store, jobs, sdk_factory
        self.task = None
        self.worker = None
        self.reader = None
        self.lock = asyncio.Lock()
        self.config_lock = asyncio.Lock()
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
            reasons = {"connected": "飞书长连接在线，等待白名单聊天中的 /modeldesk 指令", "connecting": "正在连接或重连飞书",
                       "error": "飞书连接失败，请检查应用凭据、网络及长连接配置"}
            self.status.update(state=state, reason=reasons[state])
            if state == "connected":
                self.status["last_connected_at"] = now()
        if self.worker is worker:
            self.status.update(state="error", reason="飞书长连接已退出；请检查设置并重新保存启用")

    async def shutdown(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.stop_worker()

    async def loop(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status.update(state="error", reason="飞书本地记录暂时无法处理，请检查设置")
            await asyncio.sleep(2)

    async def tick(self):
        async with self.lock:
            for record in self.store.events(["received", "dispatching", "monitoring", "attention"]):
                if not self.store.current(record):
                    self.store.update(record, state="needs_attention", reason="飞书应用或绑定配置已改变；历史任务不会向新配置自动回传")
                    continue
                try:
                    if record["state"] in {"received", "dispatching"}:
                        await self.accept(record)
                    if record["state"] in {"monitoring", "attention"}:
                        await self.deliver(record)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.store.update(record, state="needs_attention", reason=safe_error(error))

    async def accept(self, record):
        from .groups import MessageRequest, submit_message
        command = record["command"]
        if command in {"status", "状态"}:
            related = [job for job in self.jobs.jobs.values() if self.belongs(job, record)]
            latest = sorted(related, key=lambda job: job.get("created_at", ""))[-3:]
            receipts = {row["job_id"]: row for row in self.store.events() if row["state"] not in {"received", "dispatching"}}
            text = "当前绑定群暂无飞书任务。" if not latest else "\n".join(
                job["id"] + "\n执行：" + (job.get("autonomy") or {}).get("reason", job["status"])
                + "\n飞书回传：" + receipts.get(job["id"], {}).get("reason", "等待回传") for job in latest)
            await self.reply_once(record, "status", text)
            self.store.update(record, state="delivered", reason="状态已回传")
            return
        if command.startswith(("cancel ", "停止 ")):
            target = command.split(" ", 1)[1].strip()
            job = self.jobs.jobs.get(target)
            if not job or not self.belongs(job, record):
                await self.reply_once(record, "cancel", "当前飞书聊天没有此任务，请使用 /modeldesk status 查看任务 ID。")
            else:
                await self.jobs.cancel(target)
                await self.reply_once(record, "cancel", "已停止任务 " + target + "；已有成果保留。")
            self.store.update(record, state="delivered", reason="停止指令已处理")
            return
        action, _, prompt = command.partition(" ")
        if action not in {"task", "continue"} or not prompt.strip():
            await self.reply_once(record, "usage", "使用方法：\n/modeldesk task 完整任务（新任务）\n/modeldesk continue 修改要求（使用绑定群的共享上下文）\n/modeldesk status\n/modeldesk cancel 完整任务ID")
            self.store.update(record, state="delivered", reason="已回传指令用法，没有创建任务")
            return
        job = self.jobs.jobs.get(record["job_id"])
        if job:
            if not self.belongs(job, record) or job.get("external_source", {}).get("message_id") != record["message_id"]:
                raise RuntimeError("job identity conflict")
            self.store.update(record, state="monitoring", reason="已恢复已有任务记录，没有重复创建")
            return
        if record["state"] == "dispatching":
            self.store.update(record, state="needs_attention", reason="上次任务创建结果未确认；不会自动重复创建，请检查本机任务")
            return
        if sum(job.get("status") not in workbench.TERMINAL for job in self.jobs.jobs.values()) >= 4:
            return
        # Commit the deterministic job ID before any awaited validation or model
        # dispatch. Restart reconciles an existing job, never guesses absence.
        self.store.update(record, state="dispatching", reason="正在创建已授权的后台任务")
        binding = record["binding"]
        source = {"platform": "feishu", "app_id": record["app_id"], "chat_id": record["chat_id"],
                  "message_id": record["message_id"], "sender_id": record["sender_id"], "config_revision": record["config_revision"]}
        await submit_message(binding["room_id"], MessageRequest(prompt=prompt.strip(), context_mode="fresh" if action == "task" else "continue",
                             delivery_mode="auto", background_mode=True),
                             binding["project_id"], native_allowed=binding["allow_native"], job_id=record["job_id"], external_source=source,
                             admission_check=lambda: self.assert_current(record))
        self.store.update(record, state="monitoring", reason="任务已交给 ModelDesk 后台执行")
        try:
            await self.reply_once(record, "accepted", "已收到任务，ModelDesk 正在后台协作。\n任务 ID：" + record["job_id"]
                                  + "\n完成或需要处理时会回传到本条消息；Mac 和 ModelDesk 服务需保持运行。")
        except Exception:
            # An uncertain acknowledgement must not lose a successfully
            # created job or prevent its separate completion notification.
            self.store.update(record, reason="任务已创建；接收确认消息未确认，不会重复发送，仍会等待最终成果")

    @staticmethod
    def belongs(job, record):
        source = job.get("external_source") or {}
        return (source.get("platform") == "feishu" and source.get("app_id") == record["app_id"] and source.get("chat_id") == record["chat_id"]
                and source.get("config_revision") == record["config_revision"]
                and job.get("project_id") == record["binding"]["project_id"] and job.get("group_room_id") == record["binding"]["room_id"])

    def assert_current(self, record):
        if not self.store.current(record):
            raise FeishuAPIError("飞书连接已停用或授权配置已改变，未继续派发或回传")

    async def operation(self, record, key, function, *args):
        self.assert_current(record)
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu-out:" + record["id"] + ":" + key))
        reserved, receipt = self.store.reserve_outgoing(identifier, record["id"], {"kind": key})
        if not reserved:
            if receipt["state"] == "sent":
                return receipt["response_id"]
            raise RuntimeError("outbound result unconfirmed")
        try:
            def dispatch():
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

    async def reply_once(self, record, key, text):
        sdk = self.sdk_factory(self.store.config())
        return await self.operation(record, key, lambda value, dispatch: sdk.reply(record["message_id"], "text", {"text": value}, dispatch), text)

    async def deliver(self, record):
        from .autonomy import completion_problem
        from .feishu_assets import verified_artifacts
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
        title = {"complete": "任务已完成", "needs_attention": "任务需要你处理", "cancelled": "任务已停止"}[state]
        text = title + "\n任务 ID：" + job["id"] + "\n" + meta.get("reason", "")
        if artifacts:
            text += f"\n将回传完整正文和 {len(artifacts)} 份实际成果文件；图片和视频作为文件附件发送。"
        await self.reply_once(record, notification + ":summary", text)
        if state == "complete":
            content = delivery["content"]
            chunks = [content[index:index + 4500] for index in range(0, len(content), 4500)]
            for index, chunk in enumerate(chunks):
                await self.reply_once(record, notification + f":body:{index}", f"完整正文 {index + 1}/{len(chunks)}\n" + chunk)
            sdk = self.sdk_factory(self.store.config())
            for key, manifest, raw in artifacts:
                file_key = await self.operation(record, notification + ":upload:" + key,
                    lambda data, dispatch: sdk.upload(manifest, data), raw)
                await self.operation(record, notification + ":file:" + key,
                    lambda key, dispatch: sdk.reply(record["message_id"], "file", {"file_key": key}, dispatch), file_key)
        self.store.update(record, state="attention" if state == "needs_attention" else "delivered",
                          reason=title + "，已回传飞书", notifications=[*record["notifications"], notification])


store = FeishuStore()
controller = FeishuController(store, workbench.manager)


def local_admin(request: Request):
    from .remote_access import require_local
    require_local(request)
    if request.headers.get("x-modeldesk-remote"):
        raise HTTPException(403, "飞书凭据和聊天授权只能在这台 Mac 管理")


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
