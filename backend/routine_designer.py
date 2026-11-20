"""Guided routine design, pre-automation edge-case walkthrough, and self-improvement reflection.

Read-only model assistance inspired by goal-first, judgment-carrying, closed-loop automation:
- Never executes native agents, tools, or side-effecting deliveries.
- Never escalates allow_native or silently mutates saved routines without user confirmation.
"""
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import group_delivery
from .member_capabilities import member_kind
from .routine_store import RoutineSpec, _normalize_spec_dict, routine_readiness
from .vision import public_text

SPEC_DIFF_FIELDS = (
    ("goal", "预期成果"),
    ("process", "执行步骤"),
    ("judgment", "决策判断与求助边界"),
    ("checks", "验收标准"),
    ("examples", "真实案例与反例"),
    ("correlation_field", "关联追踪编号"),
)


class DesignTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["interview", "optimize"] = "interview"
    message: str = Field(default="", max_length=6000)
    history: list[DesignTurn] = Field(default_factory=list, max_length=16)
    draft: dict | None = None


class WalkthroughRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: RoutineSpec
    scenario: str = Field(default="", max_length=2000)


class _DraftPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    goal: str = ""
    process: str = ""
    judgment: str = ""
    checks: str = ""
    examples: str = ""
    correlation_field: str = ""
    trigger: dict | None = None
    document_formats: list[str] | None = None


class _DesignOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    goal_clarity_note: str = Field(default="", max_length=1000)
    restatement: str = Field(min_length=1, max_length=2500)
    next_question: str = Field(default="", max_length=1000)
    question_focus: Literal["goal", "process", "judgment", "checks", "examples", "trigger", "ready"] = "goal"
    suggestions: list[str] = Field(default_factory=list, max_length=8)
    draft_spec: _DraftPatch = Field(default_factory=_DraftPatch)


class _WalkthroughCase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=120)
    scenario: str = Field(min_length=1, max_length=800)
    predicted_steps: str = Field(min_length=1, max_length=1500)
    decision: Literal["complete", "wait_details", "ask_human"] = "ask_human"
    applied_rule: str = Field(default="", max_length=500)
    gap_detected: str = Field(default="", max_length=600)
    suggested_judgment_rule: str = Field(default="", max_length=600)


class _WalkthroughOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    overall_assessment: str = Field(min_length=1, max_length=1500)
    cases: list[_WalkthroughCase] = Field(min_length=1, max_length=3)


class _ReflectionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    diagnosis: str = Field(min_length=1, max_length=2500)
    changes_summary: list[str] = Field(default_factory=list, max_length=8)
    goal: str | None = None
    process: str | None = None
    judgment: str | None = None
    checks: str | None = None
    examples: str | None = None
    correlation_field: str | None = None


def designer_model(room):
    snapshot = room.get("team_snapshot") or {}
    coordinator = snapshot.get("coordinator_model")
    if isinstance(coordinator, str) and coordinator.strip():
        return coordinator.strip()
    for member in snapshot.get("members") or []:
        if member_kind(member) == "model" and isinstance(member.get("model"), str) and member["model"].strip():
            return member["model"].strip()
    raise HTTPException(409, "当前项目群没有可用的文字模型成员，请先在成员与分工中配置模型")


def _team_summary(room):
    snapshot = room.get("team_snapshot") or {}
    members = snapshot.get("members") or []
    lines = [f"群聊名称：{room.get('title') or '项目群'}"]
    for member in members:
        kind = member_kind(member)
        role = (member.get("role") or "协助完成任务").strip()
        lines.append(f"- {member.get('name') or member.get('id')}（{kind}）：{role}")
    return "\n".join(lines)


