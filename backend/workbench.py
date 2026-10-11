"""Local multi-model workbench: durable jobs, streaming chat, and real frameworks.

Provider credentials stay in the local OAuth gateway. The browser sends only
model IDs and task text; worker processes use fixed scripts and argument arrays.
"""

import asyncio
import codecs
import copy
import json
import os
import re
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .council import (
    calculate_aggregate_rankings,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)


PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT.parent / "llm-tools"
GATEWAY = "http://127.0.0.1:8317/v1"
VERIFIED_MODELS = {"gpt-6.1-sol", "gpt-6-luna", "gemini-3-flash"}
TERMINAL = {"complete", "error", "cancelled", "interrupted"}
router = APIRouter(prefix="/api/workbench", tags=["workbench"])


def now():
    return datetime.now(timezone.utc).isoformat()


def public_error(exc):
    """Return bounded diagnostics, never upstream credentials or request headers."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return f"模型网关返回 HTTP {status}；请检查登录状态、模型权限或配额。"
    if isinstance(exc, httpx.TimeoutException):
        return "模型请求超时，请稍后重试。"
    if isinstance(exc, httpx.RequestError):
        return "无法连接本地模型网关，请先启动服务。"
    if isinstance(exc, TimeoutError):
        return "任务运行超时，工作进程已停止，请减少轮数后重试。"
    text = str(exc)[:500]
    if re.search(r"bearer\s|access_token|refresh_token|sk-[\w-]+|authorization|GOCSPX", text, re.I):
        return "模型调用失败；详细诊断保留在本机。"
    return text or type(exc).__name__


async def model_catalog():
    async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
        response = await client.get(GATEWAY + "/models")
        response.raise_for_status()
        return sorted({item["id"] for item in response.json().get("data", []) if isinstance(item.get("id"), str)})


def provider_for(model):
    if model.startswith("gpt-") or model.startswith("o3") or model.startswith("o4"):
        return "codex"
    if model.startswith("gemini-"):
        return "antigravity"
    return "other"


class JobRequest(BaseModel):
    mode: Literal["chat", "council", "camel", "crewai"]
    prompt: str = Field(min_length=1, max_length=12000)
    models: list[str] = Field(min_length=1, max_length=4)
    chairman: str | None = Field(default=None, max_length=200)
    rounds: int = Field(default=2, ge=1, le=6)
    conversation_id: str | None = None

    @field_validator("prompt")
    @classmethod
    def meaningful_prompt(cls, value):
        if not value.strip():
            raise ValueError("请输入任务内容")
        return value.strip()

    @field_validator("models")
    @classmethod
    def unique_models(cls, value):
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("模型名称无效")
        if len(set(value)) != len(value):
            raise ValueError("请为每个角色选择不同的模型")
        return value

    @field_validator("conversation_id")
    @classmethod
    def uuid_conversation(cls, value):
        return str(uuid.UUID(value)) if value else None


class JobManager:
    def __init__(self, directory=None):
        self.directory = Path(directory or PROJECT / "data/workbench")
        self.jobs = {}
        self.tasks = {}
        self.wakeups = {}
        self.processes = {}
        self.conversation_locks = {}

    def save(self, job):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / (job["id"] + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(target)

    def recover(self):
        """A restarted service cannot claim abandoned jobs are still running."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in self.directory.glob("*.json"):
            try:
                job = json.loads(path.read_text())
                if str(uuid.UUID(job["id"])) != path.stem:
                    continue
                self.jobs[job["id"]] = job
                self.wakeups[job["id"]] = asyncio.Event()
                if job["status"] not in TERMINAL:
                    job["status"] = "interrupted"
                    job["error"] = "服务重启中断了此任务，请重新运行。"
                    self.emit(job, "error", message=job["error"])
            except (ValueError, KeyError, TypeError, OSError):
                # A malformed local file must not prevent all other jobs loading.
                continue

    def emit(self, job, event_type, **data):
        if len(job["events"]) >= 12000 and event_type not in {"error", "cancelled", "complete"}:
            raise RuntimeError("任务输出过长，已停止。")
        event = {"type": event_type, "job_id": job["id"], "seq": len(job["events"]) + 1, "created_at": now(), **data}
        job["events"].append(event)
        job["updated_at"] = event["created_at"]
        self.save(job)
        self.wakeups[job["id"]].set()

    def get(self, job_id):
        if job_id not in self.jobs:
            raise HTTPException(404, "任务不存在")
        return self.jobs[job_id]

    def create(self, request):
        if sum(job["status"] not in TERMINAL for job in self.jobs.values()) >= 4:
            raise HTTPException(429, "最多同时运行 4 个任务，请等待或停止已有任务。")
        job_id = str(uuid.uuid4())
        timestamp = now()
        job = {
            "id": job_id,
            "conversation_id": request.conversation_id if request.mode == "chat" and request.conversation_id else str(uuid.uuid4()),
            "mode": request.mode,
            "prompt": request.prompt,
            "models": request.models,
            "chairman": request.chairman or request.models[0],
            "rounds": request.rounds,
            "status": "queued",
            "created_at": timestamp,
            "updated_at": timestamp,
            "result": None,
            "error": None,
            "events": [],
        }
        self.jobs[job_id] = job
        self.wakeups[job_id] = asyncio.Event()
        self.save(job)
        self.tasks[job_id] = asyncio.create_task(self.run(job))
        return copy.deepcopy(job)

    def chat_context(self, job, model):
        prior = sorted((item for item in self.jobs.values() if item["mode"] == "chat" and item["conversation_id"] == job["conversation_id"] and item["status"] == "complete" and item["created_at"] < job["created_at"]), key=lambda item: item["created_at"])
        pairs = []
        budget = 16000 - len(job["prompt"])
        for previous in reversed(prior[-6:]):
            responses = previous["result"].get("responses", [])
            answer = next((item for item in responses if item["model"] == model and not item.get("error")), None)
            if answer:
                context = {"role": "assistant", "content": answer["text"]}
            else:
                quoted = "\n\n".join(f"[{item['model']}]\n{item['text']}" for item in responses if not item.get("error"))
                if not quoted:
                    continue
                # Never attribute a different provider's answer to this model.
                context = {"role": "user", "content": "以下是上一轮其他模型的回答，仅作为引用背景：\n" + quoted}
            if not context["content"]:
                continue
            size = len(previous["prompt"]) + len(context["content"])
            if size > budget:
                break
            budget -= size
            pairs.insert(0, [{"role": "user", "content": previous["prompt"]}, context])
        return [message for pair in pairs for message in pair] + [{"role": "user", "content": job["prompt"]}]

    async def run(self, job):
        try:
            job["status"] = "running"
            self.emit(job, "started", mode=job["mode"])
            if job["mode"] == "chat":
                lock = self.conversation_locks.setdefault(job["conversation_id"], asyncio.Lock())
                async with lock:
                    result = await self.chat(job)
            elif job["mode"] == "council":
                result = await self.council(job)
            else:
                result = await self.worker(job)
            if job["status"] == "cancelled":
                return
            job["status"] = "complete"
            job["result"] = result
            self.emit(job, "complete", result=result)
        except asyncio.CancelledError:
            if job["status"] != "cancelled":
                job["status"] = "cancelled"
                self.emit(job, "cancelled")
        except Exception as exc:
            if job["status"] == "cancelled":
                return
            job["status"] = "error"
            job["error"] = public_error(exc)
            self.emit(job, "error", message=job["error"])
        finally:
            self.tasks.pop(job["id"], None)

    async def chat(self, job):
        async def respond(model):
            content = []
            finished = False
            try:
                payload = {"model": model, "messages": self.chat_context(job, model), "stream": True, "max_tokens": 1024}
                async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), trust_env=False) as client:
                    async with client.stream("POST", GATEWAY + "/chat/completions", json=payload) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                finished = True
                                break
                            chunk = json.loads(data)
                            if chunk.get("error"):
                                raise RuntimeError("模型网关流返回错误，请检查登录权限或配额。")
                            for choice in chunk.get("choices", []):
                                if choice.get("finish_reason") is not None:
                                    finished = True
                                delta = choice.get("delta", {}).get("content")
                                if isinstance(delta, str) and delta:
                                    content.append(delta)
                                    self.emit(job, "delta", model=model, text=delta)
                if not content:
                    raise RuntimeError("模型未返回可显示的回答。")
                if not finished:
                    raise RuntimeError("回答流提前中断，已收到的内容可能不完整，请重试。")
                return {"model": model, "text": "".join(content)}
            except Exception as exc:
                message = public_error(exc)
                self.emit(job, "log", text=f"{model}: {message}")
                return {"model": model, "text": "".join(content), "error": message}

        responses = await asyncio.gather(*(respond(model) for model in job["models"]))
        if all(item.get("error") for item in responses):
            # Keep incomplete text and each model's error available after refresh.
            job["result"] = {"responses": responses}
            raise RuntimeError("所有模型均调用失败。" + " ".join(item["error"] for item in responses))
        return {"responses": responses}

    async def council(self, job):
        models = job["models"]
        self.emit(job, "log", text="第一阶段：各模型独立回答")
        first = await stage1_collect_responses(job["prompt"], models=models, max_tokens=512)
        if not first:
            raise RuntimeError("议会成员均未返回回答，请检查模型登录权限或配额。")
        missing = [model for model in models if model not in {item["model"] for item in first}]
        self.emit(job, "stage", stage="responses", data=first)
        if missing:
            self.emit(job, "log", text="未返回回答的模型：" + "、".join(missing))
        self.emit(job, "log", text="第二阶段：匿名交叉评审")
        second, labels = await stage2_collect_rankings(job["prompt"], first, models=models, max_tokens=1024)
        if not second:
            raise RuntimeError("匿名评审未返回结果，请检查模型登录权限或配额。")
        missing_reviewers = [model for model in models if model not in {item["model"] for item in second}]
        metadata = {"label_to_model": labels, "aggregate_rankings": calculate_aggregate_rankings(second, labels), "failed_models": missing, "failed_reviewers": missing_reviewers, "partial_failure": bool(missing or missing_reviewers)}
        self.emit(job, "stage", stage="rankings", data=second, metadata=metadata)
        if missing_reviewers:
            self.emit(job, "log", text="未返回评审的模型：" + "、".join(missing_reviewers))
        self.emit(job, "log", text="第三阶段：主席综合")
        third = await stage3_synthesize_final(job["prompt"], first, second, chairman=job["chairman"], max_tokens=1024)
        if third.get("response", "").startswith("Error:"):
            raise RuntimeError("主席模型综合失败，请检查登录权限或配额。")
        self.emit(job, "stage", stage="synthesis", data=third)
        return {"stage1": first, "stage2": second, "stage3": third, "metadata": metadata}

    def worker_command(self, job, output):
        mode = job["mode"]
        python = TOOLS / (".venv-" + mode) / "bin/python"
        script = TOOLS / "examples" / ("camel_dialogue.py" if mode == "camel" else "crewai_team.py")
        if not python.exists() or not script.exists():
            raise RuntimeError(f"{mode} 的独立运行环境尚未安装。")
        model_flags = ["--assistant-model", "--user-model"] if mode == "camel" else ["--researcher-model", "--reviewer-model"]
        # User prompts beginning '-' cannot become options: put task after '--'.
        args = [str(python), "-u", str(script), model_flags[0], job["models"][0], model_flags[1], job["models"][1], "--output", str(output)]
        if mode == "camel":
            args.extend(["--turns", str(job["rounds"])])
        args.extend(["--", job["prompt"]])
        return args

    async def terminate(self, process):
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def worker(self, job):
        output = self.directory / (job["id"] + ".output")
        command = self.worker_command(job, output)
        environment = {**os.environ, "CREWAI_TRACING_ENABLED": "false", "OTEL_SDK_DISABLED": "true", "PYTHONUNBUFFERED": "1"}
        creation = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=TOOLS, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True))
        try:
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Even cancellation during process creation must reap this owned child.
            process = await creation
            await self.terminate(process)
            raise
        self.processes[job["id"]] = process
        try:
            self.emit(job, "log", text=f"{job['mode']} 工作进程已启动，角色模型：" + "、".join(job["models"]))
            tail = []

            async def read_logs():
                buffer = ""
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    buffer += decoder.decode(chunk)
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    if len(buffer) > 4096:
                        lines.append(buffer[:4096])
                        buffer = ""
                    for line in lines:
                        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
                        if not clean or clean.startswith("Saved:"):
                            continue
                        tail.append(clean)
                        del tail[:-10]
                        if not re.search(r"token|authorization|bearer|api[_ -]?key|GOCSPX|sk-[\w-]+", clean, re.I):
                            self.emit(job, "log", text=clean[:2000])
                buffer += decoder.decode(b"", final=True)
                if buffer.strip():
                    tail.append(buffer.strip())
                await process.wait()

            async with asyncio.timeout(1200):
                await read_logs()
            if process.returncode != 0:
                diagnostic = tail[-1] if tail else f"exit {process.returncode}"
                raise RuntimeError(f"{job['mode']} 运行失败：" + public_error(RuntimeError(diagnostic)))
            if not output.exists():
                raise RuntimeError(f"{job['mode']} 未生成任务结果。")
            result = json.loads(output.read_text())
            if job["mode"] == "camel" and not result.get("messages"):
                raise RuntimeError("CAMEL 没有返回对话。")
            if job["mode"] == "crewai" and not result.get("answer"):
                raise RuntimeError("CrewAI 没有返回最终答案。")
            return result
        finally:
            await self.terminate(process)
            self.processes.pop(job["id"], None)
            output.unlink(missing_ok=True)

    async def cancel(self, job_id):
        job = self.get(job_id)
        if job["status"] in TERMINAL:
            return copy.deepcopy(job)
        task = self.tasks.get(job_id)
        job["status"] = "cancelled"
        self.emit(job, "cancelled")
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(job_id, None)
        return copy.deepcopy(job)

    async def shutdown(self):
        await asyncio.gather(*(self.cancel(job_id) for job_id in list(self.tasks)), return_exceptions=True)

    async def events(self, job_id, after=0):
        job = self.get(job_id)
        wakeup = self.wakeups[job_id]
        while True:
            wakeup.clear()
            pending = [event for event in job["events"] if event["seq"] > after]
            for event in pending:
                after = event["seq"]
                yield f"id: {after}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if job["status"] in TERMINAL:
                return
            try:
                await asyncio.wait_for(wakeup.wait(), 15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"


manager = JobManager()


def export_result(job):
    """Reconstruct interrupted/cancelled partial results from persisted events."""
    if job.get("result") is not None:
        return job["result"]
    result = {}
    for event in job.get("events", []):
        if event["type"] == "delta":
            responses = result.setdefault("responses", [])
            response = next((item for item in responses if item["model"] == event["model"]), None)
            if response is None:
                response = {"model": event["model"], "text": ""}
                responses.append(response)
            response["text"] += event.get("text", "")
        elif event["type"] == "stage":
            key = {"responses": "stage1", "rankings": "stage2", "synthesis": "stage3"}.get(event["stage"])
            if key:
                result[key] = event["data"]
            if event.get("metadata"):
                result["metadata"] = event["metadata"]
        elif event["type"] == "complete":
            result = event["result"]
    return result


def markdown_export(job):
    modes = {"chat": "多模型聊天", "council": "匿名评审 · LLM Council", "camel": "角色对话 · CAMEL", "crewai": "任务协作 · CrewAI"}
    statuses = {"queued": "等待开始", "running": "运行中", "complete": "已完成", "error": "失败", "cancelled": "已停止", "interrupted": "服务重启中断"}
    lines = [f"# {modes.get(job['mode'], job['mode'])}", "", f"任务：{job['prompt']}", "", f"时间（UTC）：{job['created_at']}", f"模型：{'、'.join(job['models'])}", f"状态：{statuses.get(job['status'], job['status'])}", ""]
    result = export_result(job)

    def append_response(response, field="response", heading="###"):
        lines.extend([f"{heading} {response['model']}", ""])
        if response.get("error"):
            lines.extend(["调用失败：" + response["error"], ""])
        text = response.get(field) or response.get("response") or ""
        if text:
            lines.extend([text, ""])

    if job["mode"] == "chat":
        for response in result.get("responses", []):
            append_response(response, "text", "##")
    elif job["mode"] == "council":
        lines.extend(["## 独立回答", ""])
        for response in result.get("stage1", []):
            append_response(response)
        lines.extend(["## 匿名互评", ""])
        for response in result.get("stage2", []):
            append_response(response, "ranking")
        metadata = result.get("metadata", {})
        if metadata.get("label_to_model"):
            lines.extend(["### 匿名标签对应模型", ""])
            lines.extend(f"- {label}：{model}" for label, model in metadata["label_to_model"].items())
            lines.append("")
        if metadata.get("aggregate_rankings"):
            lines.extend(["### 综合排序", ""])
            for index, ranking in enumerate(metadata["aggregate_rankings"], 1):
                lines.append(f"{index}. {ranking['model']}：平均名次 {ranking['average_rank']}，有效评审 {ranking['rankings_count']} 次")
            lines.append("")
        for field, label in [("failed_models", "未返回回答"), ("failed_reviewers", "未返回评审")]:
            if metadata.get(field):
                lines.extend([f"{label}：{'、'.join(metadata[field])}", ""])
        if result.get("stage3"):
            lines.extend(["## 主席结论", ""])
            append_response(result["stage3"])
    elif job["mode"] == "camel":
        lines.extend([f"讨论轮数：{job.get('rounds', 2)}", ""])
        for message in result.get("messages", []):
            role = {"reviewer": "审稿人", "researcher": "研究员"}.get(message["role"], message["role"])
            label = role + (f"（{message['model']}）" if message.get("model") else "")
            lines.extend([f"## {label}", "", message.get("content", ""), ""])
    elif job["mode"] == "crewai":
        for index, task in enumerate(result.get("tasks", []), 1):
            if isinstance(task, dict):
                label = task.get("name") or task.get("agent") or task.get("description") or f"协作任务 {index}"
                text = task.get("output") or task.get("result") or task.get("raw") or ""
            else:
                label, text = f"协作任务 {index}", str(task)
            lines.extend([f"## {label}", "", text, ""])
        if result.get("answer"):
            lines.extend(["## 协作结果", "", result["answer"], ""])
    if job.get("error"):
        lines.extend(["## 运行错误", "", job["error"], ""])
    if job["status"] in {"cancelled", "interrupted", "error"}:
        lines.extend(["> 此任务未完整完成；已保存内容可能仅为部分结果。", ""])
    logs = [event["text"] for event in job.get("events", []) if event["type"] == "log"]
    if logs:
        lines.extend(["## 运行记录", "", *logs, ""])
    return "\n".join(lines)


@router.get("/status")
async def status():
    try:
        catalog = await model_catalog()
        gateway = {"online": True}
    except Exception as exc:
        catalog = []
        gateway = {"online": False, "error": public_error(exc)}
    auth = {"codex": [], "antigravity": []}
    for path in (TOOLS / ".private/auth").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            if record.get("type") in auth and not record.get("disabled", False):
                auth[record["type"]].append({"expires_at": record.get("expired") or record.get("expiry") or record.get("expires_at")})
        except (OSError, ValueError, TypeError):
            continue
    providers = []
    for identifier, label in [("codex", "ChatGPT"), ("antigravity", "Gemini")]:
        available = [model for model in catalog if provider_for(model) == identifier]
        provider = {"id": identifier, "label": label, "authenticated": bool(auth[identifier]), "available_models": available, "verified_models": [model for model in available if model in VERIFIED_MODELS]}
        if auth[identifier] and auth[identifier][0].get("expires_at"):
            provider["expires_at"] = auth[identifier][0]["expires_at"]
        providers.append(provider)
    providers.append({"id": "claude", "label": "Claude", "authenticated": False, "available_models": [], "verified_models": [], "reason": "Claude 订阅 OAuth 未接入；可继续使用官方应用。"})
    tools = {}
    for identifier, label in [("chat", "多模型聊天"), ("council", "LLM Council"), ("camel", "CAMEL"), ("crewai", "CrewAI")]:
        installed = identifier in {"chat", "council"} or (TOOLS / (".venv-" + identifier) / "bin/python").exists()
        tools[identifier] = {"available": installed and gateway["online"], "label": label}
        if not installed:
            tools[identifier]["reason"] = "独立运行环境尚未安装"
        elif not gateway["online"]:
            tools[identifier]["reason"] = "本地模型网关未启动"
    return {"gateway": gateway, "providers": providers, "models": [{"id": model, "provider": provider_for(model), "verified": model in VERIFIED_MODELS} for model in catalog], "tools": tools}


@router.get("/jobs")
async def list_jobs():
    jobs = sorted(manager.jobs.values(), key=lambda job: job["created_at"], reverse=True)
    return [{key: value for key, value in job.items() if key not in {"events", "result"}} for job in jobs[:100]]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return copy.deepcopy(manager.get(job_id))


@router.get("/jobs/{job_id}/export")
async def export_job(job_id: str, format: Literal["md", "json"] = Query(default="md")):
    job = copy.deepcopy(manager.get(job_id))
    filename = f"ModelDesk-{job['mode']}-{job['id']}.{format}"
    if format == "json":
        content = json.dumps(job, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        content = markdown_export(job)
        media_type = "text/markdown"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/jobs", status_code=202)
async def create_job(request: JobRequest):
    if request.mode in {"camel", "crewai"} and len(request.models) != 2:
        raise HTTPException(422, "CAMEL 和 CrewAI 需要分别为两个角色选择模型。")
    if request.mode == "council" and len(request.models) < 2:
        raise HTTPException(422, "议会至少需要两个成员模型。")
    try:
        catalog = await model_catalog()
    except Exception as exc:
        raise HTTPException(503, public_error(exc)) from exc
    selected = request.models + ([request.chairman] if request.mode == "council" and request.chairman else [])
    invalid = [model for model in selected if model not in catalog]
    if invalid:
        raise HTTPException(422, "网关未提供这些模型：" + "、".join(invalid))
    if request.mode in {"camel", "crewai"} and not (TOOLS / (".venv-" + request.mode) / "bin/python").exists():
        raise HTTPException(503, "该框架的独立运行环境尚未安装。")
    return manager.create(request)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return await manager.cancel(job_id)


@router.get("/jobs/{job_id}/events")
async def events(job_id: str, after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)):
    manager.get(job_id)
    if last_event_id:
        try:
            after = max(after, int(last_event_id))
        except ValueError:
            pass
    return StreamingResponse(manager.events(job_id, after), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
