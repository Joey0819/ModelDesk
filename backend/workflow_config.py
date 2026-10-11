"""Typed, bounded configuration for actual CAMEL and CrewAI workers."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    role: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=2000)
    backstory: str = Field(default="重视证据与清晰表达。", max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    tools: list[Literal["math", "text_stats", "web_fetch", "web_search", "knowledge_search",
                        "memory_read", "memory_save", "project_read", "mcp_call", "statistics", "markdown_to_text"]] = Field(default_factory=list, max_length=11)

    @model_validator(mode="after")
    def unique_tools(self):
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("角色工具不得重复")
        return self


class ConditionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=40)
    operator: Literal["contains", "not_contains", "equals"]
    value: str = Field(min_length=1, max_length=2000)


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=4000)
    expected_output: str = Field(min_length=1, max_length=2000)
    agent: str = Field(min_length=1, max_length=40)
    context: list[str] = Field(default_factory=list, max_length=5)
    when: ConditionConfig | None = None


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[AgentConfig] = Field(min_length=1, max_length=4)
    tasks: list[TaskConfig] = Field(default_factory=list, max_length=6)
    process: Literal["sequential", "hierarchical"] = "sequential"
    manager_model: str | None = Field(default=None, max_length=200)
    max_iter: int = Field(default=3, ge=2, le=8)
    task_specify: bool = False
    task_planning: bool = False
    critic: bool = False
    critic_model: str | None = Field(default=None, max_length=200)
    critic_criteria: str = Field(default="核查依据并改进任务结果。", min_length=1, max_length=2000)
    planning: bool = False
    planning_model: str | None = Field(default=None, max_length=200)
    memory: bool = False
    knowledge_ids: list[UUID] = Field(default_factory=list, max_length=20)
    memory_scope: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_/-]*$")
    project_id: UUID | None = None
    snapshot_id: UUID | None = None
    mcp_server_ids: list[UUID] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_dependencies(self):
        agents = {agent.id for agent in self.agents}
        if len(agents) != len(self.agents):
            raise ValueError("角色 ID 不得重复")
        completed = set()
        for task in self.tasks:
            if task.id in completed:
                raise ValueError("任务 ID 不得重复")
            if task.agent not in agents:
                raise ValueError("任务必须分配给已配置的角色")
            if len(task.context) != len(set(task.context)) or not set(task.context) <= completed:
                raise ValueError("上下文只能引用前面已定义的任务，不允许循环依赖")
            if task.when and task.when.task not in completed:
                raise ValueError("分支条件只能引用前面已定义的任务")
            completed.add(task.id)
        if len(self.knowledge_ids) != len(set(self.knowledge_ids)) or len(self.mcp_server_ids) != len(set(self.mcp_server_ids)):
            raise ValueError("知识与 MCP 资源不得重复")
        if any(segment in ("", ".", "..") for segment in self.memory_scope.split("/")):
            raise ValueError("记忆范围只能使用命名路径，不能包含空段或相对路径")
        if bool(self.project_id) != bool(self.snapshot_id):
            raise ValueError("项目工具必须同时绑定项目与不可变快照")
        for agent in self.agents:
            if "knowledge_search" in agent.tools and not self.knowledge_ids:
                raise ValueError("知识检索工具需要选择知识库")
            if "project_read" in agent.tools and not self.snapshot_id:
                raise ValueError("项目读取工具需要选择不可变快照")
            if "mcp_call" in agent.tools and not self.mcp_server_ids:
                raise ValueError("MCP 工具需要选择已注册服务")
        return self


def validate_workflow(value, mode, models):
    configuration = WorkflowConfig.model_validate(value)
    selected = {agent.model for agent in configuration.agents}
    selected.update(name for name in (configuration.manager_model, configuration.planning_model, configuration.critic_model) if name)
    if not selected <= set(models):
        raise ValueError("角色和管理者模型必须来自本次选择的模型")
    if mode == "camel":
        if len(configuration.agents) != 2 or configuration.tasks:
            raise ValueError("CAMEL 角色对话需要两个角色；任务内容由主输入提供")
        if configuration.process != "sequential":
            raise ValueError("CAMEL 使用双角色轮流讨论")
    elif mode == "camel_workforce":
        if any(task.when for task in configuration.tasks):
            raise ValueError("CAMEL Workforce 任务暂不接受 CrewAI Flow 分支条件")
    elif mode in ("crewai", "crewai_flow"):
        if not configuration.tasks:
            raise ValueError("CrewAI 至少需要一个任务步骤")
        if configuration.process == "hierarchical" and not configuration.manager_model:
            raise ValueError("分层流程需要选择管理者模型")
        if mode == "crewai" and any(task.when for task in configuration.tasks):
            raise ValueError("条件路由需要使用 CrewAI Flow 模式")
    else:
        raise ValueError("自定义团队仅适用于 CAMEL 与 CrewAI")
    if mode.startswith("camel") and (configuration.planning or configuration.memory):
        raise ValueError("CrewAI 原生 planning 与 memory 选项仅适用于 CrewAI 模式；CAMEL 使用 task_planning 与显式记忆工具")
    if mode.startswith("crewai") and (configuration.task_specify or configuration.task_planning or configuration.critic):
        raise ValueError("CAMEL 任务细化、规划与 critic 选项仅适用于 CAMEL 模式")
    if mode == "camel_workforce" and (configuration.task_specify or configuration.task_planning or configuration.critic):
        raise ValueError("Workforce 使用原生协调与任务规划；角色对话高级选项适用于 CAMEL 对话模式")
    return configuration
