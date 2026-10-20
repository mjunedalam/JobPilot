"""DTOs for job search, jobs and portals."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jobpilot.agents.search.schema import RecencyPreset


class RecencyIn(BaseModel):
    preset: RecencyPreset = Field(description="Checkpoint H2: 1h, 24h, 7d, 30d or custom")
    since: datetime | None = None
    until: datetime | None = None


class SearchIn(BaseModel):
    recency: RecencyIn
    sources: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Enabled portal names and/or careers-page URLs",
    )
    include_undated: bool = False


class SearchReportOut(BaseModel):
    since: datetime
    until: datetime
    queries: int
    fetched: int
    unique: int
    new_jobs: int
    updated_jobs: int
    skipped: dict[str, int]
    blocked: dict[str, str]
    errors: dict[str, str]
    job_ids: list[int]


class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: str
    work_mode: str
    experience_min: float | None
    experience_max: float | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str
    posted_at: datetime | None
    portals: list[str]
    source_urls: list[str]
    skills: list[str]
    match_score: int | None
    match_reason: str
    red_flags: list[str]
    status: str
    description: str = ""


class ImportJobIn(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)


class PortalOut(BaseModel):
    name: str
    label: str
    enabled: bool
    tos_note: str
    auth_methods: list[str]
    status: str
    status_reason: str
    has_session: bool
    has_credentials: bool
    last_login: datetime | None
    session_expires_at: datetime | None


class PortalEnableIn(BaseModel):
    enabled: bool
    accept_terms: bool = False


class CredentialsIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)


class LoginIn(BaseModel):
    method: str = Field("manual", pattern="^(manual|password)$")
