"""Structured candidate profile (the Profile agent's output contract)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    years: float | None = None


class Education(BaseModel):
    model_config = ConfigDict(frozen=True)

    degree: str
    institution: str = ""
    year: int | None = None


class Certification(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    issuer: str = ""
    year: int | None = None


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: float
    currency: str = "INR"
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
