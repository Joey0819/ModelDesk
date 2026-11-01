"""Typed planning and review contracts, independent of model providers."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=500)
    kind: Literal["text", "image", "video", "code", "tests"] = "text"
    count: int = Field(default=1, ge=1, le=20)
    min_duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    audio_required: bool = False
    captions_required: bool = False

    @model_validator(mode="after")
    def video_constraints(self):
        if self.min_duration_seconds and self.max_duration_seconds and self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError("视频时长范围无效")
        if self.kind != "video" and (self.min_duration_seconds or self.max_duration_seconds or self.audio_required or self.captions_required):
            raise ValueError("只有视频验收项可指定视频约束")
        return self


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    member_id: str = Field(min_length=1, max_length=40)
    instruction: str = Field(min_length=1, max_length=5000)
    expected_output: str = Field(min_length=1, max_length=500)
    output_kind: Literal["text", "code", "image", "video"] = "text"
    output_count: int | None = Field(default=None, ge=1, le=12, strict=True)
    depends_on: list[str] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def media_count(self):
        if self.output_count is not None and self.output_kind not in {"image", "video"}:
            raise ValueError("output_count 仅用于实际图片或视频数量")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    requirements: list[Requirement] = Field(min_length=1, max_length=10)
    steps: list[Step] = Field(min_length=1, max_length=8)
    reuse_media_ids: list[str] = Field(default_factory=list, max_length=12)
    document_formats: list[Literal["docx", "pdf", "html"]] = Field(default_factory=list, max_length=3)
    document_layout: Literal["report", "article", "compact"] | None = None
    reset_document_formats: bool = False


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    met: bool
    reason: str = Field(min_length=1, max_length=600)
    evidence_status: Literal["verified", "limited", "unverified"]

    @model_validator(mode="after")
    def evidence_boundary(self):
        # Coverage is required, not guessed from keywords in the prose: an
        # honest disclosure can itself be a fully verified requirement.
        if self.evidence_status in {"limited", "unverified"}:
            self.met = False
        return self


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

    @model_validator(mode="after")
    def all_requirements_verified(self):
        if any(not item.met for item in self.checks):
            self.goal_met = False
        return self


