"""Matching & Ranking agent: score jobs against the profile and flag suspicious postings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.search.repository import _to_normalized
from jobpilot.agents.search.schema import NormalizedJob
from jobpilot.core.audit import log_action
from jobpilot.core.config import ModelRole
from jobpilot.core.models import Job
from jobpilot.harness.errors import HarnessError
from jobpilot.harness.guardrails import fence_untrusted
from jobpilot.llm.base import LLMError, SupportsStructured
from jobpilot.prompts import load_prompt

from .red_flags import RedFlag, Severity, detect_red_flags, needs_second_opinion
from .scorer import MatchResult, score_job

log = logging.getLogger(__name__)
ACTOR = "matching_agent"
MAX_REVIEW_CHARS = 6000


class RedFlagReview(BaseModel):
    suspicious: bool
    reasons: list[str] = Field(default_factory=list, max_length=3)


@dataclass(frozen=True)
class Assessment:
    job_id: int
    match: MatchResult
    red_flags: list[RedFlag]


class MatchingAgent:
    def __init__(self, llm: SupportsStructured | None = None) -> None:
        self._llm = llm

    def assess(
        self, job: NormalizedJob, profile: CandidateProfile
    ) -> tuple[MatchResult, list[RedFlag]]:
        flags = detect_red_flags(job, profile)
        if self._llm is not None and needs_second_opinion(flags):
            flags = flags + self._second_opinion(job)
        return score_job(profile, job), flags

    def _second_opinion(self, job: NormalizedJob) -> list[RedFlag]:
        prompt = load_prompt("red_flag_review")
        posting = f"{job.title} at {job.company}\n\n{job.description[:MAX_REVIEW_CHARS]}"
        try:
            review = self._llm.structured(  # type: ignore[union-attr]
                role=ModelRole.FAST,
                system=prompt.text,
                user=fence_untrusted(posting, source="job_posting"),
                schema=RedFlagReview,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
            ).value
        except (LLMError, HarnessError) as exc:
            # A failed second opinion must not hide the rule-based flags or block ranking.
            log.warning("red-flag review failed for %s: %s", job.title, exc)
            return []
        if not review.suspicious:
            return []
        reasons = "; ".join(r.strip() for r in review.reasons if r.strip())[:300]
        return [RedFlag("model_review", Severity.HIGH, reasons or "model judged it suspicious")]

    def score_and_store(
        self, session: Session, jobs: list[Job], profile: CandidateProfile
    ) -> list[Assessment]:
        results = []
        for row in jobs:
            match, flags = self.assess(_to_normalized(row), profile)
            row.match_score, row.match_reason = match.score, match.reason
            row.red_flags = [f.label() for f in flags]
            results.append(Assessment(row.id, match, flags))
        session.flush()
        log_action(
            session,
            ACTOR,
            "jobs_scored",
            entity_type="job",
            payload={"count": len(results), "flagged": sum(1 for r in results if r.red_flags)},
        )
        return results
