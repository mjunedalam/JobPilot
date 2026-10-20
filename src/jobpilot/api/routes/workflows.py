"""Apply workflows and their human-in-the-loop decisions (H3, H4, H5)."""

from __future__ import annotations

from pathlib import Path as FsPath
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from jobpilot.agents.application.selection import SelectionError, parse_selection
from jobpilot.agents.orchestrator.runtime import PendingDecision, WorkflowError
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.workflow_schemas import (
    ApplicationOut,
    DecisionIn,
    DecisionOut,
    StartedOut,
    WorkflowOut,
)
from jobpilot.core.models import Application, Job

router = APIRouter(tags=["workflows"], dependencies=AUTH)


def _decision_out(p: PendingDecision) -> DecisionOut:
    return DecisionOut(
        thread_id=p.thread_id,
        kind=p.kind,
        interrupt_id=p.interrupt_id,
        checkpoint=str(p.payload.get("checkpoint", "")),
        payload=p.payload,
    )


def _validate(decision: PendingDecision, answer: dict[str, Any]) -> None:
    """Reject malformed answers here, before the workflow is resumed."""
    allowed = decision.payload.get("allowed", [])
    action = answer.get("action")
    if action not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"action must be one of {allowed}")
    checkpoint = decision.payload.get("checkpoint")
    if checkpoint == "H3" and action == "approve":
        try:
            parse_selection(str(answer.get("selection", "all")), len(decision.payload["jobs"]))
        except SelectionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if checkpoint == "H4" and action == "answer":
        keys = {q["key"] for q in decision.payload["questions"]}
        values = answer.get("values")
        if not isinstance(values, dict) or set(values) - keys:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "values must answer the listed fields")
        missing = [k for k in keys if not str(values.get(k, "")).strip()]
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"answer every field: {missing}")
    if checkpoint == "H5" and action == "edit":
        keys = set(decision.payload.get("keys", {}).values())
        if not isinstance(answer.get("values"), dict) or set(answer["values"]) - keys:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "edit only fields shown in the preview"
            )


@router.post("/workflows/apply", response_model=Envelope[StartedOut], status_code=202)
def start_apply(ctx: Ctx, rid: RequestId) -> Envelope[StartedOut]:
    """Start an apply run: it pauses at H3 with the ranked list of found jobs."""
    try:
        thread_id, _ = ctx.workflows.start("apply")
    except WorkflowError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ok(StartedOut(thread_id=thread_id, status="running"), rid)


@router.get("/workflows", response_model=Envelope[list[WorkflowOut]])
def list_workflows(ctx: Ctx, rid: RequestId) -> Envelope[list[WorkflowOut]]:
    runs = [WorkflowOut.model_validate(r, from_attributes=True) for r in ctx.workflows.runs()]
    return ok(runs, rid, total=len(runs))


@router.get("/decisions", response_model=Envelope[list[DecisionOut]])
def pending_decisions(ctx: Ctx, rid: RequestId) -> Envelope[list[DecisionOut]]:
    data = [_decision_out(p) for p in ctx.workflows.pending()]
    return ok(data, rid, total=len(data))


@router.post("/decisions/{thread_id}", response_model=Envelope[StartedOut], status_code=202)
def answer_decision(
    ctx: Ctx, thread_id: str, body: DecisionIn, rid: RequestId
) -> Envelope[StartedOut]:
    current = next((p for p in ctx.workflows.pending() if p.thread_id == thread_id), None)
    if current is None or current.interrupt_id != body.interrupt_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "that decision is no longer pending")
    _validate(current, body.answer)
    try:
        ctx.workflows.resume(thread_id, body.interrupt_id, body.answer)
    except WorkflowError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ok(StartedOut(thread_id=thread_id, status="running"), rid)


@router.get("/applications", response_model=Envelope[list[ApplicationOut]])
def list_applications(session: DbSession, rid: RequestId) -> Envelope[list[ApplicationOut]]:
    rows = session.execute(
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .order_by(Application.started_at.desc())
        .limit(200)
    ).all()
    data = [
        ApplicationOut(
            id=a.id,
            job_id=a.job_id,
            title=j.title,
            company=j.company,
            portal=a.portal,
            status=a.status,
            attempts=a.attempts,
            submitted_at=a.submitted_at,
            confirmation_text=a.confirmation_text,
            has_proof=bool(a.proof_path) and FsPath(a.proof_path).is_file(),
            error=a.error,
            answers_given=a.answers_given,
        )
        for a, j in rows
    ]
    return ok(data, rid, total=len(data))


@router.get("/applications/{application_id}/proof", response_class=FileResponse)
def application_proof(ctx: Ctx, application_id: int, session: DbSession) -> FileResponse:
    app = session.get(Application, application_id)
    if app is None or not app.proof_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no proof for this application")
    path = FsPath(app.proof_path).resolve()
    if not path.is_relative_to(ctx.proof_dir.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no proof for this application")
    return FileResponse(path, media_type="image/png")
