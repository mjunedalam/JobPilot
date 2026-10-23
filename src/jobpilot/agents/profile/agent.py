"""Profile agent: builds, versions and maintains the candidate profile."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from jobpilot.agents.profile.extractor import ProfileExtractor
from jobpilot.agents.profile.merge import (
    apply_patch,
    diff_profiles,
    kept_over_resume,
    merge_profiles,
)
from jobpilot.agents.profile.parser import extract_text
from jobpilot.agents.profile.repository import AnswerBank, ProfileRepository, ResumeRepository
from jobpilot.agents.profile.schema import CandidateProfile, missing_required_fields
from jobpilot.core.models import ResumeVersion


@dataclass(frozen=True)
class ImportResult:
    profile: CandidateProfile
    version: int | None
    resume: ResumeVersion
    missing: dict[str, str]
    updated: tuple[str, ...] = ()  # fields this resume filled in or added to
    kept: tuple[str, ...] = ()  # fields where the profile's value won over a different one


class ProfileAgent:
    def __init__(
        self, session: Session, resume_dir: Path, extractor: ProfileExtractor | None = None
    ) -> None:
        self.profiles = ProfileRepository(session)
        self.resumes = ResumeRepository(session, resume_dir)
        self.answers = AnswerBank(session)
        self._extractor = extractor

    def import_resume(
        self, path: Path, label: str | None = None, text: str | None = None
    ) -> ImportResult:
        """`text`: the document's text when the caller already read it (to report progress)."""
        if self._extractor is None:
            raise RuntimeError("ProfileAgent was built without an extractor")
        if text is None:
            text = extract_text(path)
        extracted = self._extractor.extract(text)
        current = self.profiles.current()
        merged = merge_profiles(current, extracted)
        row = self.profiles.save(merged, source="resume")
        resume = self.resumes.add(path, label or path.stem)
        return ImportResult(
            profile=merged,
            version=row.version if row else None,
            resume=resume,
            missing=missing_required_fields(merged),
            updated=tuple(sorted(diff_profiles(current, merged))),
            kept=tuple(sorted(kept_over_resume(current, extracted))),
        )

    def update(self, patch: dict[str, Any], *, source: str = "user") -> CandidateProfile:
        updated = apply_patch(self.profiles.current(), patch)
        self.profiles.save(updated, source=source)
        return updated

    def missing_fields(self) -> dict[str, str]:
        return missing_required_fields(self.profiles.current())
