from __future__ import annotations

from datetime import UTC, datetime

from jobpilot.agents.orchestrator.autopilot import Autopilot, AutopilotSettings
from jobpilot.core.db import session_scope
from jobpilot.core.models import Job, WorkflowRun


def make(sessions, kill_switch, **kw):  # noqa: ANN001, ANN003, ANN201
    calls: dict[str, list] = {"search": [], "review": [], "mail": []}

    def search(sources, recency):  # noqa: ANN001, ANN202
        calls["search"].append((sources, recency))
        with session_scope(sessions) as s:
            s.add(Job(dedup_key=f"k{len(calls['search'])}", title="Engineer", company="Acme"))
        return {}

    ap = Autopilot(
        sessions=sessions, kill_switch=kill_switch, timezone="Asia/Kolkata",
        enabled_portals=lambda: ["naukri"], search=search,
        start_review=lambda: calls["review"].append(1) or "t1",
        poll_mail=lambda: calls["mail"].append(1), has_mailbox=lambda: True,
        clock=kw.get("clock", lambda: datetime(2026, 10, 23, 2, 0, tzinfo=UTC)),  # Fri 07:30 IST
    )  # fmt: skip
    return ap, calls


def test_run_searches_starts_one_review_and_checks_mail(sessions, kill_switch) -> None:  # noqa: ANN001
    ap, calls = make(sessions, kill_switch)
    ap.save(AutopilotSettings(enabled=True, sources=["https://careers.acme.com"], recency="7d"))
    report = ap.run("manual")
    assert calls["search"] == [(["naukri", "https://careers.acme.com"], "7d")]
    assert report.new_jobs == 1 and report.review_started and report.mail_checked
    assert ap.last_run()["trigger"] == "manual"
    # A review is already waiting: the next run does not start another one.
    with session_scope(sessions) as s:
        s.add(WorkflowRun(thread_id="t1", kind="apply", status="waiting"))
    assert ap.run().review_started is False and len(calls["review"]) == 1


def test_kill_switch_stops_the_run(sessions, kill_switch) -> None:  # noqa: ANN001
    ap, calls = make(sessions, kill_switch)
    kill_switch.engage("test")
    report = ap.run()
    assert calls["search"] == [] and "kill switch" in report.notes[0]


def test_next_run_honours_time_days_and_timezone(sessions, kill_switch) -> None:  # noqa: ANN001
    ap, _ = make(sessions, kill_switch)
    assert ap.next_run() is None  # off by default
    ap.save(AutopilotSettings(enabled=True, time="09:00", days=[0, 1, 2, 3, 4]))
    nxt = ap.next_run()
    assert nxt is not None and (nxt.isoformat()[:16], nxt.tzname()) == ("2026-10-23T09:00", "IST")
    ap.save(AutopilotSettings(enabled=True, time="06:00", days=[0]))
    assert ap.next_run().strftime("%a %H:%M") == "Mon 06:00"  # Friday 06:00 has passed
