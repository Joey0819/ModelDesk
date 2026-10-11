"""Typed, bounded configuration for actual CAMEL and CrewAI workers."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    role: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=2000)
    backstory: str = Field(default="重视证据与清晰表达。", max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    tools: list[Literal["math", "text_stats"]] = Field(default_factory=list, max_length=2)


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=4000)
    expected_output: str = Field(min_length=1, max_length=2000)
    agent: str = Field(min_length=1, max_length=40)
    context: list[str] = Field(default_factory=list, max_length=5)


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[AgentConfig] = Field(min_length=1, max_length=4)
    tasks: list[TaskConfig] = Field(default_factory=list, max_length=6)
    process: Literal["sequential", "hierarchical"] = "sequential"
    manager_model: str | None = Field(default=None, max_length=200)
    max_iter: int = Field(default=3, ge=2, le=8)

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
            completed.add(task.id)
        return self


def validate_workflow(value, mode, models):
    configuration = WorkflowConfig.model_validate(value)
    selected = {agent.model for agent in configuration.agents}
    if configuration.manager_model:
        selected.add(configuration.manager_model)
    if not selected <= set(models):
        raise ValueError("角色和管理者模型必须来自本次选择的模型")
    if mode == "camel":
        if len(configuration.agents) != 2 or configuration.tasks:
            raise ValueError("CAMEL 角色对话需要两个角色；任务内容由主输入提供")
        if configuration.process != "sequential":
            raise ValueError("CAMEL 使用双角色轮流讨论")
    elif mode == "crewai":
        if not configuration.tasks:
            raise ValueError("CrewAI 至少需要一个任务步骤")
        if configuration.process == "hierarchical" and not configuration.manager_model:
            raise ValueError("分层流程需要选择管理者模型")
    else:
        raise ValueError("自定义团队仅适用于 CAMEL 与 CrewAI")
    return configuration
