"""Decide what goes into each application form field, and how sure we are.

Never invents experience, salary or qualifications: a field is filled only from the user's own
data (profile, confirmed Answer Bank, grounded RAG answers, or an earlier H4 answer). A required
field below the confidence threshold goes to the user (checkpoint H4); an optional one is left
blank."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jobpilot.agents.application.forms import FieldKind, FormField
from jobpilot.agents.profile.schema import CandidateProfile, Money

LAKH = 100_000


class SupportsLookup(Protocol):
    def lookup(self, question: str):  # noqa: ANN201 - returns AnswerMatch | None
        ...


@dataclass(frozen=True)
class Proposal:
    field: FormField
    value: str
    confidence: float
    source: str  # override | prefilled | profile | answer_bank | rag | resume | none
    note: str = ""

    def needs_user(self, threshold: float) -> bool:
        return self.field.required and (not self.value or self.confidence < threshold)


# ---- option matching -----------------------------------------------------------------

_NUM = re.compile(r"\d+(?:\.\d+)?")
_YES = {"yes", "y", "true", "willing", "i am", "i do", "agree"}
_NO = {"no", "n", "false", "not willing", "i am not", "i don't"}


def _band(option: str) -> tuple[float, float] | None:
    """ "5-8 years" → (5, 8); "8+ years" → (8, inf); "Less than 1 year" → (0, 1)."""
    t = option.casefold()
    nums = [float(n) for n in _NUM.findall(t)]
    if not nums:
        return None
    if "less than" in t or "under" in t or "upto" in t or "up to" in t:
        return 0.0, nums[0]
    if "+" in t or "more than" in t or "above" in t or "or more" in t:
        return nums[0], float("inf")
    if len(nums) >= 2:
        return nums[0], nums[1]
    return nums[0], nums[0]


def choose_option(value: str, options: tuple[str, ...]) -> tuple[str, float] | None:
    """Map an answer onto one of the form's options, with a confidence."""
    v = value.strip().casefold()
    if not v or not options:
        return None
    for opt in options:
        if opt.casefold() == v:
            return opt, 1.0
    first = v.split()[0].strip(",.") if v.split() else v
    polarity = "yes" if first in _YES or v in _YES else "no" if first in _NO or v in _NO else None
    if polarity:
        for opt in options:
            if opt.casefold().split()[0].strip(",.") == polarity:
                return opt, 0.95
    if m := _NUM.search(v):
        number = float(m.group())
        for opt in options:
            band = _band(opt)
            if band and band[0] <= number <= band[1] and not (band[0] == band[1] != number):
                return opt, 0.9
    for opt in options:
        o = opt.casefold()
        if len(o) > 2 and (o in v or v in o):
            return opt, 0.8
    return None


# ---- profile rules -------------------------------------------------------------------


def _money(value: Money | None, label: str) -> str | None:
    if value is None:
        return None
    if re.search(r"lakh|lac|lpa", label):
        return f"{value.amount / LAKH:g}"
    return f"{value.amount:.0f}"


def _profile_value(label: str, p: CandidateProfile) -> str | None:
    t = label.casefold()
    first, _, last = p.full_name.partition(" ")
    rules: list[tuple[str, Callable[[], str | None]]] = [
        (r"\bfirst name\b", lambda: first or None),
        (r"\b(last|sur) ?name\b", lambda: last or None),
        (r"^(full )?name$|\byour name\b|\bfull name\b", lambda: p.full_name or None),
        (r"e-?mail", lambda: p.email or None),
        (r"phone|mobile|contact number", lambda: p.phone or None),
        (r"linkedin", lambda: p.links.linkedin),
        (r"github", lambda: p.links.github),
        (r"portfolio|website|personal site", lambda: p.links.portfolio),
        (
            r"expected (ctc|salary|compensation)|salary expectation",
            lambda: _money(p.expected_ctc, t),
        ),
        (r"current (ctc|salary|compensation)", lambda: _money(p.current_ctc, t)),
        (
            r"notice period",
            lambda: None if p.notice_period_days is None else str(p.notice_period_days),
        ),
        (
            r"(total|overall) (years of )?experience|years of experience$|^experience",
            lambda: None if p.total_experience_years is None else f"{p.total_experience_years:g}",
        ),
        (
            r"relevant experience",
            lambda: (
                None if p.relevant_experience_years is None else f"{p.relevant_experience_years:g}"
            ),
        ),
        (r"current (company|employer|organi[sz]ation)", lambda: p.current_company or None),
        (r"current (title|designation|role|position)|job title", lambda: p.current_role or None),
        (
            r"current (location|city)|where are you (based|located)|^city$|^location$",
            lambda: p.current_location or None,
        ),
    ]
    for pattern, getter in rules:
        if re.search(pattern, t):
            return getter()
    return None


