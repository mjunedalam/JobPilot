"""Reply bookkeeping around the drafting agent: H6 and H7 decisions, append-only draft versions,
and the approved send.

Every draft version is immutable. An approval (H7) binds the exact content of one version
(recipients, subject, body and the SHA-256 of each attachment); `mail.send` is an external
harness tool that runs only with that approval, once per idempotency key, and builds the message
from the approved payload alone. An edit is a new version that must be approved again."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.profile.repository import (
    ProfileRepository,
    ResumeRepository,
    sha256_file,
)
from jobpilot.agents.reply.drafter import draft_body, slot_options, wants_scheduling
from jobpilot.agents.reply.errors import ReplyError
from jobpilot.core.audit import log_action, log_decision
from jobpilot.core.db import session_scope
from jobpilot.core.hashing import content_sha256
from jobpilot.core.models import Application, Draft, Email, Job, ResumeVersion
from jobpilot.core.status import Checkpoint
from jobpilot.harness.approvals import ApprovalToken
from jobpilot.harness.errors import HarnessError, OutcomeUnknown
from jobpilot.harness.runner import AgentHarness
from jobpilot.harness.tools import SideEffect, Tool, ToolRegistry
from jobpilot.llm.base import LLMError
from jobpilot.mail.base import (
    Attachment,
    MailError,
    MailMessage,
    OutgoingMail,
    address_of,
    reply_subject,
    split_addresses,
)
from jobpilot.mail.service import MailService

log = logging.getLogger(__name__)
ACTOR = "reply_service"
_ATTACH_HINT = ("resume", " cv", "curriculum", "document")


# ---- harness tool I/O ------------------------------------------------------------------


class _ThreadIn(BaseModel):
    thread_id: str


class _ThreadOut(BaseModel):
    messages: list[dict[str, Any]]


class _BusyIn(BaseModel):
    start: datetime
    end: datetime


class _BusyOut(BaseModel):
    busy: list[tuple[datetime, datetime]]


class _AttachmentRef(BaseModel):
    resume_id: int
    name: str
    sha256: str


class _SendIn(BaseModel):
    """Exactly what the user approved at H7."""

    draft_id: int
    version: int
    to: list[str] = Field(min_length=1)
    cc: list[str] = []
    subject: str
    body: str = Field(min_length=1)
    attachments: list[_AttachmentRef] = []


class _SendOut(BaseModel):
    sent_message_id: str


def approval_content(version: dict[str, Any], draft_id: int) -> dict[str, Any]:
    return {
        "draft_id": draft_id,
        "version": version["n"],
        "to": list(version["to"]),
        "cc": list(version["cc"]),
        "subject": version["subject"],
        "body": version["body"],
        "attachments": [
            {"resume_id": a["resume_id"], "name": a["name"], "sha256": a["sha256"]}
            for a in version["attachments"]
        ],
    }


def _message_dict(m: MailMessage) -> dict[str, Any]:
    return {**m.__dict__, "to": list(m.to), "cc": list(m.cc)}


class ReplyService:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        mail: MailService,
        harness_for: Callable[[ToolRegistry], AgentHarness],
        resume_dir: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._mail = mail
        self._harness_for = harness_for
        self._resume_dir = resume_dir
        self._now = clock

    # ---- H6 -----------------------------------------------------------------------------

    def triage_view(self, email_id: int) -> dict[str, Any]:
        with session_scope(self._sessions) as s:
            e = s.get(Email, email_id)
            if e is None:
                raise ReplyError(f"email {email_id} not found")
            application = None
            if e.linked_application_id is not None:
                row = s.execute(
                    select(Application, Job)
                    .join(Job, Application.job_id == Job.id)
                    .where(Application.id == e.linked_application_id)
                ).first()
                if row is not None:
                    app, job = row
                    application = {
                        "id": app.id,
                        "title": job.title,
                        "company": job.company,
                        "status": app.status,
                    }
            return {
                "email_id": e.id,
                "sender": e.sender,
                "subject": e.subject,
                "summary": e.summary,
                "category": e.category,
                "suggested_action": e.suggested_action,
                "received_at": e.received_at.isoformat(),
                "snippet": e.snippet,
                "application": application,
            }

    def record_h6(self, email_id: int, action: str, instruction: str) -> None:
        status = {"draft": "drafting", "ignore": "ignored", "done": "done"}[action]
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                Checkpoint.H6_EMAIL_ACTION,
                action,
                approved_content={
                    "email_id": email_id,
                    "action": action,
                    "instruction": instruction,
                },
                entity_type="email",
                entity_id=email_id,
            )
            e = s.get(Email, email_id)
            if e is not None:
                e.action_status = status

    # ---- drafting -----------------------------------------------------------------------

    def _tools(self) -> list[Tool]:
        def get_thread(args: _ThreadIn) -> _ThreadOut:
            mailbox = self._mail.mailbox()
            if mailbox is None:
                raise MailError("no mailbox connected")
            return _ThreadOut(
                messages=[_message_dict(m) for m in mailbox.get_thread(args.thread_id)]
            )

        def free_busy(args: _BusyIn) -> _BusyOut:
            fb = self._mail.freebusy()
            if fb is None:
                raise MailError("calendar access is not enabled")
            return _BusyOut(busy=fb.busy(args.start, args.end))

        return [
            Tool("mail.get_thread", "Read the email thread", _ThreadIn, _ThreadOut, get_thread,
                 timeout_s=60, circuit_key="mail"),
            Tool("calendar.free_busy", "Busy intervals from the calendar", _BusyIn, _BusyOut,
                 free_busy, timeout_s=30),
            Tool("mail.send", "Send the approved reply", _SendIn, _SendOut, self._send_tool,
                 side_effect=SideEffect.EXTERNAL, checkpoint=Checkpoint.H7_REPLY_APPROVAL,
                 timeout_s=60),
        ]  # fmt: skip

    def _thread(self, h: AgentHarness, e: dict[str, Any]) -> list[MailMessage]:
        try:
            out = h.call_tool("mail.get_thread", {"thread_id": e["provider_thread"]})
            assert isinstance(out, _ThreadOut)
            messages = [
                MailMessage(**{**m, "to": tuple(m["to"]), "cc": tuple(m["cc"])})
                for m in out.messages
            ]
            if messages:
                return messages
        except HarnessError as exc:
            log.warning("thread fetch failed (%s); using the stored email", exc)
        return [e["message"]]

    def _slots(self, h: AgentHarness) -> tuple[list[str] | None, str]:
        if self._mail.freebusy() is None:
            return None, "Calendar not connected: times were not checked against your calendar."
        cfg = self._mail.config.calendar
        now = self._now()
        try:
            out = h.call_tool(
                "calendar.free_busy",
                {"start": now, "end": now + timedelta(days=cfg.days_ahead + 3)},
            )
        except HarnessError as exc:
            return None, f"Calendar could not be read ({exc}); times were not checked."
        assert isinstance(out, _BusyOut)
        return slot_options(
            out.busy, self._mail.working_hours(), cfg.slot_minutes, cfg.days_ahead, now
        ), ""

    def draft(self, email_id: int, instruction: str, draft_id: int | None) -> dict[str, Any]:
        """Write a new version. On failure, returns {"draft_id", "error"} and adds no version."""
        e = self._email_context(email_id)
        with session_scope(self._sessions) as s:
            if draft_id is None:
                row = Draft(email_id=email_id, instruction=instruction, status="drafting")
                s.add(row)
                s.flush()
                draft_id = row.id
            profile = ProfileRepository(s).current()
        h = self._harness_for(ToolRegistry(self._tools()))
        warnings: list[str] = []
        try:
            with h.run("draft_reply", email_id=email_id, draft_id=draft_id):
                thread = self._thread(h, e)
                slots: list[str] | None = None
                if wants_scheduling(e["category"], instruction):
                    slots, note = self._slots(h)
                    warnings += [note] if note else []
                text = draft_body(
                    h,
                    profile=profile,
                    thread=thread,
                    instruction=instruction,
                    category=e["category"],
                    slots=slots,
                )
        except (LLMError, HarnessError) as exc:
            log.warning("drafting failed for email %s: %s", email_id, exc)
            return {"draft_id": draft_id, "error": f"Drafting failed: {exc}"}
        attachments = self._default_attachments(e["category"], instruction)
        version = self._append(
            draft_id,
            to=[e["reply_to"]],
            cc=[],
            subject=reply_subject(e["subject"]),
            body=text.body,
            attachment_ids=attachments,
            source="model",
            warnings=warnings + text.warnings,
            instruction=instruction,
        )
        return {"draft_id": draft_id, "version": version}

    def _email_context(self, email_id: int) -> dict[str, Any]:
        with session_scope(self._sessions) as s:
            e = s.get(Email, email_id)
            if e is None:
                raise ReplyError(f"email {email_id} not found")
            provider_thread = e.thread_id
            message = MailMessage(
                message_id=e.message_id,
                thread_id=provider_thread,
                sender=e.sender,
                to=tuple(e.recipients.get("to", [])),
                cc=tuple(e.recipients.get("cc", [])),
                subject=e.subject,
                body=e.body,
                received_at=e.received_at,
            )
            return {
                "category": e.category,
                "subject": e.subject,
                "reply_to": address_of(e.reply_to) or address_of(e.sender),
                "provider_thread": provider_thread,
                "message": message,
            }

    def _default_attachments(self, category: str, instruction: str) -> list[int]:
        text = f" {instruction.casefold()}"
        if category != "document_request" and not any(k in text for k in _ATTACH_HINT):
            return []
        with session_scope(self._sessions) as s:
            repo = ResumeRepository(s, self._resume_dir)
            resumes = repo.all()
            chosen = repo.default() or (resumes[-1] if resumes else None)
            return [chosen.id] if chosen is not None else []

    # ---- versions -----------------------------------------------------------------------

    def _append(
        self,
        draft_id: int,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachment_ids: list[int],
        source: str,
        warnings: list[str],
        instruction: str | None = None,
    ) -> int:
        with session_scope(self._sessions) as s:
            draft = s.get(Draft, draft_id)
            if draft is None:
                raise ReplyError(f"draft {draft_id} not found")
            if draft.status in ("sent", "send_unknown", "discarded"):
                raise ReplyError(f"draft is {draft.status}")
            attachments = []
            owner = ProfileRepository(s).current().full_name.strip()
            for rid in attachment_ids:
                resume = s.get(ResumeVersion, rid)
                if resume is None or not Path(resume.file_path).is_file():
                    raise ReplyError(f"resume {rid} not found")
                attachments.append(
                    {
                        "resume_id": resume.id,
                        # Stored files are named by hash; send a name a recruiter recognises.
                        "name": _attachment_name(owner, resume.label, Path(resume.file_path)),
                        "sha256": sha256_file(Path(resume.file_path)),
                    }
                )
            n = len(draft.versions) + 1
            version = {
                "n": n,
                "to": list(split_addresses(to)),
                "cc": list(split_addresses(cc)),
                "subject": " ".join(subject.split())[:300],
                "body": body.strip(),
                "attachments": attachments,
                "source": source,
                "warnings": warnings,
                "created_at": self._now().isoformat(),
            }
            if not version["to"]:
                raise ReplyError("a reply needs at least one valid recipient")
            version["sha256"] = content_sha256(approval_content(version, draft_id))
            draft.versions = [*draft.versions, version]  # new list: JSON column change tracking
            draft.status = "awaiting_approval"
            if instruction is not None:
                draft.instruction = instruction
            return n

    def version_view(self, draft_id: int, n: int | None) -> dict[str, Any] | None:
        with session_scope(self._sessions) as s:
            draft = s.get(Draft, draft_id)
            if draft is None or not draft.versions:
                return None
            v = draft.versions[(n or len(draft.versions)) - 1]
            return {**v, "draft_id": draft_id, "versions": len(draft.versions)}

    def draft_shell(self, email_id: int) -> int:
        """An empty draft, for when the user writes the reply after drafting failed."""
        with session_scope(self._sessions) as s:
            row = Draft(email_id=email_id, status="drafting")
            s.add(row)
            s.flush()
            return row.id

    def _defaults(self, draft_id: int) -> dict[str, Any]:
        with session_scope(self._sessions) as s:
            draft = s.get(Draft, draft_id)
            if draft is None:
                raise ReplyError(f"draft {draft_id} not found")
            email_id = draft.email_id
        e = self._email_context(email_id)
        return {"to": [e["reply_to"]], "cc": [], "subject": reply_subject(e["subject"]),
                "body": "", "attachments": []}  # fmt: skip

    def edit(self, draft_id: int, base: int | None, changes: dict[str, Any]) -> int:
        current = self.version_view(draft_id, base) or self._defaults(draft_id)
        if not str(changes.get("body", current.get("body", ""))).strip():
            raise ReplyError("the reply text is empty")
        n = self._append(
            draft_id,
            to=list(changes.get("to", current.get("to", []))),
            cc=list(changes.get("cc", current.get("cc", []))),
            subject=str(changes.get("subject", current.get("subject", ""))),
            body=str(changes.get("body", current.get("body", ""))),
            attachment_ids=[
                int(a)
                for a in changes.get(
                    "attachments", [a["resume_id"] for a in current.get("attachments", [])]
                )
            ],
            source="user",
            warnings=[],
        )
        view = self.version_view(draft_id, n)
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                Checkpoint.H7_REPLY_APPROVAL,
                "edit",
                approved_content=approval_content(view, draft_id) if view else {},
                entity_type="draft",
                entity_id=draft_id,
            )
        return n

    # ---- H7 -----------------------------------------------------------------------------

    def approve(self, draft_id: int, n: int) -> int:
        view = self.version_view(draft_id, n)
        if view is None or not view["body"].strip():
            raise ReplyError("there is no draft text to approve")
        content = approval_content(view, draft_id)
        with session_scope(self._sessions) as s:
            row = log_decision(
                s,
                Checkpoint.H7_REPLY_APPROVAL,
                "approve_and_send",
                approved_content=content,
                entity_type="draft",
                entity_id=draft_id,
            )
            draft = s.get(Draft, draft_id)
            assert draft is not None
            draft.approved_version, draft.approved_sha256 = n, content_sha256(content)
            draft.status = "approved"
            return row.id

    def regenerate(self, draft_id: int, instruction: str) -> None:
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                Checkpoint.H7_REPLY_APPROVAL,
                "regenerate",
                approved_content={"draft_id": draft_id, "instruction": instruction},
                entity_type="draft",
                entity_id=draft_id,
            )

    def discard(self, draft_id: int | None, email_id: int) -> None:
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                Checkpoint.H7_REPLY_APPROVAL,
                "discard",
                approved_content={"draft_id": draft_id, "email_id": email_id},
                entity_type="draft",
                entity_id=draft_id or "",
            )
            draft = s.get(Draft, draft_id) if draft_id else None
            if draft is not None:
                draft.status = "discarded"
            e = s.get(Email, email_id)
            if e is not None:
                e.action_status = "done"

    # ---- send ---------------------------------------------------------------------------

    def _send_tool(self, args: _SendIn) -> _SendOut:
        mailbox = self._mail.mailbox()
        if mailbox is None:
            raise MailError("no mailbox connected")
        with session_scope(self._sessions) as s:
            draft = s.get(Draft, args.draft_id)
            e = s.get(Email, draft.email_id) if draft else None
            if draft is None or e is None:
                raise ReplyError("draft not found")
            in_reply_to, references, thread = e.rfc_message_id, e.references, e.thread_id
            files = []
            for a in args.attachments:
                resume = s.get(ResumeVersion, a.resume_id)
                path = Path(resume.file_path) if resume else None
                # The file must still be byte-for-byte what was approved.
                if path is None or not path.is_file() or sha256_file(path) != a.sha256:
                    raise ReplyError(f"attachment {a.name} changed after approval; approve again")
                files.append(Attachment(a.name, path.read_bytes(), _mime_type(a.name)))
        sent = mailbox.send(
            OutgoingMail(
                to=tuple(args.to),
                cc=tuple(args.cc),
                subject=args.subject,
                body=args.body,
                in_reply_to=in_reply_to,
                references=references,
                thread_id=thread if mailbox.provider == "gmail" else "",
                attachments=tuple(files),
            )
        )
        return _SendOut(sent_message_id=sent)

    def _changed_attachments(self, attachments: list[dict[str, Any]]) -> str:
        with session_scope(self._sessions) as s:
            for a in attachments:
                resume = s.get(ResumeVersion, a["resume_id"])
                path = Path(resume.file_path) if resume else None
                if path is None or not path.is_file() or sha256_file(path) != a["sha256"]:
                    return str(a["name"])
        return ""

    def send(self, draft_id: int, n: int, approval_id: int) -> dict[str, str]:
        view = self.version_view(draft_id, n)
        if view is None:
            raise ReplyError("draft version not found")
        payload = approval_content(view, draft_id)
        if changed := self._changed_attachments(payload["attachments"]):
            with session_scope(self._sessions) as s:
                draft = s.get(Draft, draft_id)
                assert draft is not None
                draft.status, draft.approved_version, draft.approved_sha256 = (
                    "awaiting_approval",
                    None,
                    "",
                )
            return {
                "outcome": "changed",
                "detail": f"{changed} changed after approval; approve again",
            }
        h = self._harness_for(ToolRegistry(self._tools()))
        outcome, detail, sent_id = "sent", "", ""
        try:
            with h.run("send_reply", draft_id=draft_id, version=n):
                out = h.call_tool(
                    "mail.send",
                    payload,
                    approval=ApprovalToken(Checkpoint.H7_REPLY_APPROVAL, approval_id),
                    idempotency_key=f"mail.send:{draft_id}:{n}:{approval_id}",
                )
                assert isinstance(out, _SendOut)
                sent_id = out.sent_message_id
        except OutcomeUnknown as exc:
            outcome, detail = "send_unknown", f"{exc}; check your Sent folder"
        except (HarnessError, ReplyError, MailError) as exc:
            # The provider may or may not have accepted it; never resend automatically.
            outcome, detail = "send_unknown", f"send did not finish ({exc}); check your Sent folder"
        with session_scope(self._sessions) as s:
            draft = s.get(Draft, draft_id)
            assert draft is not None
            draft.status = outcome
            draft.error = detail
            e = s.get(Email, draft.email_id)
            if outcome == "sent":
                draft.sent_at, draft.sent_message_id = self._now(), sent_id
                if e is not None:
                    e.action_status = "replied"
            log_action(
                s,
                ACTOR,
                f"reply:{outcome}",
                entity_type="draft",
                entity_id=draft_id,
                payload={"version": n, "approval_id": approval_id, "detail": detail},
            )
        return {"outcome": outcome, "detail": detail}


def _mime_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _attachment_name(owner: str, label: str, path: Path) -> str:
    stem = f"{owner} Resume" if owner else "Resume"
    clean = re.sub(r"[^\w .()-]", "", f"{stem} ({label})" if label else stem).strip()
    return f"{clean or 'Resume'}{path.suffix.lower()}"
