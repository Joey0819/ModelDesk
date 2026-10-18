"""Typed planning and review contracts, independent of model providers."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=500)
    kind: Literal["text", "image", "video", "code", "tests"] = "text"
    count: int = Field(default=1, ge=1, le=20)


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    member_id: str = Field(min_length=1, max_length=40)
    instruction: str = Field(min_length=1, max_length=5000)
    expected_output: str = Field(min_length=1, max_length=500)
    output_kind: Literal["text", "code", "image", "video"] = "text"
    depends_on: list[str] = Field(default_factory=list, max_length=7)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    requirements: list[Requirement] = Field(min_length=1, max_length=10)
    steps: list[Step] = Field(min_length=1, max_length=8)


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    met: bool
    reason: str = Field(min_length=1, max_length=600)


class RuntimeRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    instruction: str = Field(min_length=1, max_length=2000)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_met: bool
    missing_requirements: list[str] = Field(max_length=10)
    checks: list[ReviewItem] = Field(min_length=1, max_length=12)
    revision_instructions: str = Field(default="", max_length=2500)
    runtime_revision: RuntimeRevision | None = None


