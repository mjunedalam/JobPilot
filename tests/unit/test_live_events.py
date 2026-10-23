from __future__ import annotations

import asyncio

from jobpilot.core.activity import describe
from jobpilot.core.audit import log_action, log_decision
from jobpilot.core.events import EventBus, bus
from jobpilot.core.status import Checkpoint


def test_bus_delivers_across_threads_and_replays() -> None:
    local = EventBus(buffer=3)

    async def scenario() -> list[int]:
        sub = local.subscribe()
        import threading

        threading.Thread(target=lambda: [local.publish("tool", {"n": i}) for i in range(5)]).start()
        got = [(await asyncio.wait_for(sub.queue.get(), 2)).data["n"] for _ in range(5)]
        local.unsubscribe(sub)
        return got

    assert asyncio.run(scenario()) == [0, 1, 2, 3, 4]
    assert [e.data["n"] for e in local.since(0)] == [2, 3, 4]  # ring buffer keeps the last 3
    assert [e.data["n"] for e in local.since(local.since(0)[1].id)] == [4]
    assert local.subscribers == 0


def test_audit_entries_are_published_without_content(session) -> None:  # noqa: ANN001
    before = bus.since(0)[-1].id if bus.since(0) else 0
    log_decision(
        session,
        Checkpoint.H7_REPLY_APPROVAL,
        "approve_and_send",
        approved_content={"body": "secret reply text"},
        entity_type="draft",
        entity_id=4,
    )
    log_action(session, "matching_agent", "jobs_scored", payload={"count": 9, "flagged": 1})
    events = [e for e in bus.since(before) if e.kind == "audit"]
    assert [e.data["text"] for e in events] == [
        "You approved a reply for sending",
        "Ranked 9 jobs · 1 flagged",
    ]
    assert "secret reply text" not in str([e.data for e in events])


def test_describe_falls_back_sensibly() -> None:
    assert (
        describe("search_agent", "run_completed", None, "", {"task": "search"})
        == "Job Search finished a job search"
    )
    assert (
        describe("email_monitor", "email_stored", None, "3", {"category": "interview_invite"})
        == "New job email: interview invite"
    )
    assert describe("something_new", "did_thing", None, "") == "Something New: did thing"
