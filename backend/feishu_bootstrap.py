"""Two-step, owner-gated Feishu self-service bootstrap for projects, teams, rooms and bindings.

Security model, enforced before any of this runs:
- ingest() only admits an unbound chat when it is a private chat, the sender is
  in the config-level ``owner_sender_ids`` allowlist, and the text is exactly a
  ``/modeldesk setup`` or ``/modeldesk confirm`` directive. Group chats can
  never bootstrap, so dragging the bot into a group cannot self-authorize.
- setup only stores a plan and returns a 4-digit code. Nothing is created until
  the same chat confirms the code within 5 minutes; each code is single-use and
  each chat has at most one pending plan.
- The plan payload never contains app_secret. Only command metadata (project
  name, model ids) is persisted.
"""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import ValidationError

from .feishu_store import parse_setup_command
from .harness.models import TeamMember, TeamRequest

PLAN_TTL_MINUTES = 5
SETUP_HOURLY_LIMIT = 3
TEAM_DESCRIPTION = "由飞书自助开通创建的默认团队"


async def _default_roster_models():
    """Prefer saved connections, then available models from the subscription gateway."""
    from .config import COUNCIL_MODELS
    from .model_catalog import model_capability
    from .model_connections import model_catalog as connection_models
    from .workbench import subscription_catalog
    try:
        saved = [item["id"] for item in connection_models()
                 if not item.get("testing") and item["capability"] == "text"]
    except (OSError, RuntimeError, ValueError):
        saved = []
    if saved:
        return saved[:2]
    try:
        available = [model for model in await subscription_catalog() if model_capability(model) == "text"]
    except (httpx.HTTPError, ValueError):
        return []
    preferred = [model for model in COUNCIL_MODELS if model in available]
    return list(dict.fromkeys(preferred + available))[:2]


def _team_request(name, models):
    roles = [
        ("lead", "统筹", "理解目标、拆解任务并汇总交付"),
        ("worker", "执行", "执行具体研究与写作，给出可交付内容"),
        ("reviewer", "审阅", "核查实际交付，指出错误并提出具体修订"),
        ("researcher", "研究", "补充研究依据，将可核对的材料交给团队"),
    ]
    # Project groups support separate roles using one model connection.
    assignments = models if len(models) != 1 else models * 2
    members = [TeamMember(id=identifier, name=label, role=role, model=model, required=True)
               for (identifier, label, role), model in zip(roles, assignments)]
    team_name = name + " 团队" if len(name) <= 97 else name
    return TeamRequest(name=team_name, description=TEAM_DESCRIPTION, members=members)


class BootstrapError(Exception):
    """Reported to the owner as text; never leaks credentials or raw internals."""


