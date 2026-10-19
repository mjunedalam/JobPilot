"""Turn a free-text Q&A answer into a typed profile patch value."""

from __future__ import annotations

import re
from typing import Any

from jobpilot.agents.profile.schema import WorkMode

LIST_FIELDS = frozenset(
    {"preferred_locations", "target_roles", "target_companies", "excluded_companies"}
)
MONEY_FIELDS = frozenset({"current_ctc", "expected_ctc"})
FLOAT_FIELDS = frozenset({"total_experience_years", "relevant_experience_years"})
INT_FIELDS = frozenset({"notice_period_days"})

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_LAKH, _CRORE = 100_000, 10_000_000


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _number(text: str) -> float:
    match = _NUMBER.search(text.replace(",", ""))
    if not match:
        raise ValueError(f"no number found in '{text}'")
    return float(match.group())


def _money(text: str) -> dict[str, Any]:
    lowered = text.casefold()
    amount = _number(text)
    if re.search(r"\b(lpa|lakh|lakhs|l)\b", lowered):
        amount *= _LAKH
    elif re.search(r"\b(cr|crore|crores)\b", lowered):
        amount *= _CRORE
    currency = next(
        (code for code in ("USD", "EUR", "GBP", "INR") if code.casefold() in lowered),
        "USD" if "$" in text else "INR",
    )
    return {"amount": amount, "currency": currency, "period": "year"}


def coerce_answer(field: str, text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("answer is empty")
    if field in LIST_FIELDS:
        return _split(text)
    if field == "work_modes":
        modes = [m.casefold() for m in _split(text)]
        valid = {m.value for m in WorkMode}
        bad = [m for m in modes if m not in valid]
        if bad:
            raise ValueError(f"unknown work mode(s) {bad}; use remote, hybrid or onsite")
        return modes
    if field in MONEY_FIELDS:
        return _money(text)
    if field in FLOAT_FIELDS:
        return _number(text)
    if field in INT_FIELDS:
        return int(_number(text))
    return text
