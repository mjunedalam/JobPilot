"""Parsers for the free-text fields job portals show: dates, experience bands, salaries."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_RELATIVE = re.compile(r"(\d+)\+?\s*(minute|min|hour|hr|day|week|wk|month|mo)s?\b", re.IGNORECASE)
_UNITS = {
    "minute": "minutes",
    "min": "minutes",
    "hour": "hours",
    "hr": "hours",
    "day": "days",
    "week": "weeks",
    "wk": "weeks",
    "month": "days",
    "mo": "days",
}


def parse_posted(text: str, fetched_at: datetime) -> datetime | None:
    """ "Just now", "Today", "3 days ago", "30+ days ago", "Posted 1 week ago" → a datetime.
    Returns None when the text has no recognisable date (never guesses)."""
    t = text.strip().casefold()
    if not t:
        return None
    if any(w in t for w in ("just now", "few hours", "moments ago", "today", "just posted")):
        return fetched_at
    if "yesterday" in t:
        return fetched_at - timedelta(days=1)
    m = _RELATIVE.search(t)
    if not m:
        return _absolute_date(text, fetched_at)
    amount, unit = int(m.group(1)), _UNITS[m.group(2).casefold()]
    if m.group(2).casefold() in ("month", "mo"):
        amount *= 30
    return fetched_at - timedelta(**{unit: amount})


_ABSOLUTE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y")
_DATE_IN_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2} [A-Za-z]{3,9},? \d{4}|[A-Za-z]{3,9} \d{1,2},? \d{4}"
    r"|\d{1,2}/\d{1,2}/\d{4}"
)


def _absolute_date(text: str, fetched_at: datetime) -> datetime | None:
    """ "Posted: 18 October 2026", "2026-10-18", "Oct 18, 2026", "18/10/2026" (day first, as on
    Indian portals). A date after the fetch time is a misread and is rejected."""
    match = _DATE_IN_TEXT.search(text)
    if not match:
        return None
    candidate = match.group(0).replace(",", "")
    for fmt in _ABSOLUTE_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt).replace(tzinfo=fetched_at.tzinfo)
        except ValueError:
            continue
        return parsed if parsed <= fetched_at else None
    return None


_EXP = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*\+")


def parse_experience(text: str) -> tuple[float | None, float | None]:
    """ "5-8 Yrs" → (5, 8); "10+ years" → (10, None); "Fresher" → (0, 1)."""
    t = text.casefold()
    if "fresher" in t:
        return 0.0, 1.0
    m = _EXP.search(t)
    if not m:
        return None, None
    if m.group(3):
        return float(m.group(3)), None
    return float(m.group(1)), float(m.group(2))


_LAKH, _CRORE = 100_000, 10_000_000
# Digits with any comma grouping (Indian "12,00,000" or western "1,200,000"), optional "k".
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_SAL = re.compile(_NUM + r"\s*k?\s*(?:-|to|–)\s*" + _NUM)


def parse_salary(text: str) -> tuple[float | None, float | None, str]:
    """ "15-25 Lacs PA" → (1.5M, 2.5M, "INR"); "₹12,00,000 - ₹18,00,000" → INR amounts;
    "$120k-150k" → USD. "Not disclosed" → (None, None, "")."""
    t = text.casefold().replace(" ", "")
    if not t or "notdisclosed" in t or "competitive" in t:
        return None, None, ""
    currency = "USD" if "$" in t or "usd" in t else "EUR" if "€" in t else "INR"
    multiplier = 1.0
    if any(w in t for w in ("lac", "lakh", "lpa")):
        multiplier = _LAKH
    elif any(w in t for w in ("cr", "crore")):
        multiplier = _CRORE
    elif "k" in t:
        multiplier = 1_000
    cleaned = re.sub(r"[₹$€]|rs\.?|inr|usd", "", t)
    m = _SAL.search(cleaned)
    if not m:
        return None, None, ""

    def num(s: str) -> float:
        return float(s.replace(",", "")) * multiplier

    return num(m.group(1)), num(m.group(2)), currency