def _skill_years(label: str, p: CandidateProfile) -> str | None:
    """ "Years of experience in Kafka" → the profile's stated years for Kafka, if any."""
    t = label.casefold()
    if "experience" not in t and "years" not in t:
        return None
    for skill in p.skills:
        if skill.years and re.search(
            rf"(?<![\w+#.]){re.escape(skill.name.casefold())}(?![\w+#])", t
        ):
            return f"{skill.years:g}"
    return None


def _notice_in_option_units(field: FormField, value: str) -> str:
    """Notice-period choices are often months ("1 month", "Immediate"); the profile has days."""
    if "notice" not in field.label.casefold() or not value.isdigit() or not field.options:
        return value
    days = int(value)
    if days == 0:
        return next((o for o in field.options if "immediate" in o.casefold()), value)
    if any("month" in o.casefold() for o in field.options) and days >= 30:
        return f"{days / 30:g} months"
    return f"{days} days"


# ---- answerer ------------------------------------------------------------------------


class FieldAnswerer:
    def __init__(
        self,
        profile: CandidateProfile,
        *,
        answer_bank: SupportsLookup,
        resume_path: Path | None,
        rag: Callable[[str], tuple[str, float]] | None = None,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._p = profile
        self._bank = answer_bank
        self._resume = resume_path
        self._rag = rag
        self._overrides = overrides or {}

    def propose(self, field: FormField) -> Proposal:
        if field.key in self._overrides:
            return self._shape(field, self._overrides[field.key], 1.0, "override")
        if field.kind is FieldKind.FILE:
            if self._resume is not None and re.search(r"resume|cv|curriculum", field.label, re.I):
                return Proposal(field, str(self._resume), 1.0, "resume")
            return Proposal(field, "", 0.0, "none", "file upload not recognised")
        if field.value and field.kind is not FieldKind.CHECKBOX:
            return Proposal(field, field.value, 1.0, "prefilled")
        if value := _profile_value(field.label, self._p) or _skill_years(field.label, self._p):
            return self._shape(field, _notice_in_option_units(field, value), 1.0, "profile")
        if (match := self._bank.lookup(field.label)) is not None:
            return self._shape(field, match.entry.answer, match.confidence, "answer_bank")
        if field.required and self._rag is not None and field.kind is not FieldKind.CHECKBOX:
            answer, confidence = self._rag(field.label)
            if answer:
                return self._shape(field, answer, confidence, "rag")
        return Proposal(field, "", 0.0, "none")

    def _shape(self, field: FormField, value: str, confidence: float, source: str) -> Proposal:
        """Fit the value to the field type; choices must match one of the form's options."""
        if field.kind in (FieldKind.SELECT, FieldKind.RADIO):
            picked = choose_option(value, field.options)
            if picked is None:
                return Proposal(field, "", 0.0, source, f"'{value}' matches none of the options")
            return Proposal(field, picked[0], round(confidence * picked[1], 3), source)
        if field.kind is FieldKind.CHECKBOX:
            yes = value.strip().casefold().split()[0] in _YES if value.strip() else False
            return Proposal(field, "true" if yes else "", confidence, source)
        if field.kind is FieldKind.NUMBER:
            m = _NUM.search(value.replace(",", ""))
            if m is None:
                return Proposal(field, "", 0.0, source, "expected a number")
            return Proposal(field, m.group(), confidence, source)
        return Proposal(field, value, confidence, source)
