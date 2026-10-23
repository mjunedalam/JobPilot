"""DTOs for the mailbox connection, emails, drafts and email polling."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MailAccountOut(BaseModel):
    provider: str
    address: str
    status: str
    status_reason: str
    calendar_enabled: bool
    connected_at: datetime | None
    last_polled_at: datetime | None


class NotificationChannelOut(BaseModel):
    channel: str
    available: bool
    reason: str = ""


class MailStatusOut(BaseModel):
    account: MailAccountOut | None
    configured: dict[str, bool]  # provider -> OAuth app configured
    poll_interval_minutes: int
    notifications: list[NotificationChannelOut]


class ConnectIn(BaseModel):
    provider: Literal["gmail", "outlook", "local"]
    calendar: bool = False
    address: str = Field("me@jobpilot.local", max_length=200)  # local mailbox only


class ConnectOut(BaseModel):
    authorization_url: str | None  # open this to grant access (gmail / outlook)
    account: MailAccountOut | None


class PollOut(BaseModel):
    fetched: int
    stored: int
    ignored: int
    needs_action: int
    linked: int
    status_changes: list[str]
    error: str


class LinkedApplicationOut(BaseModel):
    id: int
    title: str
    company: str
    status: str


class EmailOut(BaseModel):
    id: int
    sender: str
    subject: str
    snippet: str
    summary: str
    category: str
    suggested_action: str
    classified_by: str
    action_status: str
    received_at: datetime
    link_reason: str
    suggested_status: str  # needs you: the sender could not be verified
    application: LinkedApplicationOut | None
    workflow_thread_id: str
    reply_open: bool  # a reply decision (H6 / H7) is waiting or in progress


class DraftOut(BaseModel):
    id: int
    email_id: int
    status: str
    instruction: str
    versions: list[dict[str, Any]]
    approved_version: int | None
    sent_at: datetime | None
    error: str


class EmailDetailOut(EmailOut):
    body: str
    recipients: dict[str, Any]
    drafts: list[DraftOut]


class ResumeOut(BaseModel):
    id: int
    label: str
    file_name: str
    is_default: bool
