"""Link an email to the application it is about, and advance that application's status.

Deterministic and conservative: a thread already linked wins; otherwise the company (and the
job title, as a tie-breaker) must match exactly one application. Ambiguous → not linked, never
guessed. Status only moves forward along the spec lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobpilot.agents.email.rules import Category, mentions_company, normalize_company
from jobpilot.core.models import Application, Email, Job
from jobpilot.core.status import InvalidTransition, JobStatus, check_transition
from jobpilot.mail.base import MailMessage

log = logging.getLogger(__name__)

LINKABLE = (
    JobStatus.APPLIED,
    JobStatus.VIEWED,
    JobStatus.SHORTLISTED,
    JobStatus.INTERVIEW,
    JobStatus.OFFER,
    JobStatus.REJECTED,
    JobStatus.FAILED,  # "needs manual check": a confirmation email can settle it
)

CATEGORY_STATUS: dict[Category, JobStatus] = {
    Category.INTERVIEW: JobStatus.INTERVIEW,
    Category.ASSESSMENT: JobStatus.SHORTLISTED,
    Category.OFFER: JobStatus.OFFER,
    Category.REJECTION: JobStatus.REJECTED,
    Category.CONFIRMATION: JobStatus.APPLIED,
}

UNCONFIRMED_PREFIX = "needs manual check"


@dataclass(frozen=True)
class Link:
    application_id: int
    reason: str


def _title_hit(message: MailMessage, title: str) -> bool:
    t = " ".join(title.casefold().split())
    return len(t) >= 4 and t in f"{message.subject} {message.body[:3000]}".casefold()


def find_application(session: Session, message: MailMessage, company_hint: str = "") -> Link | None:
    prior = session.scalars(
        select(Email.linked_application_id).where(
            Email.thread_id == message.thread_id, Email.linked_application_id.is_not(None)
        )
    ).first()
    if prior is not None:
        return Link(prior, "same thread")

    rows = session.execute(
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .where(Application.status.in_([s.value for s in LINKABLE]))
    ).all()
    hint = normalize_company(company_hint)
    scored: list[tuple[int, int, str]] = []
    for app, job in rows:
        company_match = mentions_company(message, job.company) or (
            bool(hint) and hint == normalize_company(job.company)
        )
        if not company_match:
            continue
        title = _title_hit(message, job.title)
        scored.append((2 + int(title), app.id, f"company{' + title' if title else ''}"))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        log.info("email %s matches several applications; leaving it unlinked", message.message_id)
        return None
    return Link(scored[0][1], scored[0][2])


def advance_status(session: Session, application_id: int, category: Category) -> str | None:
    """Move the application (and its job) forward. Returns the new status, or None."""
    target = CATEGORY_STATUS.get(category)
    return move_to(session, application_id, target) if target is not None else None


def move_to(session: Session, application_id: int, target: JobStatus) -> str | None:
    app = session.get(Application, application_id)
    if app is None:
        return None
    current = JobStatus(app.status)
    if current == target:
        return None
    if current == JobStatus.FAILED and not (
        target == JobStatus.APPLIED and app.error.startswith(UNCONFIRMED_PREFIX)
    ):
        return None  # only an unconfirmed submit can be settled by email
    try:
        check_transition(current, target)
    except InvalidTransition:
        return None  # never backwards (e.g. an old interview mail after an offer)
    app.status = target
    if current == JobStatus.FAILED:
        app.error = ""
    job = session.get(Job, app.job_id)
    if job is not None:
        try:
            job.status = check_transition(JobStatus(job.status), target)
        except InvalidTransition:
            log.warning("job %s is %s; left as is", job.id, job.status)
    return target.value
