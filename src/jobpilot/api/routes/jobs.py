"""Job search (H2) and ranked results. Approving jobs (H3) arrives with Phase 3."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select

from jobpilot.agents.portal.settings import PortalSettings
from jobpilot.agents.profile.repository import ProfileRepository
from jobpilot.agents.search.schema import Recency, RecencyPreset
from jobpilot.api.deps import AUTH, Ctx, DbSession, RequestId
from jobpilot.api.envelope import Envelope, ok
from jobpilot.api.job_schemas import ImportJobIn, JobOut, SearchIn, SearchReportOut
from jobpilot.core.db import session_scope
from jobpilot.core.models import Job
from jobpilot.core.status import JobStatus
from jobpilot.portals.base import PortalAdapter
from jobpilot.portals.registry import KNOWN, resolve

router = APIRouter(tags=["jobs"], dependencies=AUTH)


def _job_out(job: Job, *, with_description: bool = False) -> JobOut:
    data = {c: getattr(job, c) for c in JobOut.model_fields if c != "description"}
    return JobOut(**data, description=job.description if with_description else "")


def _adapters(ctx, sources: list[str]) -> list[PortalAdapter]:  # noqa: ANN001
    with session_scope(ctx.sessions) as session:
        enabled = PortalSettings(session, ctx.config.portals).enabled()
    adapters = []
    for source in sources:
        name = source.strip().casefold()
        if name in KNOWN and name not in enabled:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"enable {KNOWN[name].label} first (and accept its terms note)",
            )
        try:
            adapters.append(resolve(source, ctx.gateway))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return adapters


@router.post("/search", response_model=Envelope[SearchReportOut])
def run_search(ctx: Ctx, body: SearchIn, rid: RequestId) -> Envelope[SearchReportOut]:
    """Search the chosen sources within the recency window (checkpoint H2). Runs synchronously;
    with real portals this takes minutes because pages load at a human pace."""
    ctx.kill_switch.check()
    try:
        recency = Recency(**body.recency.model_dump())
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.errors()[0]["msg"]) from exc
    adapters = _adapters(ctx, body.sources)
    try:
        report = ctx.search_agent().run(adapters, recency, include_undated=body.include_undated)
    except ValueError as exc:  # e.g. no target roles in the profile yet
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    finally:
        ctx.tracer.flush()
    return ok(SearchReportOut(**vars(report)), rid)


@router.post("/jobs/import", response_model=Envelope[SearchReportOut])
def import_job(ctx: Ctx, body: ImportJobIn, rid: RequestId) -> Envelope[SearchReportOut]:
    """Add the postings on one URL (a careers page or a single job page), read by the LLM."""
    ctx.kill_switch.check()
    try:
        adapter = resolve(body.url, ctx.gateway)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if adapter.name in KNOWN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "use search for portal URLs; import is for other sites"
        )
    with session_scope(ctx.sessions) as session:
        profile = ProfileRepository(session).current()
    if not (profile.target_roles or profile.current_role):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "add target roles to your profile first")
    recency = Recency(preset=RecencyPreset.LAST_MONTH)
    try:
        report = ctx.search_agent().run([adapter], recency, include_undated=True)
    finally:
        ctx.tracer.flush()
    return ok(SearchReportOut(**vars(report)), rid)


@router.get("/jobs", response_model=Envelope[list[JobOut]])
def list_jobs(
    session: DbSession,
    rid: RequestId,
    status_filter: JobStatus = Query(JobStatus.FOUND, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> Envelope[list[JobOut]]:
    stmt = (
        select(Job)
        .where(Job.status == status_filter)
        .order_by(Job.match_score.desc().nulls_last(), Job.posted_at.desc())
        .limit(limit)
    )
    jobs = [_job_out(j) for j in session.scalars(stmt)]
    return ok(jobs, rid, total=len(jobs))


@router.get("/jobs/{job_id}", response_model=Envelope[JobOut])
def get_job(job_id: int, session: DbSession, rid: RequestId) -> Envelope[JobOut]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return ok(_job_out(job, with_description=True), rid)