class Bootstrap:
    def __init__(self, store, *, room_store=None, team_store=None, project_store=None, catalog=_default_roster_models):
        self.store = store
        self.room_store, self.team_store, self.project_store = room_store, team_store, project_store
        self.catalog = catalog

    # ---- plan lifecycle -------------------------------------------------

    async def create_plan(self, config, record, name, models):
        if not models:
            models = await self.catalog()
            if not models:
                raise BootstrapError("尚未找到可用的文字模型。请在本机 ModelDesk 启动订阅网关或启用模型连接。")
        if len(set(models)) != len(models):
            raise BootstrapError("模型列表中有重复项，请修改后重试。")
        if len(models) > 4:
            raise BootstrapError("最多支持 4 个模型，请减少后重试。")
        from .model_catalog import model_capability
        if any(model_capability(model) != "text" for model in models):
            raise BootstrapError("仅支持文字推理模型，生图或非文本模型不能作为团队成员。")
        try:
            team = _team_request(name, models)
        except ValidationError:
            raise BootstrapError("项目名或成员模型无效，请检查名称和模型 ID 后重试。") from None
        app_id, chat_id, sender = config["app_id"], record["chat_id"], record["sender_id"]
        stamp = datetime.now(timezone.utc)
        code = f"{secrets.randbelow(10000):04d}"
        payload = {"project_name": name, "models": models, "sender_id": sender}
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "modeldesk:feishu-plan:" + app_id + ":" + chat_id + ":" + stamp.isoformat()))
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Rate limit counts every plan created in the last hour, including
            # pending ones, before any replacement is made.
            since = (stamp - timedelta(hours=1)).isoformat()
            rate = db.execute("SELECT count(*) FROM plans WHERE app_id=? AND chat_id=? AND created_at>?", (app_id, chat_id, since)).fetchone()[0]
            if rate >= SETUP_HOURLY_LIMIT:
                raise BootstrapError("此会话本小时内的开通次数已用完（每小时最多 3 次），请稍后再试。")
            # One pending plan per chat at a time; superseded (never deleted)
            # plans still count toward the hourly budget.
            db.execute("UPDATE plans SET used_at=? WHERE app_id=? AND chat_id=? AND used_at IS NULL",
                       (stamp.isoformat(), app_id, chat_id))
            db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?)",
                       (identifier, app_id, chat_id, sender, code, json.dumps(payload, ensure_ascii=False),
                        stamp.isoformat(), (stamp + timedelta(minutes=PLAN_TTL_MINUTES)).isoformat(), None))
        return {"code": code, "project_name": name, "team": team}

    def redeem_plan(self, config, record, code):
        app_id, chat_id, sender = config["app_id"], record["chat_id"], record["sender_id"]
        stamp = datetime.now(timezone.utc)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,code,payload,expires_at,sender_id FROM plans WHERE app_id=? AND chat_id=? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1", (app_id, chat_id)).fetchone()
            if not row:
                raise BootstrapError("没有待确认的开通计划。请先发送 /modeldesk setup 项目名。")
            if row["code"] != code or row["sender_id"] != sender:
                raise BootstrapError("确认码不正确，请核对后重试。")
            if datetime.fromisoformat(row["expires_at"]) < stamp:
                raise BootstrapError("确认码已过期（5 分钟有效）。请重新发送 /modeldesk setup。")
            payload = json.loads(row["payload"])
            db.execute("UPDATE plans SET used_at=? WHERE id=?", (stamp.isoformat(), row["id"]))
        return payload

    # ---- execution ------------------------------------------------------

    def execute_plan(self, config, record, payload):
        """Create project → team → room → binding, rolling back on failure."""
        from .groups import RoomRequest, store as default_rooms
        from .harness.manager import manager as harness
        from .projects import ProjectRequest, store as default_projects
        rooms = self.room_store or default_rooms
        teams = self.team_store or harness.teams
        projects = self.project_store or default_projects
        name, models = payload["project_name"], payload["models"]
        created = {"project": None, "team": None, "room": None}
        try:
            request = ProjectRequest(name=name, description="由飞书自助开通创建", instructions="")
            team_request = _team_request(name, models)
            project = projects.register(request.name, request.path, None, request.description, request.instructions)
            created["project"] = project
            team_request.project_id = uuid.UUID(project["id"])
            team = teams.put(team_request)
            created["team"] = team
            room = rooms.create(RoomRequest(title=name, project_id=project["id"], team_id=team["id"]), teams)
            created["room"] = room
            label = "飞书私聊 · " + name
            binding = {"chat_id": record["chat_id"], "name": label if len(label) <= 100 else name, "project_id": project["id"],
                       "room_id": room["id"], "allowed_sender_ids": [payload["sender_id"]],
                       "allow_native": False, "natural_language_tasks": True}
            saved = self.store.save_binding(config, binding)
            return next(item for item in saved["bindings"] if item["chat_id"] == binding["chat_id"])
        except Exception as error:
            self._rollback(created, rooms, teams, projects)
            from .feishu_sdk import safe_error
            raise BootstrapError("开通失败：" + safe_error(error) + " 已清理未完成的资源；请稍后重试或在本机检查。") from None

    def _rollback(self, created, rooms, teams, projects):
        if created["room"]:
            try:
                rooms.hard_delete(created["room"]["id"], created["room"]["project_id"])
            except Exception:
                pass  # Leave partial state for local inspection; never widen the blast radius.
        if created["team"]:
            try:
                teams.archive(created["team"]["id"], created["team"]["project_id"])
            except Exception:
                pass
        if created["project"]:
            try:
                projects.remove(created["project"]["id"])
            except Exception:
                pass

    # ---- command routing -------------------------------------------------

    async def handle(self, record, config):
        """Handle an admitted bootstrap command; returns (reply_text, new_binding).

        new_binding is set when this call wrote a binding (confirm path), so
        the caller can re-validate the event record against the rotated
        configuration revision before replying.
        """
        if record.get("input_mode") != "bootstrap":
            return None, None
        owners = config.get("owner_sender_ids") or []
        if record["sender_id"] not in owners:
            return None, None  # Unreachable through ingest, but re-checked here.
        if not record["binding"] and record["app_id"] != config["app_id"]:
            return "飞书应用配置已变更，请重新发送开通指令。", None
        command = record["command"]
        parsed = parse_setup_command(command)
        if not parsed:
            return "使用方法：\n/modeldesk setup 项目名（可选 with 模型1,模型2）\n/modeldesk confirm 4位确认码\n/modeldesk unlink", None
        action, argument, models = parsed
        if action == "setup":
            if not argument:
                return "请提供项目名称：/modeldesk setup 项目名（可选 with 模型1,模型2）", None
            plan = await self.create_plan(config, record, argument, models)
            team = plan["team"]
            assignments = "\n".join("  " + member.name + " → " + member.model for member in team.members)
            return ("即将创建（尚未执行）：\n· 项目：" + plan["project_name"] + "\n· 团队：" + team.name
                    + "\n" + assignments + "\n· 项目群：" + plan["project_name"]
                    + "\n· 绑定当前私聊会话（允许自然语言派发任务）\n确认请回复 /modeldesk confirm "
                    + plan["code"] + "（5 分钟内有效，一次性使用）。"), None
        if action == "confirm":
            if not argument:
                return "请提供 4 位确认码：/modeldesk confirm 1234", None
            payload = self.redeem_plan(config, record, argument)
            result = self.execute_plan(config, record, payload)
            project_id, room_id = result["project_id"], result["room_id"]
            return ("开通完成：\n· 项目：" + payload["project_name"] + "\n· 项目 ID：" + str(project_id)
                    + "\n· 项目群 ID：" + str(room_id)
                    + "\n· 当前会话已绑定并允许自然语言派发任务。\n现在可以直接发任务，例如：\n帮我完成一份竞品分析报告\n或使用指令：/modeldesk task 任务内容、/modeldesk status。"), result
        return None, None
