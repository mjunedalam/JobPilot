"""Reply Drafting agent: write a reply body from the thread, the profile and the user's
instruction, offering only calendar-free slots when times are proposed.

The model writes the body only. Recipients, subject, signature and attachments are set in code
(and editable at H7), so text inside an email can never redirect a reply or attach a file."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.harness.guardrails import fence_untrusted
from jobpilot.harness.runner import AgentHarness
from jobpilot.mail.base import MailMessage
from jobpilot.mail.calendar import WorkingHours, describe_slot, free_slots
from jobpilot.mail.mime import strip_quoted
from jobpilot.prompts import load_prompt

log = logging.getLogger(__name__)
THREAD_MESSAGES = 5
MESSAGE_CHARS = 2500

_SCHEDULING = re.compile(
    r"\b(slots?|times?|availab\w*|schedul\w*|propose|call|meet\w*|interview|when)\b", re.I
)
_PLACEHOLDER = re.compile(r"\[(?:[A-Z][A-Za-z ]{1,30})\]|\{\{.*?\}\}|<(?:name|date|time)>", re.I)
_SUBJECT_LINE = re.compile(r"^\s*subject:.*\n+", re.I)


class ReplyBody(BaseModel):
    body: str = Field(min_length=1, max_length=6000)


@dataclass
class DraftText:
    body: str
    warnings: list[str] = field(default_factory=list)
    slots: list[str] = field(default_factory=list)


def wants_scheduling(category: str, instruction: str) -> bool:
    return category == "interview_invite" or bool(_SCHEDULING.search(instruction))


def profile_brief(p: CandidateProfile) -> str:
    """Only what a reply can need. No contact details: the signature is added in code."""
    lines = [f"Name: {p.full_name or 'unknown'}"]
    if p.current_role or p.current_company:
        lines.append(f"Current role: {p.current_role} at {p.current_company}".strip())
    if p.total_experience_years is not None:
        lines.append(f"Experience: {p.total_experience_years:g} years")
    if p.notice_period_days is not None:
        lines.append(f"Notice period: {p.notice_period_days} days")
    if p.current_location:
        lines.append(f"Location: {p.current_location}")
    return "\n".join(lines)


def thread_text(messages: list[MailMessage]) -> str:
    parts = []
    for m in messages[-THREAD_MESSAGES:]:
        body = strip_quoted(m.body)[:MESSAGE_CHARS]
        parts.append(f"From: {m.sender}\nDate: {m.received_at:%a %d %b %Y %H:%M %Z}\n\n{body}")
    return "\n\n-----\n\n".join(parts)


def signature(p: CandidateProfile) -> str:
    return "\n".join(x for x in (p.full_name, p.phone) if x)


def finish_body(body: str, p: CandidateProfile) -> str:
    lines = _SUBJECT_LINE.sub("", body.strip(), count=1).rstrip().splitlines()
    own = {x.strip().casefold() for x in (p.full_name, p.phone) if x.strip()}
    while lines and lines[-1].strip().casefold() in own:  # the model signed anyway
        lines.pop()
    sig = signature(p)
    return "\n".join(lines + ([sig] if sig else []))


def slot_options(
    busy: list[tuple[datetime, datetime]], hours: WorkingHours, minutes: int, days: int,
    now: datetime | None = None,
) -> list[str]:  # fmt: skip
    duration = timedelta(minutes=minutes)
    starts = free_slots(
        busy, now=now or datetime.now(UTC), hours=hours, duration=duration, days=days
    )
    return [describe_slot(s, duration, hours.timezone) for s in starts]


def draft_body(
    h: AgentHarness,
    *,
    profile: CandidateProfile,
    thread: list[MailMessage],
    instruction: str,
    category: str,
    slots: list[str] | None,
) -> DraftText:
    """`slots`: free slots to offer, [] when the calendar has none, None when not checked."""
    prompt = load_prompt("reply_draft")
    if slots is None:
        slot_text = "(calendar not checked: do not invent times)"
    elif slots:
        slot_text = "\n".join(f"- {s}" for s in slots)
    else:
        slot_text = "(no free slots found in the coming days: ask them to suggest times)"
    user = (
        f"User's instruction: {instruction or 'Reply appropriately.'}\n\n"
        f"Email category: {category}\n\n"
        f"Candidate profile:\n{profile_brief(profile)}\n\n"
        f"Free slots:\n{slot_text}\n\n"
        f"Thread (oldest first):\n{fence_untrusted(thread_text(thread), source='email_thread')}"
    )
    out = h.structured(
        system=prompt.text,
        user=user,
        schema=ReplyBody,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    ).value
    body = finish_body(out.body, profile)
    warnings = []
    if _PLACEHOLDER.search(body):
        warnings.append("The draft contains a placeholder; edit it before sending.")
    return DraftText(body=body, warnings=warnings, slots=slots or [])
