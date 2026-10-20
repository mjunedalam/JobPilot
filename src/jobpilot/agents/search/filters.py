"""Rules for which jobs reach the approval list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from jobpilot.agents.search.normalize import normalize_company
from jobpilot.agents.search.schema import NormalizedJob, as_utc


class SkipReason(StrEnum):
    EXCLUDED_COMPANY = "excluded_company"
    EXPIRED = "expired"
    OUTSIDE_WINDOW = "outside_window"
    UNDATED = "undated"


@dataclass(frozen=True)
class FilterResult:
    kept: list[NormalizedJob]
    skipped: list[tuple[NormalizedJob, SkipReason]]


def filter_jobs(
    jobs: list[NormalizedJob],
    *,
    since: datetime,
    until: datetime,
    excluded_companies: list[str],
    now: datetime,
    include_undated: bool = False,
) -> FilterResult:
    excluded = {normalize_company(c) for c in excluded_companies if c.strip()}
    kept: list[NormalizedJob] = []
    skipped: list[tuple[NormalizedJob, SkipReason]] = []
    for job in jobs:
        reason = _reason(job, since, until, excluded, now, include_undated)
        if reason is None:
            kept.append(job)
        else:
            skipped.append((job, reason))
    return FilterResult(kept, skipped)


def _reason(
    job: NormalizedJob,
    since: datetime,
    until: datetime,
    excluded: set[str],
    now: datetime,
    include_undated: bool,
) -> SkipReason | None:
    if normalize_company(job.company) in excluded:
        return SkipReason.EXCLUDED_COMPANY
    if job.expires_at is not None and as_utc(job.expires_at) < as_utc(now):
        return SkipReason.EXPIRED
    if job.posted_at is None:
        # The user picked a recency window; an undated listing can't be shown to be inside it.
        return None if include_undated else SkipReason.UNDATED
    if not as_utc(since) <= as_utc(job.posted_at) <= as_utc(until):
        return SkipReason.OUTSIDE_WINDOW
    return None
