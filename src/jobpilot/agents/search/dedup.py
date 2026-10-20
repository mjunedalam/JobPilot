"""Cross-portal deduplication: same company + same location + similar title = one job."""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher

from jobpilot.agents.search.schema import NormalizedJob, as_utc

TITLE_SIMILARITY = 0.85
# Postings that differ in level are different jobs even when the rest of the title matches.
_LEVEL_WORDS = frozenset(
    {
        "intern",
        "trainee",
        "junior",
        "associate",
        "senior",
        "lead",
        "staff",
        "principal",
        "manager",
        "head",
        "director",
        "vp",
        "i",
        "ii",
        "iii",
        "iv",
        "l1",
        "l2",
        "l3",
        "l4",
        "l5",
    }
)


def _split_level(title_norm: str) -> tuple[frozenset[str], str]:
    tokens = title_norm.split()
    level = frozenset(t for t in tokens if t in _LEVEL_WORDS)
    rest = " ".join(sorted(t for t in tokens if t not in _LEVEL_WORDS))
    return level, rest


def titles_match(a: str, b: str) -> bool:
    if a == b:
        return True
    level_a, rest_a = _split_level(a)
    level_b, rest_b = _split_level(b)
    if level_a != level_b:
        return False
    tokens_a, tokens_b = set(rest_a.split()), set(rest_b.split())
    jaccard = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
    return max(jaccard, SequenceMatcher(None, rest_a, rest_b).ratio()) >= TITLE_SIMILARITY


def same_job(a: NormalizedJob, b: NormalizedJob) -> bool:
    return a.bucket == b.bucket and titles_match(a.title_norm, b.title_norm)


def _union(*seqs: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for seq in seqs for item in seq))


def _first(*values):  # noqa: ANN002, ANN202
    return next((v for v in values if v not in (None, "")), None)


def merge(a: NormalizedJob, b: NormalizedJob) -> NormalizedJob:
    """Combine two listings of one job. Returns a new object; inputs are unchanged."""
    # Stored rows come back from SQLite naive (UTC); compare everything as aware UTC.
    posted = [as_utc(d) for d in (a.posted_at, b.posted_at) if d is not None]
    expires = [as_utc(d) for d in (a.expires_at, b.expires_at) if d is not None]
    richer = a if len(a.description) >= len(b.description) else b
    return a.model_copy(
        update={
            "description": richer.description,
            "posted_at": min(posted) if posted else None,
            "expires_at": max(expires) if expires else None,
            "experience_min": _first(a.experience_min, b.experience_min),
            "experience_max": _first(a.experience_max, b.experience_max),
            "salary_min": _first(a.salary_min, b.salary_min),
            "salary_max": _first(a.salary_max, b.salary_max),
            "salary_currency": _first(a.salary_currency, b.salary_currency) or "",
            "work_mode": _first(a.work_mode, b.work_mode) or "",
            "skills": _union(a.skills, b.skills),
            "portals": _union(a.portals, b.portals),
            "source_urls": _union(a.source_urls, b.source_urls),
            "source_flags": _union(a.source_flags, b.source_flags),
        }
    )


def deduplicate(jobs: Iterable[NormalizedJob]) -> list[NormalizedJob]:
    """Collapse listings of the same job. Order follows first appearance."""
    clusters: list[NormalizedJob] = []
    for job in jobs:
        for index, existing in enumerate(clusters):
            if same_job(existing, job):
                clusters[index] = merge(existing, job)
                break
        else:
            clusters.append(job)
    return clusters
