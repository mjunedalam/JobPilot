"""Keep the RAG collections in sync with the profile, Answer Bank and resumes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from jobpilot.agents.profile.parser import ResumeParseError, extract_text
from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository, ResumeRepository
from jobpilot.agents.profile.schema import CandidateProfile, Money
from jobpilot.rag.chunking import chunk_text
from jobpilot.rag.store import VectorStore

log = logging.getLogger(__name__)

PROFILE, RESUME, ANSWERS = "profile", "resume", "answers"
CANDIDATE_COLLECTIONS = [PROFILE, ANSWERS, RESUME]


def _money(value: Money | None) -> str | None:
    return f"{value.currency} {value.amount:,.0f} per {value.period}" if value else None


def profile_facts(p: CandidateProfile) -> list[str]:
    """One self-contained sentence group per topic, so each chunk answers a question alone."""
    facts: list[tuple[str, str | None]] = [
        ("Name", p.full_name),
        ("Email", p.email),
        ("Phone", p.phone),
        ("Current location", p.current_location),
        ("Current role", " at ".join(x for x in (p.current_role, p.current_company) if x) or None),
        (
            "Total experience",
            f"{p.total_experience_years:g} years" if p.total_experience_years is not None else None,
        ),
        (
            "Relevant experience",
            f"{p.relevant_experience_years:g} years"
            if p.relevant_experience_years is not None
            else None,
        ),
        ("Current CTC", _money(p.current_ctc)),
        ("Expected CTC", _money(p.expected_ctc)),
        (
            "Notice period",
            f"{p.notice_period_days} days" if p.notice_period_days is not None else None,
        ),
        ("Preferred locations", ", ".join(p.preferred_locations) or None),
        ("Open to work modes", ", ".join(p.work_modes) or None),
        ("Target roles", ", ".join(p.target_roles) or None),
        ("Work authorization", p.work_authorization or None),
    ]
    groups = [f"{label}: {value}." for label, value in facts if value]
    if p.skills:
        groups.append(
            "Skills: "
            + ", ".join(f"{s.name} ({s.years:g} years)" if s.years else s.name for s in p.skills)
            + "."
        )
    if p.education:
        groups.append(
            "Education: "
            + "; ".join(
                ", ".join(str(x) for x in (e.degree, e.institution, e.year) if x)
                for e in p.education
            )
            + "."
        )
    if p.certifications:
        groups.append("Certifications: " + "; ".join(c.name for c in p.certifications) + ".")
    links = [f"{k}: {v}" for k, v in p.links.model_dump().items() if v and k != "other"]
    if links:
        groups.append("Links: " + ", ".join(links) + ".")
    return groups


@dataclass(frozen=True)
class SyncReport:
    profile_changed: bool
    answers_indexed: int
    resumes_indexed: int
    pruned: int


class KnowledgeBase:
    def __init__(self, session: Session, store: VectorStore, resume_dir: Path) -> None:
        self._s = session
        self._store = store
        self._resume_dir = resume_dir

    def sync(self) -> SyncReport:
        profile = ProfileRepository(self._s).current()
        profile_changed = self._store.upsert_document(PROFILE, "current", profile_facts(profile))

        confirmed = [e for e in AnswerBank(self._s).all() if e.confirmed_by_user]
        answer_ids = set()
        for entry in confirmed:
            doc_id = f"answer:{entry.id}"
            answer_ids.add(doc_id)
            self._store.upsert_document(
                ANSWERS,
                doc_id,
                [f"Screening question: {entry.question}\nAnswer: {entry.answer}"],
                {"answer_id": entry.id},
            )

        resume_ids = set()
        for resume in ResumeRepository(self._s, self._resume_dir).all():
            doc_id = f"resume:{resume.sha256[:16]}"
            resume_ids.add(doc_id)
            try:
                text = extract_text(Path(resume.file_path))
            except ResumeParseError as exc:
                log.warning("skipping resume %s: %s", resume.label, exc)
                continue
            self._store.upsert_document(RESUME, doc_id, chunk_text(text), {"label": resume.label})

        pruned = self._store.prune(ANSWERS, answer_ids) + self._store.prune(RESUME, resume_ids)
        return SyncReport(profile_changed, len(answer_ids), len(resume_ids), pruned)