def _sanitize_draft_spec(patch: _DraftPatch, existing_draft: dict | None):
    base = _normalize_spec_dict(existing_draft or {})
    # Never allow AI output to elevate allow_native.
    allow_native = bool(base.get("allow_native", False))
    auto_reflect = bool(base.get("auto_reflect", False))
    trigger = dict(base.get("trigger") or {"kind": "manual"})
    if isinstance(patch.trigger, dict) and patch.trigger.get("kind") in (
        "manual", "daily", "weekly", "interval", "board_card", "board_status"
    ):
        candidate = {
            "kind": patch.trigger["kind"],
            "time": patch.trigger.get("time"),
            "timezone": patch.trigger.get("timezone"),
            "weekday": patch.trigger.get("weekday"),
            "every_hours": patch.trigger.get("every_hours"),
            "tag": patch.trigger.get("tag"),
            "board_status": patch.trigger.get("board_status"),
        }
        try:
            validated_trigger = RoutineSpec.model_validate({
                "name": "tmp", "goal": "tmp", "trigger": candidate
            }).trigger.model_dump(mode="json")
            trigger = validated_trigger
        except ValidationError:
            pass

    formats = list(base.get("document_formats") or [])
    if isinstance(patch.document_formats, list):
        valid_formats = [fmt for fmt in patch.document_formats if fmt in ("docx", "pdf", "html")]
        formats = list(dict.fromkeys(valid_formats))

    def _pick(field, limit):
        val = getattr(patch, field, "")
        text = val.strip() if isinstance(val, str) and val.strip() else str(base.get(field) or "").strip()
        return public_text(text)[:limit]

    assembled = {
        "name": _pick("name", 120),
        "goal": _pick("goal", 3000),
        "process": _pick("process", 2500),
        "judgment": _pick("judgment", 2000),
        "checks": _pick("checks", 2500),
        "examples": _pick("examples", 2000),
        "correlation_field": _pick("correlation_field", 80),
        "trigger": trigger,
        "document_formats": formats,
        "allow_native": allow_native,
        "auto_reflect": auto_reflect,
    }
    # Respect combined 7000-char text budget gracefully if AI was verbose.
    budget_fields = ("examples", "judgment", "checks", "process", "goal")
    overflow = sum(len(assembled[k]) for k in budget_fields) - 7000
    for key in budget_fields:
        if overflow <= 0:
            break
        if assembled[key] and key != "goal":
            cut = min(overflow, len(assembled[key]))
            assembled[key] = assembled[key][:-cut].rstrip()
            overflow -= cut
    if overflow > 0 and len(assembled["goal"]) > overflow:
        assembled["goal"] = assembled["goal"][:-overflow].rstrip()
    return assembled


def spec_diff(current_spec, proposed_spec):
    current = _normalize_spec_dict(current_spec)
    proposed = _normalize_spec_dict(proposed_spec)
    diffs = []
    for field, label in SPEC_DIFF_FIELDS:
        before = (current.get(field) or "").strip()
        after = (proposed.get(field) or "").strip()
        if before != after:
            diffs.append({"field": field, "label": label, "before": before, "after": after})
    return diffs


async def _structured_call(room, system, prompt, schema_cls, *, error_label):
    model = designer_model(room)
    try:
        raw, _ = await group_delivery.model_output(
            {"id": "routine-designer"}, model, system, prompt, max_tokens=4096, image_parts=[]
        )
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error
    try:
        parsed = group_delivery.json_document(raw)
        return schema_cls.model_validate(parsed)
    except (ValueError, ValidationError) as error:
        raise HTTPException(502, f"{error_label}返回格式无效，请重试") from error


