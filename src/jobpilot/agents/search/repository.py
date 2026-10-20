"""Job persistence. One row per real job across runs and portals."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobpilot.agents.search.dedup import merge, titles_match
from jobpilot.agents.search.schema import NormalizedJob, as_utc
from jobpilot.core.models import Application, Job
from jobpilot.core.status import JobStatus


def _title_norm(row: Job) -> str:
    return row.dedup_key.split("|", 2)[2] if row.dedup_key.count("|") >= 2 else ""


def _to_normalized(row: Job) -> NormalizedJob:
    return NormalizedJob(
        bucket=row.dedup_bucket,
        title_norm=_title_norm(row),
        title=row.title,
        company=row.company,
        location=row.location,
        description=row.description,
        posted_at=as_utc(row.posted_at) if row.posted_at else None,
        expires_at=as_utc(row.expires_at) if row.expires_at else None,
        experience_min=row.experience_min,
        experience_max=row.experience_max,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        skills=tuple(row.skills),
        work_mode=row.work_mode,
        portals=tuple(row.portals),
        source_urls=tuple(row.source_urls),
        source_flags=tuple(row.source_flags),
    )


def _apply(row: Job, job: NormalizedJob) -> None:
    row.dedup_bucket = job.bucket
    row.title, row.company, row.location = job.title, job.company, job.location
    row.description, row.work_mode = job.description, job.work_mode
    row.posted_at, row.expires_at = job.posted_at, job.expires_at
    row.experience_min, row.experience_max = job.experience_min, job.experience_max
    row.salary_min, row.salary_max = job.salary_min, job.salary_max
    row.salary_currency = job.salary_currency
    row.skills, row.portals = list(job.skills), list(job.portals)
    row.source_urls = list(job.source_urls)
    row.source_flags = list(job.source_flags)


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def find_same(self, job: NormalizedJob) -> Job | None:
        candidates = self._s.scalars(select(Job).where(Job.dedup_bucket == job.bucket))
        return next((r for r in candidates if titles_match(_title_norm(r), job.title_norm)), None)

    def upsert(self, job: NormalizedJob) -> tuple[Job, bool]:
        """Insert a new job, or merge this listing into the matching stored job.

        Status, score and approval are never reset by a re-listing. A job past `found`
        (approved, applying, applied…) is frozen: a later listing cannot add a source URL or
        text to something the user already reviewed."""
        existing = self.find_same(job)
        if existing is not None and existing.status != JobStatus.FOUND:
            return existing, False
        if existing is None:
            row = Job(dedup_key=job.dedup_key, status=JobStatus.FOUND)
            _apply(row, job)
            self._s.add(row)
            self._s.flush()
            return row, True
        _apply(existing, merge(_to_normalized(existing), job))
        self._s.flush()
        return existing, False

    def upsert_many(self, jobs: Iterable[NormalizedJob]) -> list[tuple[Job, bool]]:
        return [self.upsert(j) for j in jobs]

    def get(self, job_id: int) -> Job | None:
        return self._s.get(Job, job_id)

    def already_handled(self, row: Job) -> bool:
        """Applied to, in progress, rejected by the user, or otherwise past `found`."""
        if row.status != JobStatus.FOUND:
            return True
        return (
            self._s.scalar(select(Application.id).where(Application.job_id == row.id)) is not None
        )

    def list_found(self, *, since: datetime | None = None, limit: int = 200) -> list[Job]:
        stmt = select(Job).where(Job.status == JobStatus.FOUND)
        if since is not None:
            stmt = stmt.where(Job.posted_at >= since)
        stmt = stmt.order_by(Job.match_score.desc().nulls_last(), Job.posted_at.desc()).limit(limit)
        return list(self._s.scalars(stmt))
