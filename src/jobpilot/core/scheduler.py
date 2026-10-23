"""Background schedule (APScheduler).

- email poll every `email.poll_interval_minutes`;
- Autopilot at its chosen time and days (rescheduled when its settings change).

A tick is skipped while the kill switch is engaged; a failed tick is logged and retried next
time. Each job runs one at a time (max_instances=1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from jobpilot.core.killswitch import KillSwitch

log = logging.getLogger(__name__)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def build_scheduler(
    *,
    poll: Callable[[], object],
    has_mailbox: Callable[[], bool],
    kill_switch: KillSwitch,
    interval_minutes: int,
) -> BackgroundScheduler:
    def email_tick() -> None:
        if kill_switch.engaged or not has_mailbox():
            return
        try:
            report = poll()
            log.info("email poll: %s", report)
        except Exception:  # noqa: BLE001 - a tick must never kill the scheduler
            log.exception("email poll failed")

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        email_tick,
        "interval",
        minutes=interval_minutes,
        id="email_poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    return scheduler


def schedule_autopilot(scheduler: BackgroundScheduler, autopilot: Any) -> None:
    """(Re)place the Autopilot job to match its settings; removes it when disabled."""
    if scheduler.get_job("autopilot") is not None:
        scheduler.remove_job("autopilot")
    cfg = autopilot.settings()
    if not cfg.enabled or not cfg.days:
        return
    hour, minute = (int(x) for x in cfg.time.split(":"))

    def tick() -> None:
        try:
            autopilot.run("schedule")
        except Exception:  # noqa: BLE001
            log.exception("autopilot run failed")

    scheduler.add_job(
        tick,
        CronTrigger(
            day_of_week=",".join(DAYS[d] for d in sorted(set(cfg.days))),
            hour=hour,
            minute=minute,
            timezone=autopilot.timezone,
        ),
        id="autopilot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
