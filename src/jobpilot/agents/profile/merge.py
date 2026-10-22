"""Pure functions to merge and diff profiles. Inputs are never mutated."""

from __future__ import annotations

from typing import Any

from jobpilot.agents.profile.schema import CandidateProfile, Skill

EMPTY: tuple[object, ...] = (None, "", [], {})


def _merge_skills(base: list[Skill], incoming: list[Skill]) -> list[Skill]:
    by_name: dict[str, Skill] = {s.name.casefold(): s for s in base}
    for skill in incoming:
        key = skill.name.casefold()
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = skill
        elif existing.years is None and skill.years is not None:
            by_name[key] = Skill(name=existing.name, years=skill.years)
    return list(by_name.values())


def _merge_unique(base: list[Any], incoming: list[Any]) -> list[Any]:
    seen = list(base)
    for item in incoming:
        if item not in seen:
            seen.append(item)
    return seen


def merge_profiles(base: CandidateProfile, incoming: CandidateProfile) -> CandidateProfile:
    """Fill gaps in base from incoming. Values already in base (often user-confirmed) win;
    list fields are unioned so nothing the user entered is lost."""
    base_d = base.model_dump()
    inc_d = incoming.model_dump()
    merged: dict[str, Any] = {}
    for field, base_val in base_d.items():
        inc_val = inc_d[field]
        if field == "skills":
            merged[field] = [s.model_dump() for s in _merge_skills(base.skills, incoming.skills)]
        elif isinstance(base_val, list):
            merged[field] = _merge_unique(base_val, inc_val)
        elif field == "links":
            merged[field] = {k: (v if v not in EMPTY else inc_val[k]) for k, v in base_val.items()}
        else:
            merged[field] = base_val if base_val not in EMPTY else inc_val
    return CandidateProfile.model_validate(merged)


def kept_over_resume(base: CandidateProfile, incoming: CandidateProfile) -> list[str]:
    """Single-value fields where the resume says something different from the profile, and the
    profile's value was kept (see merge_profiles). Shown so the user can correct either."""
    base_d, inc_d = base.model_dump(mode="json"), incoming.model_dump(mode="json")
    return [
        f
        for f, v in base_d.items()
        if not isinstance(v, list | dict)
        and v not in EMPTY
        and inc_d[f] not in EMPTY
        and v != inc_d[f]
    ]


def apply_patch(base: CandidateProfile, patch: dict[str, Any]) -> CandidateProfile:
    """Explicit user edits overwrite. Unknown fields are rejected by validation."""
    unknown = set(patch) - set(CandidateProfile.model_fields)
    if unknown:
        raise ValueError(f"unknown profile fields: {sorted(unknown)}")
    return CandidateProfile.model_validate({**base.model_dump(), **patch})


def diff_profiles(old: CandidateProfile, new: CandidateProfile) -> dict[str, dict[str, Any]]:
    old_d, new_d = old.model_dump(mode="json"), new.model_dump(mode="json")
    return {k: {"old": old_d[k], "new": new_d[k]} for k in new_d if old_d[k] != new_d[k]}