async def design_routine(room, request: DesignRequest):
    if request.mode == "interview" and not request.message.strip() and not request.history and not request.draft:
        raise HTTPException(422, "请先描述你想自动化的目标、平时怎么做，或先选择一个起点")
    team_info = _team_summary(room)
    if request.mode == "optimize":
        system = (
            "你是项目群自动化设计顾问。请对照用户的「原始目标」审视当前的「执行步骤、判断规则、验收标准、案例和触发方式」。\n"
            "核心原则：\n"
            "1. 目标是最终想得到的结果，步骤只是手段；如果某些步骤冗余、绕远或容易出错，请指出更直接可靠的方法。\n"
            "2. 检查是否缺少隐性判断规则：多来源数据冲突时以谁为准？什么才算真正完成？信息不全时如何记录待补状态而不重复创建？什么时候该停下来求助人？\n"
            "3. 不要执行任何实际任务，也不要自行开启本机工具权限。\n"
            "只输出一个 JSON 对象，字段如下：\n"
            '{"goal_clarity_note": "目标是否清晰、是否把步骤误当成目标的简评", '
            '"restatement": "用简洁白话复述优化后的目标、步骤与判断逻辑，便于用户确认", '
            '"next_question": "如果还有最关键的一个待确认问题就写在这里，否则为空字符串", '
            '"question_focus": "goal|process|judgment|checks|examples|trigger|ready", '
            '"suggestions": ["具体改进点1", "具体改进点2"], '
            '"draft_spec": {"name": "", "goal": "", "process": "", "judgment": "", "checks": "", '
            '"examples": "", "correlation_field": "", "trigger": {"kind": "manual"}, "document_formats": []}}'
        )
    else:
        system = (
            "你是项目群自动化设计访谈助手。帮助用户把日常重复工作整理成可执行、可核验的例程，先对齐方案，不立即执行。\n"
            "工作方法：\n"
            "1. 先明确最终目标（Goal）：如果用户只说了操作动作（如“填表”“发邮件”），温和指出背后的业务结果，并把目标写清。\n"
            "2. 白话复述确认（Restatement）：每次回复先用简洁白话复述你目前理解的目标、步骤和判断规则，让用户纠偏。\n"
            "3. 一次只问一个最关键的问题（next_question）：按顺序补齐隐性经验——①最终目标 ②常用步骤与信息来源 "
            "③决策判断（来源冲突信谁、何时停下来问人、待补信息如何跟进） ④完成验收标准 ⑤历史出错案例与反例 ⑥触发节奏与跨来源关联编号。\n"
            "4. 同步把已确认的信息填入 draft_spec，保持简洁具体，不杜撰用户没提过的外部系统或密钥。\n"
            "5. trigger.kind 仅限 manual/daily/weekly/interval/board_card/board_status；若用户说“每隔两天/每48小时”请用 interval 且设置 every_hours。\n"
            "只输出一个 JSON 对象，字段如下：\n"
            '{"goal_clarity_note": "关于目标清晰度的简短提示（如无问题可简短肯定）", '
            '"restatement": "用白话复述当前理解的目标与流程，供用户确认", '
            '"next_question": "一次只问一个最关键的问题；若信息已充分可留空并建议演练案例", '
            '"question_focus": "goal|process|judgment|checks|examples|trigger|ready", '
            '"suggestions": ["可选的流程简化或自动化建议"], '
            '"draft_spec": {"name": "", "goal": "", "process": "", "judgment": "", "checks": "", '
            '"examples": "", "correlation_field": "", "trigger": {"kind": "manual"}, "document_formats": []}}'
        )

    parts = [f"当前项目群团队：\n{team_info}"]
    if request.draft:
        parts.append("当前已有例程草稿（JSON）：\n" + json.dumps(_normalize_spec_dict(request.draft), ensure_ascii=False))
    if request.history:
        turns = "\n".join(
            f"{'用户' if item.role == 'user' else '设计助手'}：{item.content}" for item in request.history[-12:]
        )
        parts.append(f"此前访谈记录：\n{turns}")
    if request.message.strip():
        parts.append(f"用户本轮补充：\n{request.message.strip()}")
    elif request.mode == "optimize":
        parts.append("请对照原始目标审视当前草稿，指出可以简化、补强判断规则或提升可靠性的地方。")

    result = await _structured_call(room, system, "\n\n".join(parts), _DesignOutput, error_label="例程设计助手")
    draft_spec = _sanitize_draft_spec(result.draft_spec, request.draft)
    return {
        "goal_clarity_note": public_text(result.goal_clarity_note.strip()),
        "restatement": public_text(result.restatement.strip()),
        "next_question": public_text(result.next_question.strip()),
        "question_focus": result.question_focus,
        "suggestions": [public_text(item.strip()) for item in result.suggestions if item.strip()],
        "draft_spec": draft_spec,
        "readiness": routine_readiness(draft_spec),
    }


async def walkthrough_routine(room, request: WalkthroughRequest):
    spec = request.spec.model_dump(mode="json")
    team_info = _team_summary(room)
    system = (
        "你是项目群自动化演练模拟官。在真正开启自动化前，用具体案例（尤其是边界情况、来源冲突、信息缺失或历史易错场景）"
        "推演当前例程会如何执行，帮助用户发现尚未说清的判断规则。本次演练不会执行任何真实工具或外部写入。\n"
        "请只输出一个 JSON 对象：\n"
        '{"overall_assessment": "整体就绪度简评与开启自动化前最值得补齐的点", '
        '"cases": [{"title": "案例标题", "scenario": "具体触发情境", '
        '"predicted_steps": "按当前配置会采取的处理步骤", '
        '"decision": "complete|wait_details|ask_human", '
        '"applied_rule": "依据了配置中的哪条规则，或注明配置未明确约定", '
        '"gap_detected": "暴露出的盲区或歧义（如无则为空字符串）", '
        '"suggested_judgment_rule": "如存在盲区，给出一条可直接加入「决策判断与求助边界」的简洁规则"}]}'
    )
    parts = [
        f"当前项目群团队：\n{team_info}",
        "待演练的例程配置：\n" + json.dumps(spec, ensure_ascii=False),
    ]
    if request.scenario.strip():
        parts.append(f"请重点演练用户指定的这个具体情境（可附带 1 个关联边界情境）：\n{request.scenario.strip()}")
    else:
        parts.append("请设计 2–3 个最容易出问题的真实边界案例（例如：多来源信息冲突、关键细节缺失需等待补充、验收不达标或重复触发），逐一推演当前配置的表现。")

    result = await _structured_call(room, system, "\n\n".join(parts), _WalkthroughOutput, error_label="案例演练模拟")
    return {
        "overall_assessment": public_text(result.overall_assessment.strip()),
        "cases": [
            {
                "title": public_text(case.title.strip()),
                "scenario": public_text(case.scenario.strip()),
                "predicted_steps": public_text(case.predicted_steps.strip()),
                "decision": case.decision,
                "applied_rule": public_text(case.applied_rule.strip()),
                "gap_detected": public_text(case.gap_detected.strip()),
                "suggested_judgment_rule": public_text(case.suggested_judgment_rule.strip()),
            }
            for case in result.cases
        ],
    }


