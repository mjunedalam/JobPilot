"""Autopilot: the scheduled run that does the legwork and brings you the decisions.

    at the chosen time on the chosen days:
      search enabled portals + saved careers pages (recency window) → rank (in the search)
      → start a review if there is anything new: the shortlist waits for you at H3
      → check the mailbox

It never approves anything. Every submission and every send still needs your logged approval,
here or from your phone; Autopilot only makes sure the decisions are waiting when you look."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.core.audit import log_action
from jobpilot.core.db import session_scope
from jobpilot.core.killswitch import KillSwitch
from jobpilot.core.models import AppSetting, Job, WorkflowRun
from jobpilot.core.status import JobStatus

log = logging.getLogger(__name__)
KEY, LAST_KEY = "autopilot", "autopilot_last"
ACTOR = "autopilot"


class AutopilotSettings(BaseModel):
    enabled: bool = False
    time: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Monday = 0
    recency: str = Field("24h", pattern=r"^(1h|24h|7d|30d)$")
    sources: list[str] = Field(default_factory=list, max_length=10)  # careers-page URLs
    check_mail: bool = True


@dataclass
class RunReport:
    trigger: str
    started_at: str
    finished_at: str = ""
    searched: list[str] = field(default_factory=list)
    new_jobs: int = 0
    review_started: bool = False
    mail_checked: bool = False
    notes: list[str] = field(default_factory=list)


class Autopilot:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        kill_switch: KillSwitch,
        timezone: str,
        enabled_portals: Callable[[], list[str]],
        search: Callable[[list[str], str], dict[str, Any]],
        start_review: Callable[[], str],
        poll_mail: Callable[[], Any] | None,
        has_mailbox: Callable[[], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._kill = kill_switch
        self.timezone = timezone
        self._portals = enabled_portals
        self._search = search
        self._start_review = start_review
        self._poll_mail = poll_mail
        self._has_mailbox = has_mailbox
        self._now = clock
        self._lock = threading.Lock()

    # ---- settings -----------------------------------------------------------------------

    def settings(self) -> AutopilotSettings:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, KEY)
            return AutopilotSettings.model_validate(row.value) if row else AutopilotSettings()

    def save(self, settings: AutopilotSettings) -> AutopilotSettings:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, KEY)
            if row is None:
                s.add(AppSetting(key=KEY, value=settings.model_dump()))
            else:
                row.value = settings.model_dump()
            log_action(
                s, ACTOR, "autopilot_settings",
                payload={"enabled": settings.enabled, "time": settings.time, "days": settings.days},
            )  # fmt: skip
        return settings

    def last_run(self) -> dict[str, Any] | None:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, LAST_KEY)
            return dict(row.value) if row else None

    def next_run(self, now: datetime | None = None) -> datetime | None:
        cfg = self.settings()
        if not cfg.enabled or not cfg.days:
            return None
        tz = ZoneInfo(self.timezone)
        local = (now or self._now()).astimezone(tz)
        at = time.fromisoformat(cfg.time)
        for offset in range(8):
            day = (local + timedelta(days=offset)).date()
            candidate = datetime.combine(day, at, tz)
            if day.weekday() in cfg.days and candidate > local:
                return candidate
        return None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    # ---- the run ------------------------------------------------------------------------

    def run(self, trigger: str = "schedule") -> RunReport:
        report = RunReport(trigger=trigger, started_at=self._now().isoformat())
        if not self._lock.acquire(blocking=False):
            report.notes.append("a run is already in progress")
            return report
        try:
            self._run(report)
        finally:
            report.finished_at = self._now().isoformat()
            self._record(report)
            self._lock.release()
        return report

    def _run(self, report: RunReport) -> None:
        if self._kill.engaged:
            report.notes.append("the kill switch is engaged: nothing ran")
            return
        cfg = self.settings()
        sources = [*self._portals(), *cfg.sources]
        before = self._found()
        if sources:
            try:
                self._search(sources, cfg.recency)
                report.searched = sources
            except Exception as exc:  # noqa: BLE001 - one failed step must not hide the others
                log.warning("autopilot search failed: %s", exc)
                report.notes.append(f"search: {exc}")
        else:
            report.notes.append("no sources: enable a portal or add a careers page")
        report.new_jobs = max(0, self._found() - before)
        if self._found() and not self._review_open():
            try:
                self._start_review()
                report.review_started = True
            except Exception as exc:  # noqa: BLE001
                report.notes.append(f"review: {exc}")
        if cfg.check_mail and self._poll_mail is not None and self._has_mailbox():
            try:
                self._poll_mail()
                report.mail_checked = True
            except Exception as exc:  # noqa: BLE001
                report.notes.append(f"mail: {exc}")

    def _found(self) -> int:
        with session_scope(self._sessions) as s:
            return len(list(s.scalars(select(Job.id).where(Job.status == JobStatus.FOUND))))

    def _review_open(self) -> bool:
        with session_scope(self._sessions) as s:
            return (
                s.scalar(
                    select(WorkflowRun.thread_id).where(
                        WorkflowRun.kind == "apply", WorkflowRun.status.in_(["running", "waiting"])
                    )
                )
                is not None
            )

    def _record(self, report: RunReport) -> None:
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, LAST_KEY)
            data = asdict(report)
            if row is None:
                s.add(AppSetting(key=LAST_KEY, value=data))
            else:
                row.value = data
            log_action(
                s, ACTOR, "autopilot_run",
                payload={"trigger": report.trigger, "new_jobs": report.new_jobs,
                         "review_started": report.review_started, "notes": len(report.notes)},
            )  # fmt: skip
