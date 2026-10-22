"""Email Monitor agent: poll the mailbox, keep job-related mail, classify it, link it to an
application, advance that application's status, and raise H6 for mail that needs action.

    list new (since cursor) → skip own / OTP / non-job → classify (model, rules fallback)
        → store → link + advance status → needs action? notify + start the H6 workflow

Non-job mail is never stored. OTP mail is never stored or sent to a model. Mail bodies reach the
model fenced as untrusted data; the category decides whether action is needed, not the model."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.email.linker import advance_status, find_application
from jobpilot.agents.email.rules import (
    NEEDS_ACTION,
    SUGGESTED_ACTION,
    Category,
    Signal,
    is_otp_message,
    job_signal,
    rule_category,
)
from jobpilot.core.audit import log_action
from jobpilot.core.db import session_scope
from jobpilot.core.models import Application, Email, Job
from jobpilot.harness.errors import BudgetExceeded, HarnessError
from jobpilot.harness.guardrails import fence_untrusted, injection_signals
from jobpilot.harness.runner import AgentHarness
from jobpilot.harness.tools import SideEffect, Tool, ToolRegistry
from jobpilot.llm.base import LLMError
from jobpilot.mail.base import MailAuthError, Mailbox, MailError, MailMessage
from jobpilot.notifications import Dispatcher, Notification
from jobpilot.prompts import load_prompt

log = logging.getLogger(__name__)
ACTOR = "email_monitor"
CLASSIFY_CHARS = 4000

CategoryName = Literal[
    "interview_invite",
    "recruiter_outreach",
    "assessment",
    "rejection",
    "offer",
    "document_request",
    "application_confirmation",
    "other",
]


class EmailTriage(BaseModel):
    job_related: bool
    category: CategoryName = "other"
    summary: str = Field("", max_length=600)
    company: str = Field("", max_length=200)


# ---- harness tool I/O ------------------------------------------------------------------


class _ListIn(BaseModel):
    since: datetime
    limit: int = Field(50, ge=1, le=500)


class _Message(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    received_at: datetime
    rfc_message_id: str = ""
    references: str = ""
    reply_to: str = ""

    def to_mail(self) -> MailMessage:
        data = self.model_dump()
        return MailMessage(**{**data, "to": tuple(self.to), "cc": tuple(self.cc)})


class _ListOut(BaseModel):
    address: str
    messages: list[_Message]


class _NotifyIn(BaseModel):
    email_id: int
    sender: str
    subject: str
    summary: str
    category: str
    suggested_action: str


class _NotifyOut(BaseModel):
    delivered: list[str]
    failed: dict[str, str]


@dataclass
class PollReport:
    fetched: int = 0
    stored: int = 0
    ignored: int = 0
    needs_action: int = 0
    linked: int = 0
    status_changes: list[str] = field(default_factory=list)
    error: str = ""


class EmailMonitor:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        mailbox: Callable[[], Mailbox | None],
        harness_for: Callable[[ToolRegistry], AgentHarness],
        dispatcher: Dispatcher,
        cursor: Callable[[], datetime],
        record_poll: Callable[..., None],
        on_needs_action: Callable[[int], str | None] = lambda _: None,
        max_messages: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._mailbox = mailbox
        self._harness_for = harness_for
        self._dispatcher = dispatcher
        self._cursor = cursor
        self._record_poll = record_poll
        self._on_needs_action = on_needs_action
        self._max = max_messages
        self._now = clock
        self._lock = threading.Lock()  # one poll at a time (scheduler + "poll now")

    # ---- tools ----------------------------------------------------------------------------

    def _tools(self, mailbox: Mailbox) -> list[Tool]:
        def list_new(args: _ListIn) -> _ListOut:
            messages = mailbox.list_since(args.since, args.limit)
            return _ListOut(
                address=mailbox.address(),
                messages=[
                    _Message(
                        **{
                            **m.__dict__,
                            "to": list(m.to),
                            "cc": list(m.cc),
                        }
                    )
                    for m in messages
                ],
            )

        def notify(args: _NotifyIn) -> _NotifyOut:
            deliveries = self._dispatcher.send(
                Notification(
                    args.sender, args.subject, args.summary, args.category, args.suggested_action
                )
            )
            return _NotifyOut(
                delivered=[d.channel for d in deliveries if d.ok],
                failed={d.channel: d.error for d in deliveries if not d.ok},
            )

        return [
            Tool("mail.list_new", "List inbox messages since the cursor", _ListIn, _ListOut,
                 list_new, timeout_s=180, circuit_key="mail"),
            Tool("notify", "Notify the user about an email that needs action", _NotifyIn,
                 _NotifyOut, notify, side_effect=SideEffect.WRITE, timeout_s=30),
        ]  # fmt: skip

    # ---- poll -----------------------------------------------------------------------------

    def poll(self) -> PollReport:
        if not self._lock.acquire(blocking=False):
            return PollReport(error="a poll is already running")
        try:
            return self._poll()
        finally:
            self._lock.release()

    def _poll(self) -> PollReport:
        report = PollReport()
        mailbox = self._mailbox()
        if mailbox is None:
            report.error = "no mailbox connected"
            return report
        since = self._cursor()
        harness = self._harness_for(ToolRegistry(self._tools(mailbox)))
        try:
            with harness.run("poll", since=since.isoformat()):
                listed = harness.call_tool("mail.list_new", {"since": since, "limit": self._max})
                assert isinstance(listed, _ListOut)
                newest = since
                for raw in listed.messages:
                    message = raw.to_mail()
                    newest = max(newest, message.received_at)
                    self._handle(harness, mailbox.provider, listed.address, message, report)
                report.fetched = len(listed.messages)
        except MailAuthError as exc:
            report.error = str(exc)
            self._record_poll(None, error=str(exc), auth_failed=True)
            return report
        except (MailError, HarnessError) as exc:
            # The cursor stays put; the next poll retries and de-duplicates by message id.
            report.error = str(exc)
            self._record_poll(None, error=str(exc))
            return report
        self._record_poll(newest)
        return report

    def _handle(
        self,
        h: AgentHarness,
        provider: str,
        own_address: str,
        message: MailMessage,
        report: PollReport,
    ) -> None:
        key = f"{provider}:{message.message_id}"
        with session_scope(self._sessions) as s:
            if s.scalar(select(Email.id).where(Email.message_id == key)) is not None:
                return
            linked_threads = set(
                s.scalars(select(Email.thread_id).where(Email.linked_application_id.is_not(None)))
            )
            companies = list(
                s.scalars(
                    select(Job.company).join(Application, Application.job_id == Job.id).distinct()
                )
            )
        if message.sender_address == own_address.lower() or is_otp_message(message):
            report.ignored += 1  # our own sent replies; login codes are read only on request
            return
        signal = job_signal(message, linked_threads=linked_threads, companies=companies)
        if signal is None:
            report.ignored += 1
            return
        triage, source = self._classify(h, message)
        # A specific category is job mail, whatever `job_related` says (small models contradict
        # themselves); only "other" can be dropped, and never when the signal is strong.
        job_related = triage.job_related or triage.category != Category.OTHER.value
        if not job_related and not signal.strong:
            report.ignored += 1
            return
        email_id, needs_action = self._store(key, message, triage, source, signal, report)
        report.stored += 1
        if needs_action:
            report.needs_action += 1
            self._raise_h6(h, email_id)

    @staticmethod
    def _by_rules(message: MailMessage) -> EmailTriage:
        category = rule_category(message)
        return EmailTriage(job_related=True, category=category.value, summary=message.snippet)

    def _classify(self, h: AgentHarness, message: MailMessage) -> tuple[EmailTriage, str]:
        prompt = load_prompt("email_triage")
        text = (
            f"From: {message.sender}\nSubject: {message.subject}\n\n{message.body[:CLASSIFY_CHARS]}"
        )
        if signals := injection_signals(text):
            # The mail tries to instruct the model: classify it without one.
            log.warning("email %s carries injection markers %s", message.message_id, signals)
            return self._by_rules(message), "rules"
        try:
            triage = h.structured(
                system=prompt.text,
                user=fence_untrusted(text, source="email"),
                schema=EmailTriage,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
            ).value
            return triage, "model"
        except BudgetExceeded:
            raise  # stop the poll; the rest is picked up next time
        except (LLMError, HarnessError) as exc:
            log.warning("email triage by model failed (%s); using rules", type(exc).__name__)
            return self._by_rules(message), "rules"

    def _store(
        self,
        key: str,
        message: MailMessage,
        triage: EmailTriage,
        source: str,
        signal: Signal,
        report: PollReport,
    ) -> tuple[int, bool]:
        category = Category(triage.category)
        needs_action = NEEDS_ACTION[category]
        with session_scope(self._sessions) as s:
            row = Email(
                message_id=key,
                thread_id=message.thread_id,
                sender=message.sender[:300],
                reply_to=message.reply_to[:300],
                recipients={"to": list(message.to), "cc": list(message.cc)},
                subject=message.subject[:500],
                snippet=message.snippet,
                body=message.body,
                rfc_message_id=message.rfc_message_id[:500],
                references=message.references,
                received_at=message.received_at,
                category=category.value,
                summary=(triage.summary or message.snippet)[:600],
                suggested_action=SUGGESTED_ACTION[category],
                classified_by=source,
                action_status="needs_action" if needs_action else "info",
            )
            s.add(row)
            s.flush()
            link = find_application(s, message, triage.company)
            changed = None
            if link is not None:
                row.linked_application_id, row.link_reason = link.application_id, link.reason
                report.linked += 1
                changed = advance_status(s, link.application_id, category)
                if changed:
                    report.status_changes.append(f"application {link.application_id} → {changed}")
            log_action(
                s,
                ACTOR,
                "email_stored",
                entity_type="email",
                entity_id=row.id,
                payload={
                    "category": category.value,
                    "classified_by": source,
                    "signal": signal.reason,
                    "linked_application_id": row.linked_application_id,
                    "status_change": changed,
                },
            )
            return row.id, needs_action

    def _raise_h6(self, h: AgentHarness, email_id: int) -> None:
        with session_scope(self._sessions) as s:
            row = s.get(Email, email_id)
            assert row is not None
            payload = {
                "email_id": row.id,
                "sender": row.sender,
                "subject": row.subject,
                "summary": row.summary,
                "category": row.category,
                "suggested_action": row.suggested_action,
            }
        try:
            h.call_tool("notify", payload)
        except HarnessError as exc:
            log.warning("could not notify about email %s: %s", email_id, exc)
        thread_id = self._on_needs_action(email_id)
        with session_scope(self._sessions) as s:
            row = s.get(Email, email_id)
            assert row is not None
            row.notified_at = self._now()
            row.workflow_thread_id = thread_id or ""
