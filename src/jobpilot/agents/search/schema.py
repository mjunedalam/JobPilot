"""Job Search contracts: recency windows (HITL H2) and the normalised posting shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class RecencyPreset(StrEnum):
    LAST_HOUR = "1h"
    LAST_DAY = "24h"
    LAST_WEEK = "7d"
    LAST_MONTH = "30d"
    CUSTOM = "custom"


_PRESET_DELTAS = {
    RecencyPreset.LAST_HOUR: timedelta(hours=1),
    RecencyPreset.LAST_DAY: timedelta(days=1),
    RecencyPreset.LAST_WEEK: timedelta(days=7),
    RecencyPreset.LAST_MONTH: timedelta(days=30),
}
MAX_CUSTOM_WINDOW = timedelta(days=90)


class Recency(BaseModel):
    """The user's choice at checkpoint H2."""

    model_config = ConfigDict(frozen=True)

    preset: RecencyPreset
    since: datetime | None = None
    until: datetime | None = None

    @model_validator(mode="after")
    def _custom_needs_bounds(self) -> Recency:
        if self.preset is RecencyPreset.CUSTOM:
            if self.since is None:
                raise ValueError("custom recency needs `since`")
            until = self.until or datetime.now(UTC)
            if as_utc(self.since) >= as_utc(until):
                raise ValueError("`since` must be before `until`")
            if as_utc(until) - as_utc(self.since) > MAX_CUSTOM_WINDOW:
                raise ValueError("custom window is limited to 90 days")
        return self

    def window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        now = as_utc(now or datetime.now(UTC))
        if self.preset is RecencyPreset.CUSTOM:
            assert self.since is not None
            return as_utc(self.since), as_utc(self.until or now)
        return now - _PRESET_DELTAS[self.preset], now


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; JobPilot stores UTC, so naive means UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class RawPosting(BaseModel):
    """What a portal adapter returns for one listing, before normalisation."""

    model_config = ConfigDict(frozen=True)

    portal: str
    url: HttpUrl
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location: str = ""
    description: str = ""
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    experience_min: float | None = Field(None, ge=0, le=60)
    experience_max: float | None = Field(None, ge=0, le=60)
    salary_min: float | None = Field(None, ge=0)
    salary_max: float | None = Field(None, ge=0)
    salary_currency: str = ""
    skills: tuple[str, ...] = ()
    work_mode: str = ""


class NormalizedJob(BaseModel):
    """One real job, possibly listed on several portals."""

    model_config = ConfigDict(frozen=True)

    bucket: str  # company_norm|location_norm
    title_norm: str
    title: str
    company: str
    location: str
    description: str
    posted_at: datetime | None
    expires_at: datetime | None
    experience_min: float | None
    experience_max: float | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str
    skills: tuple[str, ...]
    work_mode: str
    portals: tuple[str, ...]
    source_urls: tuple[str, ...]
    # Red-flag labels found in any single listing, kept through merges.
    source_flags: tuple[str, ...] = ()

    @property
    def dedup_key(self) -> str:
        return f"{self.bucket}|{self.title_norm}"
