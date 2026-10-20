"""SQLAlchemy ORM models for every table in the data model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from jobpilot.core.status import JobStatus, PortalStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, list[str]: JSON}


class CandidateProfileRow(Base):
    """One row per profile version. Exactly one row has is_current=True."""

    __tablename__ = "candidate_profile"
    __table_args__ = (UniqueConstraint("version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120), unique=True)
    file_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    skills_focus: Mapped[list[str]] = mapped_column(default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnswerBankEntry(Base):
    __tablename__ = "answer_bank"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    question_norm: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    answer: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(default=list)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PortalAccount(Base):
    __tablename__ = "portal_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal: Mapped[str] = mapped_column(String(64), unique=True)
    base_url: Mapped[str] = mapped_column(String(500), default="")
    auth_type: Mapped[str] = mapped_column(String(32))
    # Fernet-encrypted JSON: credentials and/or Playwright storage_state. Never logged.
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PortalStatus.DISCONNECTED)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    # normalised company|location. Postings in one bucket with similar titles are one job.
    dedup_bucket: Mapped[str] = mapped_column(String(300), default="", index=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300), index=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    work_mode: Mapped[str] = mapped_column(String(16), default="")
    experience_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    experience_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(default=list)
    portals: Mapped[list[str]] = mapped_column(default=list)
    source_urls: Mapped[list[str]] = mapped_column(default=list)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_reason: Mapped[str] = mapped_column(Text, default="")
    red_flags: Mapped[list[str]] = mapped_column(default=list)
    # Flags found in any individual listing before dedup (agents/matching/red_flags.py).
    source_flags: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.FOUND, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped[Application | None] = relationship(back_populates="job", uselist=False)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True enforces "never apply to the same job twice" at the database level.
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    portal: Mapped[str] = mapped_column(String(64), index=True)
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id"), nullable=True
    )
    answers_given: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.APPLYING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    confirmation_text: Mapped[str] = mapped_column(Text, default="")
    proof_path: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="application")


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(300), unique=True)
    thread_id: Mapped[str] = mapped_column(String(300), index=True)
    sender: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(500))
    snippet: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    linked_application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id"), nullable=True
    )
    action_status: Mapped[str] = mapped_column(String(32), default="new")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)
    # Each version: {n, to, cc, subject, body, attachments, created_at, sha256}
    versions: Mapped[list[Any]] = mapped_column(default=list)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="drafting")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_actor_ts", "actor", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    checkpoint: Mapped[str | None] = mapped_column(String(4), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)


class IdempotencyRecord(Base):
    """Result of a completed side-effecting tool call, keyed so a replay is a no-op."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    tool: Mapped[str] = mapped_column(String(100))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMProviderRow(Base):
    """A provider the user added at runtime. API keys are stored only as Fernet ciphertext."""

    __tablename__ = "llm_providers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    preset: Mapped[str] = mapped_column(String(64), default="custom")
    type: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    structured_mode: Mapped[str] = mapped_column(String(16), default="json_schema")
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelRouteRow(Base):
    """Runtime override of config.yaml's llm.roles entry for one role."""

    __tablename__ = "model_routes"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    fallbacks: Mapped[list[Any]] = mapped_column(default=list)
    max_tokens: Mapped[int] = mapped_column(Integer, default=16000)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSetting(Base):
    """Small key/value store for runtime settings (privacy mode, settings revision)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class RagChunk(Base):
    """One retrievable chunk. Embeddings are float32 bytes; search is exact cosine in numpy,
    which is fast at a single candidate's scale (thousands of chunks)."""

    __tablename__ = "rag_chunks"
    __table_args__ = (
        UniqueConstraint("collection", "doc_id", "chunk_index"),
        Index("ix_rag_collection_doc", "collection", "doc_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection: Mapped[str] = mapped_column(String(32))
    doc_id: Mapped[str] = mapped_column(String(200))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    doc_sha256: Mapped[str] = mapped_column(String(64))
    embedder: Mapped[str] = mapped_column(String(120))
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
