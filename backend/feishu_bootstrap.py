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

from .feishu_store import parse_setup_command

PLAN_TTL_MINUTES = 5
SETUP_HOURLY_LIMIT = 3
TEAM_DESCRIPTION = "由飞书自助开通创建的默认团队"


def _default_roster_models():
    """First two text models from the first enabled connection, in catalog order."""
    from .model_connections import model_catalog as connection_models
    from .model_catalog import model_capability
    try:
        catalog = [item["id"] for item in connection_models() if not item.get("testing")]
    except (OSError, RuntimeError, ValueError):
        return []
    text = [model for model in catalog if model_capability(model) == "text"]
    return text[:2]


def _roster(models):
    from .harness.models import TeamMember
    return [TeamMember.model_validate({"id": "lead", "name": "统筹", "model": models[0],
                                       "role": "理解目标、拆解任务并汇总交付", "required": True}),
            TeamMember.model_validate({"id": "worker", "name": "执行", "model": models[1],
                                       "role": "执行具体研究与写作，给出可交付内容", "required": True})]


class BootstrapError(Exception):
    """Reported to the owner as text; never leaks credentials or raw internals."""


class Bootstrap:
    def __init__(self, store, *, room_store=None, team_store=None, project_store=None, catalog=_default_roster_models):
        self.store = store
        self.room_store, self.team_store, self.project_store = room_store, team_store, project_store
        self.catalog = catalog

    # ---- plan lifecycle -------------------------------------------------

    def create_plan(self, config, record, name, models):
        if not models:
            models = self.catalog()
            if len(models) < 2:
                raise BootstrapError("尚未配置可用的模型连接，无法自助开通。请先在本机 ModelDesk 的“模型连接”里启用至少一条连接。")
        if len(set(models)) != len(models):
            raise BootstrapError("模型列表中有重复项，请修改后重试。")
        if len(models) > 4:
            raise BootstrapError("最多支持 4 个模型，请减少后重试。")
        from .model_catalog import model_capability
        if any(model_capability(model) != "text" for model in models):
            raise BootstrapError("仅支持文字推理模型，生图或非文本模型不能作为团队成员。")
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
        return {"code": code, "project_name": name, "models": models}

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
        from .harness.models import TeamRequest
        from .harness.manager import manager as harness
        from .projects import ProjectRequest, store as default_projects
        rooms = self.room_store or default_rooms
        teams = self.team_store or harness.teams
        projects = self.project_store or default_projects
        name, models = payload["project_name"], payload["models"]
        created = {"project": None, "team": None, "room": None}
        try:
            request = ProjectRequest(name=name, description="由飞书自助开通创建", instructions="")
            project = projects.register(request.name, request.path, None, request.description, request.instructions)
            created["project"] = project
            team = teams.put(TeamRequest(name=name + " 团队", description=TEAM_DESCRIPTION, project_id=project["id"], members=_roster(models)))
            created["team"] = team
            room = rooms.create(RoomRequest(title=name, project_id=project["id"], team_id=team["id"]), teams)
            created["room"] = room
            binding = {"chat_id": record["chat_id"], "name": "飞书私聊 · " + name, "project_id": project["id"],
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

    def handle(self, record, config):
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
            plan = self.create_plan(config, record, argument, models)
            return ("即将创建（尚未执行）：\n· 项目：" + plan["project_name"] + "\n· 团队：" + plan["project_name"]
                    + " 团队 · 成员模型：" + "、".join(plan["models"]) + "\n· 项目群：" + plan["project_name"]
                    + "\n· 绑定当前私聊会话（允许自然语言派发任务）\n确认请回复 /modeldesk confirm "
                    + plan["code"] + "（5 分钟内有效，一次性使用）。"), None
        if action == "confirm":
            if not argument:
                return "请提供 4 位确认码：/modeldesk confirm 1234", None
            payload = self.redeem_plan(config, record, argument)
            result = self.execute_plan(config, record, payload)
            project_id, room_id = result["project_id"], result["room_id"]
            return ("开通完成：\n· 项目：" + payload["project_name"] + "\n· 项目 ID：" + str(project_id)
                    + "\n· 团队：" + payload["project_name"] + " 团队\n· 项目群 ID：" + str(room_id)
                    + "\n· 当前会话已绑定并允许自然语言派发任务。\n现在可以直接发任务，例如：\n帮我完成一份竞品分析报告\n或使用指令：/modeldesk task 任务内容、/modeldesk status。"), result
        return None, None
