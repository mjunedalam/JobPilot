"""Mailbox connection (OAuth), email polling, emails and reply drafts.

The H6 / H7 decisions themselves go through /decisions like every other checkpoint."""

from __future__ import annotations

import html
from pathlib import Path as FsPath

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobpilot.agents.email.linker import move_to
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.mail_schemas import (
    ConnectIn,
    ConnectOut,
    DraftOut,
    EmailDetailOut,
    EmailOut,
    LinkedApplicationOut,
    MailAccountOut,
    MailStatusOut,
    NotificationChannelOut,
    PollOut,
    ResumeOut,
)
from jobpilot.core.audit import log_decision
from jobpilot.core.context import AppContext
from jobpilot.core.models import Application, Draft, Email, Job, ResumeVersion, WorkflowRun
from jobpilot.core.status import Checkpoint, JobStatus
from jobpilot.mail.base import MailError

router = APIRouter(tags=["mail"])
API_PREFIX = "/api/v1"
CALLBACK_PATH = "/mail/oauth/callback"


def redirect_uri(ctx: AppContext) -> str:
    """Built from config, never from the request's Host header. Loopback, as Google's and
    Microsoft's desktop-app clients require."""
    host = ctx.config.api.host
    if host in ("0.0.0.0", "::", ""):  # noqa: S104 - a bind address, not a redirect target
        host = "127.0.0.1"
    return f"http://{host}:{ctx.config.api.port}{API_PREFIX}{CALLBACK_PATH}"


def _account(ctx: AppContext) -> MailAccountOut | None:
    view = ctx.mail.status()
    return MailAccountOut(**view.__dict__) if view else None


@router.get("/mail", response_model=Envelope[MailStatusOut], dependencies=AUTH)
def mail_status(ctx: Ctx, rid: RequestId) -> Envelope[MailStatusOut]:
    dispatcher = ctx.notifier
    channels = [NotificationChannelOut(channel=c, available=True) for c in dispatcher.channels]
    channels += [
        NotificationChannelOut(channel=c, available=False, reason=why)
        for c, why in dispatcher.unavailable.items()
    ]
    return ok(
        MailStatusOut(
            account=_account(ctx),
            configured=ctx.mail.configured(),
            poll_interval_minutes=ctx.config.email.poll_interval_minutes,
            notifications=channels,
        ),
        rid,
    )


@router.post("/mail/connect", response_model=Envelope[ConnectOut], dependencies=AUTH)
def connect(ctx: Ctx, body: ConnectIn, rid: RequestId) -> Envelope[ConnectOut]:
    """Gmail / Outlook: returns the URL where you grant access; the provider then redirects
    back to this API and the account is saved. Local: a folder mailbox for trying things out."""
    try:
        if body.provider == "local":
            ctx.mail.connect_local(body.address, calendar=body.calendar)
            return ok(ConnectOut(authorization_url=None, account=_account(ctx)), rid)
        url = ctx.mail.begin_connect(body.provider, redirect_uri(ctx), body.calendar)
    except MailError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ok(ConnectOut(authorization_url=url, account=_account(ctx)), rid)


def _page(title: str, message: str, code: int = 200) -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><title>JobPilot</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;line-height:1.5'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        "<p>You can close this tab and return to JobPilot.</p></body>"
    )
    return HTMLResponse(body, status_code=code, headers={"cache-control": "no-store"})


@router.get(CALLBACK_PATH, response_class=HTMLResponse, include_in_schema=False)
def oauth_callback(
    ctx: Ctx,
    state: str = Query("", max_length=200),
    code: str = Query("", max_length=4000),
    error: str = Query("", max_length=200),
) -> HTMLResponse:
    """Unauthenticated by necessity (the browser returns from Google/Microsoft without the API
    token); `state` is single use, short lived and bound to the PKCE verifier."""
    if error:
        ctx.mail.flows.take(state)
        return _page("Not connected", f"The provider reported: {error}", 400)
    if not state or not code:
        return _page("Not connected", "The sign-in response was incomplete.", 400)
    try:
        view = ctx.mail.complete_connect(state, code)
    except MailError as exc:
        return _page("Not connected", str(exc), 400)
    return _page("Mailbox connected", f"JobPilot can now read and send mail as {view.address}.")


@router.delete("/mail", response_model=Envelope[MailStatusOut], dependencies=AUTH)
def disconnect(ctx: Ctx, rid: RequestId) -> Envelope[MailStatusOut]:
    ctx.mail.disconnect()
    return mail_status(ctx, rid)


@router.post("/mail/poll", response_model=Envelope[PollOut], dependencies=AUTH)
def poll_now(ctx: Ctx, rid: RequestId) -> Envelope[PollOut]:
    ctx.kill_switch.check()
    if ctx.mail.status() is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "connect a mailbox first")
    report = ctx.email_monitor.poll()
    return ok(PollOut(**report.__dict__), rid)


# ---- emails and drafts -------------------------------------------------------------------


