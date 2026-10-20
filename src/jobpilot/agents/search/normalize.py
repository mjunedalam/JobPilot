"""Pure normalisation used for deduplication and filtering. No I/O."""

from __future__ import annotations

import re
import unicodedata

from jobpilot.agents.matching.red_flags import detect_content_flags
from jobpilot.agents.search.schema import NormalizedJob, RawPosting

_COMPANY_SUFFIXES = re.compile(
    r"\b(private|pvt|limited|ltd|llp|llc|inc|incorporated|corp|corporation|co|company|gmbh|plc|"
    r"technologies|technology|tech|solutions|services|software|systems|labs)\b\.?",
)
_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "bombay": "mumbai",
    "navi mumbai": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    "new delhi": "delhi",
    "delhi ncr": "delhi",
    "ncr": "delhi",
    "noida": "noida",
    "poona": "pune",
    "trivandrum": "thiruvananthapuram",
}
_REMOTE_WORDS = re.compile(r"\b(remote|work from home|wfh|anywhere)\b")
_HYBRID_WORDS = re.compile(r"\bhybrid\b")
_TITLE_EQUIV = [
    (re.compile(r"\bsr\b\.?"), "senior"),
    (re.compile(r"\bjr\b\.?"), "junior"),
    (re.compile(r"\bengg?\b\.?"), "engineer"),
    (re.compile(r"\bdev\b"), "developer"),
    (re.compile(r"\bmgr\b"), "manager"),
    (re.compile(r"\bswe\b"), "software engineer"),
    (re.compile(r"\bsde\b"), "software development engineer"),
]
_NOISE = re.compile(r"[^a-z0-9+#. ]+")
_SPACES = re.compile(r"\s+")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SPACES.sub(" ", _NOISE.sub(" ", text.casefold())).strip()


def normalize_company(name: str) -> str:
    folded = _fold(name).replace(".", " ")
    stripped = _SPACES.sub(" ", _COMPANY_SUFFIXES.sub(" ", folded)).strip()
    return stripped or folded


def normalize_location(location: str) -> str:
    """Primary city of a location string, with common aliases merged ("Bangalore" == "Bengaluru").
    Remote listings normalise to "remote"."""
    folded = _fold(location)
    if not folded or _REMOTE_WORDS.search(folded):
        return "remote" if folded else ""
    # Split on separators before folding (which strips commas): "Pune, Maharashtra, India" and
    # "Pune, Bengaluru" both have Pune as their primary city.
    first = _fold(re.split(r"[,/|;(]| or | and ", location.casefold())[0])
    for alias, city in sorted(_CITY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in first:
            return city
    return first


_PARENTHETICAL = re.compile(r"[\(\[].*?[\)\]]")  # "(Remote)", "[5-8 yrs]"
_EXP_RANGE = re.compile(r"\b\d+\s*(?:-|to)\s*\d+\s*\+?\s*(?:yrs?|years?)\b", re.IGNORECASE)


def normalize_title(title: str) -> str:
    # Strip annotations before folding, which would otherwise remove the brackets and hyphens.
    text = _fold(_EXP_RANGE.sub(" ", _PARENTHETICAL.sub(" ", title)))
    for pattern, replacement in _TITLE_EQUIV:
        text = pattern.sub(replacement, text)
    return _SPACES.sub(" ", text).strip(" -.")


def detect_work_mode(*texts: str) -> str:
    blob = _fold(" ".join(texts))
    if _HYBRID_WORDS.search(blob):
        return "hybrid"
    if _REMOTE_WORDS.search(blob):
        return "remote"
    return ""


def normalize_posting(raw: RawPosting) -> NormalizedJob:
    company_norm = normalize_company(raw.company)
    location_norm = normalize_location(raw.location)
    return NormalizedJob(
        bucket=f"{company_norm}|{location_norm}",
        title_norm=normalize_title(raw.title),
        title=raw.title.strip(),
        company=raw.company.strip(),
        location=raw.location.strip(),
        description=raw.description.strip(),
        posted_at=raw.posted_at,
        expires_at=raw.expires_at,
        experience_min=raw.experience_min,
        experience_max=raw.experience_max,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency.upper()[:3],
        skills=tuple(dict.fromkeys(s.strip() for s in raw.skills if s.strip())),
        work_mode=raw.work_mode or detect_work_mode(raw.title, raw.location),
        portals=(raw.portal,),
        source_urls=(str(raw.url),),
        source_flags=tuple(f.label() for f in detect_content_flags(raw.title, raw.description)),
    )
