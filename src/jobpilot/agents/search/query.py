"""Turn the candidate profile into portal search queries."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jobpilot.agents.profile.schema import CandidateProfile, WorkMode

MAX_QUERIES = 8
MAX_KEYWORD_SKILLS = 3


class SearchQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    location: str  # "" means anywhere; "remote" for remote-only searches
    experience_years: float | None
    skills: tuple[str, ...]
    since: datetime
    until: datetime

    @property
    def keywords(self) -> str:
        return " ".join((self.role, *self.skills)).strip()


def build_queries(profile: CandidateProfile, since: datetime, until: datetime) -> list[SearchQuery]:
    roles = [r for r in profile.target_roles if r.strip()] or (
        [profile.current_role] if profile.current_role else []
    )
    if not roles:
        raise ValueError("set target roles (or a current role) in your profile before searching")

    locations = list(
        dict.fromkeys(loc.strip() for loc in profile.preferred_locations if loc.strip())
    )
    if not locations and profile.current_location:
        locations = [profile.current_location]
    if WorkMode.REMOTE in profile.work_modes:
        locations.append("remote")
    locations = locations or [""]

    top_skills = tuple(
        s.name for s in sorted(profile.skills, key=lambda s: s.years or 0, reverse=True)
    )[:MAX_KEYWORD_SKILLS]
    queries = [
        SearchQuery(
            role=role,
            location=location,
            experience_years=profile.total_experience_years,
            skills=top_skills,
            since=since,
            until=until,
        )
        for role in roles
        for location in locations
    ]
    return queries[:MAX_QUERIES]
