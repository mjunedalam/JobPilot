"""Rule-based red flags for scam-like or mismatched postings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from jobpilot.agents.profile.schema import CandidateProfile
from jobpilot.agents.search.schema import NormalizedJob


class Severity(StrEnum):
    HIGH = "high"  # likely scam: shown prominently, never auto-approved
    SOFT = "soft"  # worth a second look


@dataclass(frozen=True)
class RedFlag:
    code: str
    severity: Severity
    message: str

    def label(self) -> str:
        return f"{self.severity}:{self.code}: {self.message}"

    @classmethod
    def from_label(cls, label: str) -> RedFlag | None:
        severity, _, rest = label.partition(":")
        code, _, message = rest.partition(": ")
        try:
            return cls(code, Severity(severity), message)
        except ValueError:
            return None


_FEE = re.compile(
    r"(registration|joining|processing|training|security|onboarding)\s+(fee|charges?|deposit)"
    r"|(pay|deposit)\s+(rs\.?|inr|₹|\$)\s*\d+|refundable\s+deposit|fees?\s+(is|are)\s+refundable",
    re.IGNORECASE,
)
_SCAM_PHRASES = re.compile(
    r"earn\s+(up\s+to\s+)?(rs\.?|₹|\$)?\s*[\d,]+\s*(per|/)\s*(day|week)|no\s+interview|"
    r"guaranteed\s+(job|placement|income)|work\s+from\s+home\s+and\s+earn|data\s+entry\s+from\s+home|"
    r"typing\s+job|part[-\s]time\s+online\s+job|investment\s+required",
    re.IGNORECASE,
)
_PERSONAL_EMAIL = re.compile(
    r"[\w.+-]+@(gmail|yahoo|hotmail|outlook|rediffmail|ymail)\.com", re.IGNORECASE
)
_CHAT_ONLY = re.compile(r"\b(whats\s?app|telegram)\b", re.IGNORECASE)
EXPERIENCE_GAP_YEARS = 3
MIN_DESCRIPTION_CHARS = 120


def detect_content_flags(title: str, description: str) -> list[RedFlag]:
    """Flags that depend only on the posting text. Computed per listing before dedup, so a scam
    listing merged into a genuine job (whose longer description would win) still taints it."""
    text = f"{title}\n{description}"
    flags: list[RedFlag] = []
    if _FEE.search(text):
        flags.append(RedFlag("upfront_fee", Severity.HIGH, "asks for a fee or deposit"))
    if _SCAM_PHRASES.search(text):
        flags.append(
            RedFlag("scam_language", Severity.HIGH, "uses get-rich-quick or no-interview language")
        )
    if _PERSONAL_EMAIL.search(text):
        flags.append(
            RedFlag("personal_email", Severity.SOFT, "recruiter uses a personal email address")
        )
    if _CHAT_ONLY.search(text):
        flags.append(
            RedFlag("chat_contact", Severity.SOFT, "asks to continue on WhatsApp/Telegram")
        )
    if description and len(description) < MIN_DESCRIPTION_CHARS:
        flags.append(
            RedFlag("thin_description", Severity.SOFT, "very little detail about the role")
        )
    return flags


def detect_red_flags(job: NormalizedJob, profile: CandidateProfile) -> list[RedFlag]:
    """Content flags on the merged text, plus every listing's own flags, plus profile fit."""
    flags = detect_content_flags(job.title, job.description)
    seen = {f.code for f in flags}
    for label in job.source_flags:
        carried = RedFlag.from_label(label)
        if carried is not None and carried.code not in seen:
            flags.append(carried)
            seen.add(carried.code)
    years = profile.total_experience_years
    if years is not None:
        if job.experience_min is not None and job.experience_min - years > EXPERIENCE_GAP_YEARS:
            flags.append(
                RedFlag(
                    "experience_mismatch",
                    Severity.SOFT,
                    f"needs {job.experience_min:g}+ yrs, you have {years:g}",
                )
            )
        if job.experience_max is not None and years - job.experience_max > 2 * EXPERIENCE_GAP_YEARS:
            flags.append(
                RedFlag(
                    "experience_mismatch",
                    Severity.SOFT,
                    f"band tops out at {job.experience_max:g} yrs, you have {years:g}",
                )
            )
    return flags


def needs_second_opinion(flags: list[RedFlag]) -> bool:
    """Borderline: soft signals only. Clear scams and clean postings skip the LLM."""
    return (
        bool(flags)
        and all(f.severity is Severity.SOFT for f in flags)
        and any(f.code in {"personal_email", "chat_contact", "thin_description"} for f in flags)
    )
