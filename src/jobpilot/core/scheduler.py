"""Background schedule (APScheduler): the email poll every `email.poll_interval_minutes`.

A tick is skipped while the kill switch is engaged or no mailbox is connected; a failed poll is
logged and retried at the next tick. Only one poll runs at a time."""

from __future__ import annotations

import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from jobpilot.core.killswitch import KillSwitch

log = logging.getLogger(__name__)


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
