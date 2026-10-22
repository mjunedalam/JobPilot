"""Request/response DTOs exposed in the OpenAPI spec."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: str
    database: str
    kill_switch_engaged: bool
    llm_privacy_mode: str
    tracing: str


class ProfileVersionOut(BaseModel):
    version: int
    source: str
    change_summary: str
    is_current: bool
    created_at: datetime


class MissingFieldOut(BaseModel):
    field: str
    question: str


class ProfilePatchIn(BaseModel):
    fields: dict[str, Any] = Field(..., min_length=1, description="Profile fields to overwrite")


class AnswerIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    answer: str = Field(..., min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class AnswerOut(BaseModel):
    id: int
    question: str
    answer: str
    tags: list[str]
    confirmed_by_user: bool
    times_used: int


class StopIn(BaseModel):
    reason: str = Field("stopped via API", max_length=200)


class KillSwitchOut(BaseModel):
    engaged: bool
    reason: str


class ImportResultOut(BaseModel):
    version: int | None
    resume_label: str
    missing: list[MissingFieldOut]
    usage: dict[str, float]
    updated: list[str] = []  # fields the resume filled in
    kept: list[str] = []  # your existing value kept over a different one in the resume


class ProfileQAIn(BaseModel):
    answers: dict[str, str] = Field(
        ...,
        min_length=1,
        description="Field name to free-text answer, e.g. '30 LPA', 'Pune, Delhi'",
    )
