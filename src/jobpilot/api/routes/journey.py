"""One job's journey: found → your decisions → attempts → applied → emails → replies."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from jobpilot.api.deps import AUTH, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.core.activity import describe, is_noise
from jobpilot.core.models import Application, AuditLog, Draft, Email, Job

router = APIRouter(tags=["workflows"], dependencies=AUTH)


class JourneyStep(BaseModel):
    at: datetime
    title: str
    detail: str = ""
    tone: str = "neutral"  # neutral | you | go | stop


class JourneyOut(BaseModel):
    job_id: int
    title: str
    company: str
    status: str
    steps: list[JourneyStep]  # oldest first


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/jobs/{job_id}/journey", response_model=Envelope[JourneyOut])
def journey(job_id: int, session: DbSession, rid: RequestId) -> Envelope[JourneyOut]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    steps = [
        JourneyStep(
            at=_utc(job.fetched_at),
            title="Found",
            detail=f"on {', '.join(job.portals) or 'the web'}"
            + (" · merged from several listings" if len(job.source_urls) > 1 else ""),
        )
    ]
    for row in session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "job", AuditLog.entity_id == str(job_id))
        .order_by(AuditLog.id)
    ):
        if is_noise(row.action) or row.action.startswith("tool:"):
            continue
        tone = "you" if row.actor == "user" else ("stop" if "failed" in row.action else "neutral")
        steps.append(
            JourneyStep(
                at=_utc(row.ts),
                title=describe(row.actor, row.action, row.checkpoint, row.entity_id, row.payload),
                tone=tone,
            )
        )
    app = session.scalars(select(Application).where(Application.job_id == job_id)).first()
    if app is not None:
        if app.submitted_at:
            steps.append(
                JourneyStep(
                    at=_utc(app.submitted_at),
                    title="Applied",
                    detail=app.confirmation_text[:140] or app.error[:140],
                    tone="go" if app.status != "failed" else "stop",
                )
            )
        for email in session.scalars(
            select(Email).where(Email.linked_application_id == app.id).order_by(Email.received_at)
        ):
            steps.append(
                JourneyStep(
                    at=_utc(email.received_at),
                    title=email.category.replace("_", " ").capitalize() + " email",
                    detail=f"{email.sender} · {email.subject}"[:160],
                    tone="you" if email.action_status == "needs_action" else "neutral",
                )
            )
            for draft in session.scalars(select(Draft).where(Draft.email_id == email.id)):
                if draft.sent_at:
                    steps.append(JourneyStep(at=_utc(draft.sent_at), title="Reply sent", tone="go"))
    steps.sort(key=lambda s: s.at)
    return ok(
        JourneyOut(
            job_id=job.id,
            title=job.title,
            company=job.company,
            status=app.status if app else job.status,
            steps=steps,
        ),  # fmt: skip
        rid,
    )
