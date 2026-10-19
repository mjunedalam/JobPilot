"""Persistence for versioned profiles, resume versions and the screening Answer Bank."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jobpilot.agents.profile.merge import diff_profiles
from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.core.audit import log_action
from jobpilot.core.models import AnswerBankEntry, CandidateProfileRow, ResumeVersion

ACTOR = "profile_agent"


class ProfileRepository:
    """Append-only: every change is a new version row; the previous rows are kept."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def current_row(self) -> CandidateProfileRow | None:
        stmt = select(CandidateProfileRow).where(CandidateProfileRow.is_current.is_(True))
        return self._s.scalars(stmt).first()

    def current(self) -> CandidateProfile:
        row = self.current_row()
        return CandidateProfile.model_validate(row.data) if row else CandidateProfile()

    def history(self) -> list[CandidateProfileRow]:
        stmt = select(CandidateProfileRow).order_by(CandidateProfileRow.version)
        return list(self._s.scalars(stmt))

    def get_version(self, version: int) -> CandidateProfile | None:
        stmt = select(CandidateProfileRow).where(CandidateProfileRow.version == version)
        row = self._s.scalars(stmt).first()
        return CandidateProfile.model_validate(row.data) if row else None

    def save(self, profile: CandidateProfile, *, source: str) -> CandidateProfileRow | None:
        """Store a new version if anything changed. Returns None when there is no change."""
        previous = self.current_row()
        old = CandidateProfile.model_validate(previous.data) if previous else CandidateProfile()
        changes = diff_profiles(old, profile)
        if previous is not None and not changes:
            return None

        next_version = (self._s.scalar(select(func.max(CandidateProfileRow.version))) or 0) + 1
        self._s.execute(update(CandidateProfileRow).values(is_current=False))
        row = CandidateProfileRow(
            version=next_version,
            data=profile.model_dump(mode="json"),
            change_summary=", ".join(sorted(changes)) or "initial",
            source=source,
            is_current=True,
        )
        self._s.add(row)
        self._s.flush()
        # Field names only: values may be PII (CTC, phone) and the audit log is not the vault.
        log_action(
            self._s,
            ACTOR,
            "profile_version_created",
            entity_type="candidate_profile",
            entity_id=next_version,
            payload={"source": source, "fields": sorted(changes)},
        )
        return row


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ResumeRepository:
    def __init__(self, session: Session, storage_dir: Path) -> None:
        self._s = session
        self._dir = storage_dir

    def add(self, source: Path, label: str, skills_focus: list[str] | None = None) -> ResumeVersion:
        digest = sha256_file(source)
        existing = self._s.scalars(select(ResumeVersion).where(ResumeVersion.sha256 == digest))
        if (found := existing.first()) is not None:
            return found
        if self._s.scalars(select(ResumeVersion).where(ResumeVersion.label == label)).first():
            raise ValueError(f"a resume labelled '{label}' already exists")

        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{digest[:12]}{source.suffix.lower()}"
        shutil.copy2(source, target)
        is_first = self._s.scalar(select(func.count(ResumeVersion.id))) == 0
        row = ResumeVersion(
            label=label,
            file_path=str(target),
            sha256=digest,
            skills_focus=skills_focus or [],
            is_default=is_first,
        )
        self._s.add(row)
        self._s.flush()
        log_action(
            self._s,
            ACTOR,
            "resume_added",
            entity_type="resume_version",
            entity_id=row.id,
            payload={"label": label},
        )
        return row

    def all(self) -> list[ResumeVersion]:
        return list(self._s.scalars(select(ResumeVersion).order_by(ResumeVersion.id)))

    def default(self) -> ResumeVersion | None:
        stmt = select(ResumeVersion).where(ResumeVersion.is_default.is_(True))
        return self._s.scalars(stmt).first()


_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")
FUZZY_MIN_RATIO = 0.6
UNCONFIRMED_PENALTY = 0.5


def normalize_question(question: str) -> str:
    text = _NON_WORD.sub(" ", question.casefold())
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class AnswerMatch:
    entry: AnswerBankEntry
    confidence: float


class AnswerBank:
    """Reusable screening answers. Only user-confirmed answers can reach full confidence."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self, question: str, answer: str, *, confirmed: bool, tags: list[str] | None = None
    ) -> AnswerBankEntry:
        norm = normalize_question(question)
        if not norm or not answer.strip():
            raise ValueError("question and answer must both be non-empty")
        entry = self._s.scalars(
            select(AnswerBankEntry).where(AnswerBankEntry.question_norm == norm)
        ).first()
        if entry is None:
            entry = AnswerBankEntry(question=question.strip(), question_norm=norm)
            self._s.add(entry)
        entry.answer = answer.strip()
        entry.tags = tags or entry.tags or []
        entry.confirmed_by_user = confirmed
        self._s.flush()
        log_action(
            self._s,
            ACTOR,
            "answer_saved",
            entity_type="answer_bank",
            entity_id=entry.id,
            payload={"question": entry.question, "confirmed": confirmed},
        )
        return entry

    def all(self) -> list[AnswerBankEntry]:
        return list(self._s.scalars(select(AnswerBankEntry).order_by(AnswerBankEntry.id)))

    def lookup(self, question: str) -> AnswerMatch | None:
        norm = normalize_question(question)
        best: AnswerMatch | None = None
        for entry in self.all():
            ratio = (
                1.0
                if entry.question_norm == norm
                else SequenceMatcher(None, entry.question_norm, norm).ratio()
            )
            if ratio < FUZZY_MIN_RATIO:
                continue
            confidence = ratio if entry.confirmed_by_user else ratio * UNCONFIRMED_PENALTY
            if best is None or confidence > best.confidence:
                best = AnswerMatch(entry=entry, confidence=round(confidence, 3))
        return best

    def mark_used(self, entry: AnswerBankEntry) -> None:
        entry.times_used += 1
        self._s.flush()
