"""Calendar free/busy (optional) and the slot finder used when a reply proposes meeting times.

Only busy intervals are read (Google `calendar.freebusy`, Graph `getSchedule`): no event titles,
attendees or descriptions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from jobpilot.mail.base import MailError
from jobpilot.mail.http import AuthorizedHttp

Interval = tuple[datetime, datetime]


class FreeBusy(Protocol):
    def busy(self, start: datetime, end: datetime) -> list[Interval]: ...


def _parse(value: str, tz: str = "UTC") -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


class GoogleFreeBusy:
    URL = "https://www.googleapis.com/calendar/v3/freeBusy"

    def __init__(self, http: AuthorizedHttp) -> None:
        self._http = http

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        data = self._http.json(
            "POST",
            self.URL,
            json={
                "timeMin": start.astimezone(UTC).isoformat(),
                "timeMax": end.astimezone(UTC).isoformat(),
                "items": [{"id": "primary"}],
            },
        )
        primary = data.get("calendars", {}).get("primary", {})
        if primary.get("errors"):
            raise MailError("Google Calendar could not read free/busy for the primary calendar")
        return [(_parse(b["start"]), _parse(b["end"])) for b in primary.get("busy", [])]


class GraphFreeBusy:
    URL = "https://graph.microsoft.com/v1.0/me/calendar/getSchedule"

    def __init__(self, http: AuthorizedHttp, address: str) -> None:
        self._http = http
        self._address = address

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        fmt = "%Y-%m-%dT%H:%M:%S"
        data = self._http.json(
            "POST",
            self.URL,
            json={
                "schedules": [self._address],
                "startTime": {"dateTime": start.astimezone(UTC).strftime(fmt), "timeZone": "UTC"},
                "endTime": {"dateTime": end.astimezone(UTC).strftime(fmt), "timeZone": "UTC"},
                "availabilityViewInterval": 30,
            },
        )
        items = (data.get("value") or [{}])[0].get("scheduleItems", [])
        return [
            (
                _parse(i["start"]["dateTime"], i["start"].get("timeZone", "UTC")),
                _parse(i["end"]["dateTime"], i["end"].get("timeZone", "UTC")),
            )
            for i in items
            if i.get("status") != "free"
        ]


class LocalFreeBusy:
    """Dev/demo: busy intervals from `busy.json` next to the local mailbox."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        if not self._path.is_file():
            return []
        rows = json.loads(self._path.read_text())
        spans = [(_parse(r["start"]), _parse(r["end"])) for r in rows]
        return [(s, e) for s, e in spans if e > start and s < end]


@dataclass(frozen=True)
class WorkingHours:
    timezone: str = "Asia/Kolkata"
    start: time = time(10, 0)
    end: time = time(18, 0)
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday = 0


def free_slots(
    busy: list[Interval],
    *,
    now: datetime,
    hours: WorkingHours,
    duration: timedelta = timedelta(hours=1),
    days: int = 5,
    per_day: int = 2,
    limit: int = 6,
) -> list[datetime]:
    """Start times of free slots on the next working days (from tomorrow), on the hour."""
    tz = ZoneInfo(hours.timezone)
    local_now = now.astimezone(tz)
    slots: list[datetime] = []
    day: date = local_now.date()
    checked = 0
    while checked < days and len(slots) < limit:
        day += timedelta(days=1)
        if day.weekday() not in hours.weekdays:
            continue
        checked += 1
        taken = 0
        cursor = datetime.combine(day, hours.start, tz)
        close = datetime.combine(day, hours.end, tz)
        while cursor + duration <= close and taken < per_day and len(slots) < limit:
            end = cursor + duration
            if not any(s < end and e > cursor for s, e in busy):
                slots.append(cursor)
                taken += 1
                cursor = end + duration  # leave a gap so the options spread over the day
            else:
                cursor += timedelta(minutes=30)
    return slots


def describe_slot(start: datetime, duration: timedelta, tz: str) -> str:
    local = start.astimezone(ZoneInfo(tz))
    end = local + duration
    label = local.tzname() or tz
    return f"{local:%A %d %b}, {local:%-I:%M %p}–{end:%-I:%M %p} {label}"
