"""Application bookkeeping around the agent: approvals (H3/H4/H5), application rows, status
transitions, daily caps and portal blocks. Everything returned is JSON-serialisable so it can
live in the checkpointed workflow state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.agents.application.agent import ApplicationAgent, ApplyContext, AttemptResult, Outcome
from jobpilot.agents.application.answers import FieldAnswerer
from jobpilot.agents.application.resume import pick_resume
from jobpilot.agents.portal.agent import PortalConnector
from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository, ResumeRepository
from jobpilot.core.audit import log_action, log_decision
from jobpilot.core.config import ApplicationConfig
from jobpilot.core.crypto import Vault
from jobpilot.core.db import session_scope
from jobpilot.core.models import Application, Job
from jobpilot.core.status import Checkpoint, JobStatus, check_transition
from jobpilot.harness.approvals import ApprovalToken
from jobpilot.llm.base import SupportsStructured
from jobpilot.portals.base import PortalAdapter
from jobpilot.portals.registry import KNOWN, resolve

log = logging.getLogger(__name__)
ACTOR = "application_service"
RagFn = Callable[[str], tuple[str, float]]


def job_row(job: Job, rank: int) -> dict[str, Any]:
    """The H3 table row (spec: #, title, company, location, exp, salary, posted, portal,
    match %, reason, link)."""
    return {
        "rank": rank,
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "experience_min": job.experience_min,
        "experience_max": job.experience_max,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "portals": list(job.portals),
        "match_score": job.match_score,
        "match_reason": job.match_reason,
        "red_flags": list(job.red_flags),
        "url": job.source_urls[0] if job.source_urls else "",
    }


class ApplicationService:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        agent: ApplicationAgent,
        config: ApplicationConfig,
        vault: Vault | None,
        llm: SupportsStructured,
        resume_dir: Path,
        proof_dir: Path,
        rag: RagFn | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._agent = agent
        self._cfg = config
        self._vault = vault
        self._llm = llm
        self._resume_dir = resume_dir
        self._proof_dir = proof_dir
        self._rag = rag
        self._now = clock

    # ---- H3 ---------------------------------------------------------------------------

    def candidates(self, limit: int = 25) -> list[dict[str, Any]]:
        with session_scope(self._sessions) as s:
            jobs = s.scalars(
                select(Job)
                .where(Job.status == JobStatus.FOUND)
                .order_by(Job.match_score.desc().nulls_last())
                .limit(limit)
            )
            return [job_row(j, i) for i, j in enumerate(jobs, 1)]

    def record_h3(self, shown: list[int], approved: list[int]) -> dict[str, int]:
        """One approval record per job, bound to exactly {"job_id", "answers": {}}."""
        tokens: dict[str, int] = {}
        with session_scope(self._sessions) as s:
            for job_id in shown:
                job = s.get(Job, job_id)
                if job is None or job.status != JobStatus.FOUND:
                    continue
                if job_id in approved:
                    row = log_decision(
                        s,
                        Checkpoint.H3_JOB_APPROVAL,
                        "approve",
                        approved_content={"job_id": job_id, "answers": {}},
                        entity_type="job",
                        entity_id=job_id,
                    )
                    job.status = check_transition(JobStatus(job.status), JobStatus.APPROVED)
                    job.approved_at = self._now()
                    tokens[str(job_id)] = row.id
                else:
                    log_decision(
                        s,
                        Checkpoint.H3_JOB_APPROVAL,
                        "reject",
                        approved_content={"job_id": job_id},
                        entity_type="job",
                        entity_id=job_id,
                    )
                    job.status = check_transition(JobStatus(job.status), JobStatus.SKIPPED)
        return tokens

    # ---- H4 / H5 ----------------------------------------------------------------------

    def record_h4(
        self, job_id: int, values: dict[str, str], labels: dict[str, str], save: list[str]
    ) -> None:
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                Checkpoint.H4_UNKNOWN_FIELD,
                "answer",
                approved_content=values,
                entity_type="job",
                entity_id=job_id,
            )
            bank = AnswerBank(s)
            for key in save:  # only what the user chose to keep, as confirmed answers
                if key in values and key in labels and values[key].strip():
                    bank.upsert(labels[key], values[key], confirmed=True, tags=["application"])

    def record_skip(self, job_id: int, checkpoint: Checkpoint, reason: str) -> None:
        with session_scope(self._sessions) as s:
            log_decision(
                s,
                checkpoint,
                "skip",
                approved_content={"job_id": job_id},
                entity_type="job",
                entity_id=job_id,
            )
            self._finish(s, job_id, JobStatus.SKIPPED, error=reason)

    def record_h5(self, job_id: int, answers: dict[str, str]) -> int:
        with session_scope(self._sessions) as s:
            row = log_decision(
                s,
                Checkpoint.H5_PRESUBMIT,
                "submit",
                approved_content={"job_id": job_id, "answers": answers},
                entity_type="job",
                entity_id=job_id,
            )
            return row.id

    # ---- attempts ---------------------------------------------------------------------

    def _cap_reached(self, s: Session, portal: str) -> bool:
        since = self._now() - timedelta(days=1)
        count = s.scalar(
            select(func.count(Application.id)).where(
                Application.portal == portal, Application.submitted_at >= since
            )
        )
        return (count or 0) >= self._cfg.daily_cap_per_portal

    def _adapter(self, url: str) -> PortalAdapter:
        return resolve(url, self._llm)

    def run_attempt(
        self,
        job_id: int,
        attempt: int,
        overrides: dict[str, str],
        approval_audit_id: int | None,
        preview: bool,
    ) -> dict[str, Any]:
        with session_scope(self._sessions) as s:
            job = s.get(Job, job_id)
            if job is None:
                return {"outcome": "failed", "detail": "job not found"}
            if job.status not in (JobStatus.APPROVED, JobStatus.APPLYING):
                return {"outcome": "skipped", "detail": f"job is {job.status}"}
            url = self._choose_url(job)
            adapter = self._adapter(url)
            if self._cap_reached(s, adapter.name):
                return {
                    "outcome": "cap_reached",
                    "portal": adapter.name,
                    "detail": f"daily cap of {self._cfg.daily_cap_per_portal} reached",
                }
            profile = ProfileRepository(s).current()
            resume = pick_resume(job, ResumeRepository(s, self._resume_dir).all())
            app = s.scalars(select(Application).where(Application.job_id == job_id)).first()
            if app is None:
                app = Application(  # explicit: column defaults only apply on INSERT
                    job_id=job_id,
                    portal=adapter.name,
                    attempts=0,
                    status=JobStatus.APPLYING,
                    resume_version_id=resume.id if resume else None,
                )
                s.add(app)
            if job.status == JobStatus.APPROVED:
                job.status = check_transition(JobStatus.APPROVED, JobStatus.APPLYING)
            app.status, app.attempts = JobStatus.APPLYING, max(app.attempts, attempt)
            s.flush()
            resume_path = Path(resume.file_path) if resume else None

        checkpoint = Checkpoint.H5_PRESUBMIT if preview else Checkpoint.H3_JOB_APPROVAL
        token = ApprovalToken(checkpoint, approval_audit_id) if approval_audit_id else None
        with session_scope(self._sessions) as s:  # read-only: Answer Bank lookups during fill
            answerer = FieldAnswerer(
                profile,
                answer_bank=AnswerBank(s),
                resume_path=resume_path,
                rag=self._rag,
                overrides=overrides,
            )
            result = self._agent.attempt(
                ApplyContext(
                    job_id=job_id,
                    url=url,
                    adapter=adapter,
                    answerer=answerer,
                    threshold=self._cfg.answer_confidence_threshold,
                    preview=preview,
                    proof_dir=self._proof_dir,
                    approval=token,
                    attempt=attempt,
                )
            )
        return self._record(job_id, adapter, result)

    @staticmethod
    def _choose_url(job: Job) -> str:
        """Prefer a listing on a known portal (signed-in session, known block detection)."""
        urls = list(job.source_urls)
        known = [u for u in urls if resolve_known(u)]
        return (known or urls)[0]

    def _record(self, job_id: int, adapter: PortalAdapter, result: AttemptResult) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "outcome": result.outcome.value,
            "detail": result.detail,
            "url": result.url,
            "portal": adapter.name,
            "answers": result.answers,
            "answer_keys": result.answer_keys,
            "questions": [asdict(q) for q in result.questions],
        }
        with session_scope(self._sessions) as s:
            app = s.scalars(select(Application).where(Application.job_id == job_id)).first()
            if app is not None:
                app.answers_given = result.answers
                app.proof_path = result.proof_path or app.proof_path
                app.confirmation_text = result.confirmation or app.confirmation_text
            if result.outcome in (Outcome.SUBMITTED, Outcome.ALREADY_APPLIED):
                self._finish(s, job_id, JobStatus.APPLIED)
                if app is not None:
                    app.submitted_at = self._now()
            elif result.outcome is Outcome.SUBMITTED_UNCONFIRMED:
                # Terminal: FAILED never transitions back, so the job is never tried again; the
                # submit time counts toward the daily cap as if it went through.
                self._finish(
                    s, job_id, JobStatus.FAILED, error=f"needs manual check: {result.detail}"
                )
                if app is not None:
                    app.submitted_at = self._now()
            elif result.outcome is Outcome.BLOCKED and result.block is not None:
                PortalConnector(s, self._vault).mark_blocked(adapter.name, result.block, result.url)
            log_action(
                s,
                ACTOR,
                f"attempt:{result.outcome.value}",
                entity_type="job",
                entity_id=job_id,
                payload={"detail": result.detail},
            )
        return summary

    def mark_failed(self, job_id: int, reason: str, url: str) -> None:
        with session_scope(self._sessions) as s:
            self._finish(
                s, job_id, JobStatus.FAILED, error=f"needs manual action: {reason} ({url})"
            )

    @staticmethod
    def _finish(s: Session, job_id: int, status: JobStatus, error: str = "") -> None:
        job = s.get(Job, job_id)
        if job is not None and job.status != status:
            current = JobStatus(job.status)
            if current == JobStatus.APPROVED and status != JobStatus.SKIPPED:
                current = check_transition(current, JobStatus.APPLYING)
            job.status = check_transition(current, status)
        app = s.scalars(select(Application).where(Application.job_id == job_id)).first()
        if app is not None:
            app.status = status
            app.error = error or app.error


def resolve_known(url: str) -> bool:
    try:
        return resolve(url).name in KNOWN
    except ValueError:
        return False