def _email_out(session: Session, e: Email) -> EmailOut:
    application = None
    if e.linked_application_id is not None:
        row = session.execute(
            select(Application, Job)
            .join(Job, Application.job_id == Job.id)
            .where(Application.id == e.linked_application_id)
        ).first()
        if row is not None:
            app, job = row
            application = LinkedApplicationOut(
                id=app.id, title=job.title, company=job.company, status=app.status
            )
    return EmailOut(
        id=e.id,
        sender=e.sender,
        subject=e.subject,
        snippet=e.snippet,
        summary=e.summary,
        category=e.category,
        suggested_action=e.suggested_action,
        classified_by=e.classified_by,
        action_status=e.action_status,
        received_at=e.received_at,
        link_reason=e.link_reason,
        suggested_status=e.suggested_status,
        application=application,
        workflow_thread_id=e.workflow_thread_id,
        reply_open=_reply_open(session, e),
    )


def _reply_open(session: Session, e: Email) -> bool:
    if not e.workflow_thread_id:
        return False
    run = session.get(WorkflowRun, e.workflow_thread_id)
    return run is not None and run.status in ("waiting", "running")


@router.get("/emails", response_model=Envelope[list[EmailOut]], dependencies=AUTH)
def list_emails(
    session: DbSession,
    rid: RequestId,
    category: str | None = Query(None, max_length=32),
    action_status: str | None = Query(None, max_length=32),
    limit: int = Query(100, ge=1, le=500),
) -> Envelope[list[EmailOut]]:
    stmt = select(Email).order_by(Email.received_at.desc()).limit(limit)
    if category:
        stmt = stmt.where(Email.category == category)
    if action_status:
        stmt = stmt.where(Email.action_status == action_status)
    data = [_email_out(session, e) for e in session.scalars(stmt)]
    return ok(data, rid, total=len(data))


def _draft_out(d: Draft) -> DraftOut:
    return DraftOut(
        id=d.id,
        email_id=d.email_id,
        status=d.status,
        instruction=d.instruction,
        versions=list(d.versions),
        approved_version=d.approved_version,
        sent_at=d.sent_at,
        error=d.error,
    )


@router.get("/emails/{email_id}", response_model=Envelope[EmailDetailOut], dependencies=AUTH)
def get_email(email_id: int, session: DbSession, rid: RequestId) -> Envelope[EmailDetailOut]:
    e = session.get(Email, email_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    drafts = session.scalars(select(Draft).where(Draft.email_id == email_id).order_by(Draft.id))
    return ok(
        EmailDetailOut(
            **_email_out(session, e).model_dump(),
            body=e.body,
            recipients=e.recipients,
            drafts=[_draft_out(d) for d in drafts],
        ),
        rid,
    )


@router.post(
    "/emails/{email_id}/apply-suggestion", response_model=Envelope[EmailOut], dependencies=AUTH
)
def apply_suggestion(
    ctx: Ctx, email_id: int, session: DbSession, rid: RequestId
) -> Envelope[EmailOut]:
    """Apply the status an unverified email suggests, as your own (audited) decision."""
    ctx.kill_switch.check()
    e = session.get(Email, email_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    if not e.suggested_status or e.linked_application_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this email has no pending suggestion")
    target = JobStatus(e.suggested_status)
    if move_to(session, e.linked_application_id, target) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"the application can no longer move to {target.value}"
        )
    log_decision(
        session,
        Checkpoint.H6_EMAIL_ACTION,
        "apply_status",
        approved_content={
            "email_id": e.id,
            "application_id": e.linked_application_id,
            "status": target.value,
        },
        entity_type="email",
        entity_id=e.id,
    )
    e.suggested_status = ""
    session.flush()
    return ok(_email_out(session, e), rid)


@router.post("/emails/{email_id}/reply", response_model=Envelope[EmailOut], dependencies=AUTH)
def reply_again(ctx: Ctx, email_id: int, session: DbSession, rid: RequestId) -> Envelope[EmailOut]:
    """Start a reply for this email (H6 → draft → H7), e.g. after a send was refused or an
    email was set aside. Refused while a reply for it is already open."""
    ctx.kill_switch.check()
    e = session.get(Email, email_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    if _reply_open(session, e):
        raise HTTPException(status.HTTP_409_CONFLICT, "a reply to this email is already open")
    thread_id, _ = ctx.workflows.start("reply", {"email_id": e.id})
    e.workflow_thread_id, e.action_status = thread_id, "needs_action"
    session.flush()
    return ok(_email_out(session, e), rid)


@router.get("/drafts/{draft_id}", response_model=Envelope[DraftOut], dependencies=AUTH)
def get_draft(draft_id: int, session: DbSession, rid: RequestId) -> Envelope[DraftOut]:
    d = session.get(Draft, draft_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft not found")
    return ok(_draft_out(d), rid)


@router.get("/resumes", response_model=Envelope[list[ResumeOut]], dependencies=AUTH)
def list_resumes(session: DbSession, rid: RequestId) -> Envelope[list[ResumeOut]]:
    rows = session.scalars(select(ResumeVersion).order_by(ResumeVersion.id))
    data = [
        ResumeOut(
            id=r.id, label=r.label, file_name=FsPath(r.file_path).name, is_default=r.is_default
        )
        for r in rows
    ]
    return ok(data, rid, total=len(data))
