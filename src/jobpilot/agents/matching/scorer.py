"""Deterministic 0-100 match score with a short, human-readable reason.

The numbers come only from these rules, so a score can be explained and unit-tested. An LLM is
used elsewhere for borderline red flags, never for the score itself."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot.agents.profile.schema import CandidateProfile, WorkMode
from jobpilot.agents.search.normalize import normalize_location
from jobpilot.agents.search.schema import NormalizedJob

WEIGHTS = {"skills": 0.40, "experience": 0.20, "location": 0.15, "seniority": 0.15, "salary": 0.10}
NEUTRAL = 0.6  # unknown data is neither rewarded nor punished hard
_LEVEL_MIN_YEARS = {
    "intern": (0, 1),
    "junior": (0, 3),
    "senior": (5, 99),
    "lead": (6, 99),
    "staff": (8, 99),
    "principal": (10, 99),
}


@dataclass(frozen=True)
class SubScores:
    skills: float
    experience: float
    location: float
    seniority: float
    salary: float

    def total(self) -> int:
        return round(100 * sum(getattr(self, k) * w for k, w in WEIGHTS.items()))


@dataclass(frozen=True)
class MatchResult:
    score: int
    reason: str
    sub_scores: SubScores


def _mentions(text: str, skill: str) -> bool:
    # Word-boundary match that also works for c++, c#, .net, node.js.
    pattern = rf"(?<![\w+#.]){re.escape(skill.casefold())}(?![\w+#])"
    return re.search(pattern, text.casefold()) is not None


def score_skills(profile: CandidateProfile, job: NormalizedJob) -> tuple[float, str]:
    mine = [s.name for s in profile.skills]
    if not mine:
        return NEUTRAL, "no skills in profile"
    if job.skills:
        wanted = list(dict.fromkeys(job.skills))
        hits = [w for w in wanted if any(w.casefold() == m.casefold() for m in mine)]
        return len(hits) / len(wanted), f"skills {len(hits)}/{len(wanted)}" + (
            f" ({', '.join(hits[:4])})" if hits else ""
        )
    text = f"{job.title} {job.description}"
    hits = [m for m in mine if _mentions(text, m)]
    if not job.description:
        return NEUTRAL, "no description to compare skills"
    # Listings mention a handful of skills; 4 overlaps is a strong match.
    return min(1.0, len(hits) / min(4, len(mine))), (
        f"mentions {', '.join(hits[:4])}" if hits else "none of your skills mentioned"
    )


def score_experience(profile: CandidateProfile, job: NormalizedJob) -> tuple[float, str]:
    years = profile.total_experience_years
    low, high = job.experience_min, job.experience_max
    if years is None:
        return 0.5, "your experience not set"
    if low is None and high is None:
        return NEUTRAL, "experience band not shown"
    if low is not None and years < low:
        gap = low - years
        return max(0.0, 1 - gap / 3), f"{years:g} yrs is {gap:g} below the {low:g}+ band"
    if high is not None and years > high:
        gap = years - high
        return max(0.0, 1 - gap / 6), f"{years:g} yrs is {gap:g} above the band"
    band = f"{low or 0:g}-{high:g}" if high is not None else f"{low:g}+"
    return 1.0, f"{years:g} yrs fits {band}"


def score_location(profile: CandidateProfile, job: NormalizedJob) -> tuple[float, str]:
    job_loc = normalize_location(job.location)
    remote_job = job.work_mode == "remote" or job_loc == "remote"
    if remote_job:
        if WorkMode.REMOTE in profile.work_modes or not profile.work_modes:
            return 1.0, "remote"
        return 0.4, "remote, but you prefer office/hybrid"
    preferred = {normalize_location(p) for p in profile.preferred_locations if p.strip()}
    if not preferred:
        return NEUTRAL, job.location or "location not shown"
    if job_loc in preferred:
        return 1.0, f"{job.location} is a preferred city"
    return 0.1, f"{job.location or 'unknown location'} not in your locations"


def score_salary(profile: CandidateProfile, job: NormalizedJob) -> tuple[float, str]:
    expected = profile.expected_ctc
    if expected is None or job.salary_max is None:
        return NEUTRAL, "salary not shown" if job.salary_max is None else "expected CTC not set"
    if job.salary_currency and job.salary_currency != expected.currency:
        return NEUTRAL, f"salary in {job.salary_currency}"
    if job.salary_max >= expected.amount:
        return 1.0, "salary meets your expectation"
    if job.salary_max >= 0.85 * expected.amount:
        return 0.6, "salary slightly below expectation"
    return 0.15, "salary below expectation"


def score_seniority(profile: CandidateProfile, job: NormalizedJob) -> tuple[float, str]:
    years = profile.total_experience_years
    title = job.title.casefold()
    level = next((lvl for lvl in _LEVEL_MIN_YEARS if re.search(rf"\b{lvl}\b", title)), None)
    if level is None or years is None:
        return 0.8, ""
    low, high = _LEVEL_MIN_YEARS[level]
    if low <= years <= high:
        return 1.0, f"{level} level fits"
    return 0.3, f"{level} level vs your {years:g} yrs"


def score_job(profile: CandidateProfile, job: NormalizedJob) -> MatchResult:
    parts = {
        "skills": score_skills(profile, job),
        "experience": score_experience(profile, job),
        "location": score_location(profile, job),
        "seniority": score_seniority(profile, job),
        "salary": score_salary(profile, job),
    }
    subs = SubScores(**{k: round(v[0], 3) for k, v in parts.items()})
    reason = "; ".join(text for _, text in parts.values() if text)
    return MatchResult(score=subs.total(), reason=reason[:500], sub_scores=subs)
