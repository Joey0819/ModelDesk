"""User plans and bounded execution policies, independent of HTTP and engines."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..workflow_config import WorkflowConfig


Mode = Literal["chat", "council", "camel", "crewai", "group", "review", "camel_workforce", "crewai_flow"]


class TeamMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["model", "codex", "claude", "muse"] = "model"
    model: str | None = Field(default=None, max_length=200)
    role: str = Field(default="", max_length=1600)

    @field_validator("name", "model")
    @classmethod
    def nonempty_value(cls, value):
        if value is None:
            return None
        if any(ord(char) < 32 for char in value):
            raise ValueError("成员名称与模型标识不能为空或含控制字符")
        return value.strip()

    @model_validator(mode="after")
    def no_credentials(self):
        from ..attachments import secret_text
        if not self.name or (self.kind == "model" and not self.model):
            raise ValueError("模型成员需要名称与模型标识")
        if self.kind == "muse" and self.model:
            raise ValueError("Muse 使用自己的应用与账户，不配置底层模型")
        if self.kind in {"codex", "claude"} and self.model and (self.model.startswith("-") or any(char.isspace() for char in self.model)):
            raise ValueError("CLI 模型标识不能含空格或以选项符号开头")
        if not self.model:
            self.model = None
        if secret_text(self.name + "\n" + (self.model or "") + "\n" + self.role):
            raise ValueError("团队配置不能包含凭据；请使用现有模型连接")
        return self


def validate_members(members, *, distinct_models=True):
    if len({member.id for member in members}) != len(members):
        raise ValueError("每位团队成员必须使用不同的成员标识")
    if distinct_models and any(member.kind != "model" for member in members):
        raise ValueError("此协作流程仅支持模型成员；包含可执行 Agent 的团队请使用项目群")
    if distinct_models and len({member.model for member in members}) != len(members):
        raise ValueError("协作流程需要不同模型；同一模型的多个角色请在项目群聊中使用")


class TeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    project_id: UUID | None = None
    members: list[TeamMember] = Field(min_length=2, max_length=4)
    coordinator_model: str | None = Field(default=None, max_length=200)

    @field_validator("coordinator_model")
    @classmethod
    def clean_coordinator(cls, value):
        if value is None or not value.strip():
            return None
        if any(ord(char) < 32 for char in value):
            raise ValueError("主控模型标识不能含控制字符")
        return value.strip()

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("请填写团队名称")
        return value.strip()

    @model_validator(mode="after")
    def valid_team(self):
        from ..attachments import secret_text
        # Saved rosters describe people-like roles, not unique model endpoints.
        # Group rooms dispatch by member ID; workflow engines still validate
        # their distinct-model invariant when a saved roster is used for a run.
        validate_members(self.members, distinct_models=False)
        model_ids = {member.model for member in self.members if member.kind == "model"}
        if self.coordinator_model:
            model_ids.add(self.coordinator_model)
        if not model_ids:
            raise ValueError("团队至少需要一位模型成员或一个主控模型")
        if len(model_ids) > 4:
            raise ValueError("成员与主控合计最多使用四个不同模型")
        if secret_text(self.name + "\n" + self.description + "\n" + (self.coordinator_model or "")):
            raise ValueError("团队配置不能包含凭据")
        return self


class StepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    title: str = Field(min_length=1, max_length=100)
    mode: Mode
    instructions: str = Field(default="", max_length=2000)
    models: list[str] | None = Field(default=None, min_length=1, max_length=4)
    workflow: WorkflowConfig | None = None
    engine: Literal["chat", "council", "camel", "crewai"] = "council"
    rounds: int = Field(default=2, ge=1, le=6)
    require_review: bool = False


TEMPLATES = [
    {"id": "research-review", "name": "研究 → 评审 → 交付", "description": "角色共同研究，议会交叉核查，再形成一份可使用的成果。",
     "steps": [
         {"id": "research", "title": "协作研究", "mode": "camel", "instructions": "依据用户提供的材料分析任务，区分已知事实与需要核实的内容。"},
         {"id": "review", "title": "议会评审", "mode": "council", "instructions": "核查前一步的研究，指出不足、矛盾与证据限制，给出改进建议。"},
         {"id": "deliver", "title": "形成交付", "mode": "crewai", "instructions": "根据前面研究和评审完成用户需要的最终成果，保留关键依据与限制。"}]},
    {"id": "write-refine", "name": "初稿 → 改进", "description": "多个模型提出方案，经交叉评审形成完整结果。",
     "steps": [
         {"id": "draft", "title": "并行初稿", "mode": "chat", "instructions": "独立完成任务的初稿，给出具体内容。"},
         {"id": "refine", "title": "综合改进", "mode": "council", "instructions": "比较前一步各模型的初稿，核查错误并给出完整改进版本。"}]},
    {"id": "compare-decide", "name": "讨论 → 决策", "description": "模型交换观点，再由议会形成有依据的结论。",
     "steps": [
         {"id": "discuss", "title": "群组讨论", "mode": "group", "instructions": "围绕用户目标讨论可行方案，列出优缺点与不确定性。"},
         {"id": "decide", "title": "综合决策", "mode": "council", "instructions": "依据讨论比较方案，给出明确结论、理由与执行步骤。"}]},
]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=4000)
    template_id: str = "research-review"
    steps: list[StepConfig] | None = Field(default=None, min_length=1, max_length=6)
    models: list[str] = Field(default_factory=list, max_length=4)
    team_id: UUID | None = None
    members: list[TeamMember] | None = Field(default=None, min_length=2, max_length=4)
    project_id: UUID | None = None
    snapshot_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=30)
    include_project_assets: bool = True
    knowledge_ids: list[UUID] = Field(default_factory=list, max_length=100)
    memory_ids: list[UUID] = Field(default_factory=list, max_length=100)
    memory_scope: str = Field(default="global", min_length=1, max_length=100)
    remember: bool = False
    tools: list[str] = Field(default_factory=list, max_length=11)
    mcp_server_ids: list[UUID] = Field(default_factory=list, max_length=20)
    system_prompt: str = Field(default="", max_length=4000)
    timeout_seconds: int = Field(default=1200, ge=30, le=3600)
    step_timeout_seconds: int = Field(default=600, ge=10, le=1200)
    max_attempts: int = Field(default=3, ge=1, le=3)

    @field_validator("goal")
    @classmethod
    def meaningful_goal(cls, value):
        if not value.strip():
            raise ValueError("请描述需要完成的目标")
        return value.strip()

    @model_validator(mode="after")
    def bounded_plan(self):
        if self.team_id and self.members:
            raise ValueError("请选择已保存团队或临时成员，不能同时指定两者")
        if self.members:
            validate_members(self.members)
            member_models = [member.model for member in self.members]
            if self.models and set(self.models) != set(member_models):
                raise ValueError("临时成员使用的模型必须与本次模型选择一致")
            self.models = member_models
        if not self.models and not self.team_id:
            raise ValueError("请选择模型或协作团队")
        if self.steps is None:
            template = next((item for item in TEMPLATES if item["id"] == self.template_id), None)
            if template is None:
                raise ValueError("协作模板不存在")
            self.steps = [StepConfig.model_validate(item) for item in template["steps"]]
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("步骤标识不能重复")
        if len(set(self.models)) != len(self.models) or any(not name or len(name) > 200 for name in self.models):
            raise ValueError("模型名称无效或重复")
        from ..platform_store import TOOL_IDS
        if len(self.tools) != len(set(self.tools)) or not set(self.tools) <= TOOL_IDS:
            raise ValueError("工具必须来自已注册目录且不能重复")
        for step in self.steps:
            if step.models and (len(set(step.models)) != len(step.models) or (not self.team_id and not set(step.models) <= set(self.models))):
                raise ValueError("步骤模型必须来自本次选择且不能重复")
        for field in ["attachment_ids", "knowledge_ids", "memory_ids", "mcp_server_ids"]:
            if len(getattr(self, field)) != len(set(getattr(self, field))):
                raise ValueError("资料选择不能重复")
        if self.snapshot_id and not self.project_id:
            raise ValueError("代码快照必须选择所属项目")
        return self


class ResumeRequest(BaseModel):
    acknowledge_replay: Literal[True]
