"""Structured candidate profile (the Profile agent's output contract)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _year(value: Any) -> Any:
    """Resumes write "2011 – 2015" or "May 2019"; keep the last year (graduation / award).
    A string with no year becomes None instead of failing the whole extraction."""
    if isinstance(value, str):
        years = _YEAR.findall(value)
        return int(years[-1]) if years else None
    return value


def _years(value: Any) -> Any:
    """ "3+ years" → 3, "two" → None."""
    if isinstance(value, str):
        m = _NUMBER.search(value)
        return float(m.group()) if m else None
    return value


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    years: float | None = None

    @field_validator("years", mode="before")
    @classmethod
    def _lenient_years(cls, value: Any) -> Any:
        return _years(value)

    @field_validator("years")
    @classmethod
    def _zero_means_unknown(cls, value: float | None) -> float | None:
        # Nobody lists a skill they have used for zero years; 0 is a model placeholder.
        return value if value is None or value > 0 else None


class Education(BaseModel):
    model_config = ConfigDict(frozen=True)

    degree: str = Field(validation_alias=AliasChoices("degree", "name", "title", "qualification"))
    institution: str = Field(
        "", validation_alias=AliasChoices("institution", "school", "university", "college")
    )
    year: int | None = Field(
        None,
        validation_alias=AliasChoices("year", "graduation_year", "end_year", "duration", "dates"),
    )

    @field_validator("year", mode="before")
    @classmethod
    def _lenient_year(cls, value: Any) -> Any:
        return _year(value)


class Certification(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    issuer: str = ""
    year: int | None = Field(
        None,
        validation_alias=AliasChoices("year", "graduation_year", "end_year", "duration", "dates"),
    )

    @field_validator("year", mode="before")
    @classmethod
    def _lenient_year(cls, value: Any) -> Any:
        return _year(value)


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: float = Field(gt=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    period: str = "year"


class Links(BaseModel):
    model_config = ConfigDict(frozen=True)

    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    full_name: str = ""
    email: str = ""
    phone: str = ""
    current_location: str = ""
    current_role: str = ""
    current_company: str = ""
    total_experience_years: float | None = None
    relevant_experience_years: float | None = None
    summary: str = ""
    skills: list[Skill] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    current_ctc: Money | None = None
    expected_ctc: Money | None = None
    notice_period_days: int | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    work_modes: list[WorkMode] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    target_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    work_authorization: str = ""
    links: Links = Field(default_factory=Links)


class ResumeFacts(BaseModel):
    """What the extraction model may read from a resume. Deliberately excludes salary, notice
    period, experience totals, preferences and work authorization: models tend to fill those
    with plausible placeholders (measured: CTC 0, notice 0, every work mode), and a wrong value
    would end up on real applications. Those come only from the user (USER_ONLY_FIELDS)."""

    model_config = ConfigDict(frozen=True)

    full_name: str = ""
    email: str = ""
    phone: str = ""
    current_location: str = ""
    current_role: str = ""
    current_company: str = ""
    summary: str = ""
    skills: list[Skill] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)

    @model_validator(mode="before")
    @classmethod
    def _drop_unreadable_entries(cls, data: Any) -> Any:
        """One malformed list entry must not fail the whole resume: that costs a full second
        model run (measured ~80 s on a laptop). Unreadable entries are dropped, never guessed."""
        if not isinstance(data, dict):
            return data
        models: dict[str, type[BaseModel]] = {
            "skills": Skill, "education": Education, "certifications": Certification,
        }  # fmt: skip
        cleaned = dict(data)
        for key, model in models.items():
            items = cleaned.get(key)
            if not isinstance(items, list):
                continue
            kept = []
            for item in items:
                try:
                    model.model_validate(item)
                except ValidationError:
                    continue
                kept.append(item)
            cleaned[key] = kept
        return cleaned


USER_ONLY_FIELDS: frozenset[str] = frozenset(CandidateProfile.model_fields) - frozenset(
    ResumeFacts.model_fields
)


# Fields the user must supply before the profile is ready for searching and applying.
# Resumes rarely state these, so the Profile agent asks for them in a short Q&A.
REQUIRED_FIELDS: dict[str, str] = {
    "full_name": "What is your full name?",
    "email": "Which email should employers use?",
    "phone": "Which phone number should employers use?",
    "total_experience_years": "How many years of total work experience do you have?",
    "current_ctc": "What is your current CTC (amount and currency)?",
    "expected_ctc": "What is your expected CTC (amount and currency)?",
    "notice_period_days": "What is your notice period in days?",
    "preferred_locations": "Which locations do you prefer? (comma separated)",
    "work_modes": "Which work modes are you open to? (remote, hybrid, onsite)",
    "target_roles": "Which roles are you targeting? (comma separated)",
    "work_authorization": "What is your work authorization (e.g. Indian citizen, H-1B)?",
}


def missing_required_fields(profile: CandidateProfile) -> dict[str, str]:
    """Required fields that are empty, mapped to the question to ask."""
    return {
        name: question
        for name, question in REQUIRED_FIELDS.items()
        if getattr(profile, name) in (None, "", [])
    }