async def reflect_on_routine(room, routine, runs_evidence, feedback_items):
    current = _normalize_spec_dict(routine)
    team_info = _team_summary(room)
    system = (
        "你是项目群自动化自改进复盘分析师。请基于例程的真实运行记录、耗时指标、交付审阅问题和用户/外部反馈，"
        "对照原始目标诊断根因，并提出克制、精准的配置改进建议。\n"
        "严格约束：\n"
        "1. 只能依据给出的运行证据与反馈分析，不得编造未发生的故障。\n"
        "2. 可以改进 goal、process、judgment、checks、examples、correlation_field；保留原有用内容，不要无故删掉已有规则。\n"
        "3. 如果多条运行共享同一关联编号（correlation_key）并反复出问题，总结成明确的判断规则或反例。\n"
        "4. 如果现有配置已经能覆盖近期证据无需修改，各字段返回 null 并在 diagnosis 说明原因。\n"
        "只输出一个 JSON 对象：\n"
        '{"diagnosis": "基于运行指标、交付审查和反馈的根因诊断", '
        '"changes_summary": ["改动点1及原因", "改动点2及原因"], '
        '"goal": null或更新后的完整文本, "process": null或更新后的完整文本, '
        '"judgment": null或更新后的完整文本, "checks": null或更新后的完整文本, '
        '"examples": null或更新后的完整文本, "correlation_field": null或更新后的字段名}'
    )
    compact_spec = {
        key: current.get(key)
        for key in ("name", "goal", "process", "judgment", "checks", "examples", "correlation_field", "trigger")
    }
    prompt = "\n\n".join([
        f"当前项目群团队：\n{team_info}",
        "当前例程配置：\n" + json.dumps(compact_spec, ensure_ascii=False),
        "近期运行记录与交付核验证据：\n" + json.dumps(runs_evidence[:10], ensure_ascii=False),
        "已收集的用户/外部反馈：\n" + json.dumps(feedback_items[:15], ensure_ascii=False),
    ])
    result = await _structured_call(room, system, prompt, _ReflectionOutput, error_label="自改进复盘分析")

    proposed = {
        "name": current["name"],
        "goal": current["goal"],
        "process": current["process"],
        "judgment": current["judgment"],
        "checks": current["checks"],
        "examples": current["examples"],
        "correlation_field": current["correlation_field"],
        "trigger": current["trigger"],
        "document_formats": current["document_formats"],
        "allow_native": bool(current["allow_native"]),
        "auto_reflect": bool(current["auto_reflect"]),
    }
    limits = {"goal": 3000, "process": 2500, "judgment": 2000, "checks": 2500, "examples": 2000, "correlation_field": 80}
    for field, limit in limits.items():
        candidate = getattr(result, field)
        if isinstance(candidate, str) and candidate.strip():
            proposed[field] = public_text(candidate.strip())[:limit]

    # Enforce 7000-char combined text budget gracefully.
    budget_fields = ("examples", "judgment", "checks", "process", "goal")
    overflow = sum(len(proposed[k]) for k in budget_fields) - 7000
    for key in budget_fields:
        if overflow <= 0:
            break
        if proposed[key] and key != "goal":
            cut = min(overflow, len(proposed[key]))
            proposed[key] = proposed[key][:-cut].rstrip()
            overflow -= cut
    if overflow > 0 and len(proposed["goal"]) > overflow:
        proposed["goal"] = proposed["goal"][:-overflow].rstrip()

    diffs = spec_diff(current, proposed)
    return {
        "changed": bool(diffs),
        "diagnosis": public_text(result.diagnosis.strip()),
        "changes_summary": [public_text(item.strip()) for item in result.changes_summary if item.strip()],
        "proposed_spec": proposed,
        "diff": diffs,
    }
