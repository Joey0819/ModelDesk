"""Export a selected, observed deliverable without exporting the whole transcript."""
import json
import re
from fastapi import HTTPException


def text(value):
    return value if isinstance(value, str) else ""


def selected_artifact(job, identifier):
    result = job.get("result") or {}
    mode = job["mode"]
    candidates = {}

    def add(key, title, content, *, partial=False, source="已保存的模型输出", code=False):
        if not text(content).strip():
            return
        candidates[key] = {"title": title, "content": content, "partial": partial,
                           "source": source, "code": code or bool(re.match(r"\s*(?:<!doctype\s+html|<html\b|<script\b|<style\b|<iframe\b|<svg\b)", content, re.I))}

    if mode == "chat":
        for response in result.get("responses", []):
            add(f"chat:{response.get('model')}", response.get("model") or "模型回答",
                text(response.get("text")) or text(response.get("response")),
                partial=bool(response.get("error") or response.get("incomplete")), source="独立模型回答")
    elif mode == "council":
        synthesis = result.get("stage3") or {}
        add("council:synthesis", "主席综合结论", synthesis.get("response"), source="LLM Council 第三阶段")
        if not candidates:
            for response in result.get("stage1", []):
                add(f"council:response:{response.get('model')}", "独立回答", response.get("response"), partial=True, source="尚未形成主席结论的独立回答")
    elif mode in {"camel", "group"}:
        latest = {}
        messages = result.get("messages", [])
        def identity(message):
            return (message.get("member_id") or message.get("model")) if mode == "group" else message.get("role") or message.get("model")
        for index, message in enumerate(messages):
            if text(message.get("content")).strip() and not re.search(r"任务规划|任务细化|模型传输重试|原生任务结果|原生结果整理", message.get("role") or ""):
                latest[identity(message)] = (index, message)
        for index, message in latest.values():
            later_failed = any(identity(item) == identity(message) and (item.get("error") or item.get("incomplete")) for item in messages[index + 1:])
            add(f"{mode}:{index}", f"最后发言 · {message.get('role') or message.get('model') or '参与角色'}", message["content"],
                partial=bool(message.get("error") or message.get("incomplete") or later_failed), source="角色实际发言，未另行生成综合结论")
    elif mode == "sdk_script":
        add("sdk:answer", "SDK 返回内容", result.get("answer"), source="原生 SDK 实际返回")
        if not candidates and job.get("result") is not None:
            add("sdk:result", "SDK 返回数据", json.dumps(result, ensure_ascii=False, indent=2), source="原生 SDK 实际返回", code=True)
    elif mode == "review":
        if job.get("result") is not None:
            findings = []
            for finding in result.get("findings", []):
                location = f"{finding.get('file', '')}:{finding.get('line_start', '')}"
                if finding.get("line_end") and finding["line_end"] != finding.get("line_start"):
                    location += f"–{finding['line_end']}"
                body = f"### {finding.get('severity', '')} {finding.get('title') or '代码发现'}\n\n{location}\n\n{finding.get('body', '')}"
                if finding.get("evidence"):
                    body += f"\n\n证据：\n\n```\n{finding['evidence']}\n```"
                if finding.get("suggestion"):
                    body += f"\n\n建议：{finding['suggestion']}"
                findings.append(body)
            if not findings:
                findings.append("本次未产生已验证的代码发现。")
            if result.get("unconfirmed"):
                findings.append("### 尚未确认的主张\n\n" + "\n".join(f"- {item.get('model') or '模型'}：{item.get('reason', '')}" + (f" · {item['claim']}" if item.get('claim') else "") for item in result["unconfirmed"]))
            if result.get("coverage", {}).get("disclaimer"):
                findings.append("覆盖范围：" + result["coverage"]["disclaimer"])
            add("review:findings", "代码评审报告", "\n\n".join(findings), source="已保存的评审发现与覆盖范围")
    else:
        for index, task in enumerate(result.get("tasks", [])):
            if task.get("status") == "skipped":
                continue
            content = text(task.get("output")) or text(task.get("result")) or text(task.get("raw"))
            add(f"task:{task.get('id') or index}", task.get("title") or text(task.get("description")).split("\n")[0][:54] or task.get("name") or f"任务 {index + 1} 输出", content,
                partial=task.get("status") not in {"complete", "completed"}, source="原生子任务实际输出")
        add("workflow:answer", "协作交付结果", result.get("answer"), source="原生框架实际返回")
        if not candidates:
            for index, event in enumerate(job.get("events", [])):
                native = event.get("native_event") or (event if event.get("type") == "native_event" else {})
                if native.get("event_type") == "task_failed":
                    add(f"failure:{native.get('task_id') or event.get('seq') or index}", "失败子任务的证据记录", native.get("error_message"), partial=True, source="原生 task_failed 事件")
        if not candidates:
            diagnostics = result.get("format_diagnostics") or [{**event, "raw": event.get("raw", event.get("content"))} for event in job.get("events", []) if event.get("phase") in {"research", "finalization", "finalization_error"}]
            for index, entry in enumerate(diagnostics):
                add(f"format:{index}", "原始研究输出", entry.get("raw"), partial=True, source="未形成有效交付的原始运行依据")
    if identifier not in candidates:
        raise HTTPException(404, "当前任务没有这份实际成果。")
    artifact = candidates[identifier]
    artifact["partial"] |= job.get("status") not in {"complete", "completed"} or bool(result.get("partial") or result.get("partial_failure"))
    return artifact


def artifact_markdown(job, identifier):
    artifact = selected_artifact(job, identifier)
    status = "部分输出；整体任务尚未完成" if artifact["partial"] else "已保存的实际输出"
    body = artifact["content"]
    if artifact["code"]:
        fence = "`" * max(3, max((len(match) for match in re.findall(r"`+", body)), default=0) + 1)
        body = f"{fence}\n{body}\n{fence}"
    return f"# {artifact['title']}\n\n> {status}\n> 来源：{artifact['source']}\n> 任务记录：{job['id']}\n\n{body}\n"
