"""DTOs for workflows, decisions (H3–H5) and applications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowOut(BaseModel):
    thread_id: str
    kind: str
    status: str
    error: str
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DecisionOut(BaseModel):
    thread_id: str
    kind: str
    interrupt_id: str
    checkpoint: str
    payload: dict[str, Any]


class DecisionIn(BaseModel):
    interrupt_id: str = Field(..., min_length=1, max_length=100)
    answer: dict[str, Any]


class StartedOut(BaseModel):
    thread_id: str
    status: str


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    title: str
    company: str
    portal: str
    status: str
    attempts: int
    submitted_at: datetime | None
    confirmation_text: str
    has_proof: bool
    error: str
    answers_given: dict[str, Any]
