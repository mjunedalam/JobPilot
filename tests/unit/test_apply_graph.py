from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from jobpilot.agents.orchestrator.apply_graph import build_apply_graph
from jobpilot.core.status import Checkpoint


def job(job_id: int, portal: str = "naukri") -> dict[str, Any]:
    return {"job_id": job_id, "title": f"Job {job_id}", "portals": [portal], "match_score": 80}


class ScriptedService:
    """Attempt outcomes are scripted per job; every call is recorded."""

    def __init__(self, jobs: list[dict], script: dict[int, list[dict]]) -> None:
        self.jobs, self.script = jobs, {k: list(v) for k, v in script.items()}
        self.calls: list[tuple] = []

    def candidates(self, limit: int = 25) -> list[dict]:
        return self.jobs

    def record_h3(self, shown: list[int], approved: list[int]) -> dict[str, int]:
        self.calls.append(("h3", shown, approved))
        return {str(j): 100 + j for j in approved}

    def record_h4(self, job_id, values, labels, save) -> None:  # noqa: ANN001
        self.calls.append(("h4", job_id, values, save))

    def record_h5(self, job_id: int, answers: dict) -> int:
        self.calls.append(("h5", job_id, answers))
        return 500 + job_id

    def record_skip(self, job_id: int, checkpoint: Checkpoint, reason: str) -> None:
        self.calls.append(("skip", job_id, checkpoint.value))

    def run_attempt(self, job_id, attempt, overrides, approval_audit_id, preview) -> dict:  # noqa: ANN001
        self.calls.append(("attempt", job_id, attempt, dict(overrides), approval_audit_id))
        return self.script[job_id].pop(0)

    def mark_failed(self, job_id: int, reason: str, url: str) -> None:
        self.calls.append(("failed", job_id, reason))


def needs_input() -> dict:
    return {
        "outcome": "needs_input",
        "questions": [{"key": "why", "label": "Why us?"}],
        "answers": {},
        "answer_keys": {},
    }


def needs_preview(answers: dict | None = None) -> dict:
    return {
        "outcome": "needs_preview",
        "answers": answers or {"Name": "Asha"},
        "answer_keys": {"Name": "name"},
        "questions": [],
    }


def done(outcome: str = "submitted", **kw: str) -> dict:
    return {
        "outcome": outcome,
        "detail": kw.get("detail", ""),
        "portal": kw.get("portal", "naukri"),
        "url": "https://x",
    }


def graph(service: ScriptedService, db: Path, preview: bool = True):  # noqa: ANN201
    saver = SqliteSaver(sqlite3.connect(db, check_same_thread=False))
    return build_apply_graph(
        service, preview=preview, delay=(0, 0), sleep=lambda _: None, checkpointer=saver
    )


def pending(g, cfg) -> dict:  # noqa: ANN001
    [task] = [t for t in g.get_state(cfg).tasks if t.interrupts]
    return task.interrupts[0].value


CFG = {"configurable": {"thread_id": "run-1"}}


def test_full_run_h3_h4_h5(tmp_path: Path) -> None:
    svc = ScriptedService(
        [job(1), job(2)],
        {
            1: [needs_input(), needs_preview(), done()],
            2: [needs_preview({"Name": "Asha", "Why": "x"}), done()],
        },
    )
    g = graph(svc, tmp_path / "w.db")
    g.invoke({}, CFG)
    assert pending(g, CFG)["checkpoint"] == "H3"

    g.invoke(Command(resume={"action": "approve", "selection": "all"}), CFG)
    h4 = pending(g, CFG)
    assert h4["checkpoint"] == "H4" and h4["questions"][0]["key"] == "why"

    g.invoke(Command(resume={"action": "answer", "values": {"why": "Scale"}, "save": ["why"]}), CFG)
    assert pending(g, CFG)["checkpoint"] == "H5"
    g.invoke(Command(resume={"action": "submit"}), CFG)  # job 1 submitted, job 2 → preview
    h5 = pending(g, CFG)
    assert h5["job_id"] == 2
    final = g.invoke(Command(resume={"action": "submit"}), CFG)

    assert final["results"] == {
        "1": {"status": "submitted", "detail": ""},
        "2": {"status": "submitted", "detail": ""},
    }
    assert final["applied_count"] == 2
    attempts = [c for c in svc.calls if c[0] == "attempt"]
    # the H4 answer reaches the next attempt; the H5 token is the one recorded for that job
    assert attempts[1][3] == {"why": "Scale"} and attempts[2][4] == 501
    assert ("h4", 1, {"why": "Scale"}, ["why"]) in svc.calls


def test_pending_decision_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "w.db"
    svc = ScriptedService([job(1)], {1: [needs_input(), needs_preview(), done()]})
    g = graph(svc, db)
    g.invoke({}, CFG)
    g.invoke(Command(resume={"action": "approve", "selection": "1"}), CFG)
    assert pending(g, CFG)["checkpoint"] == "H4"

    restarted = graph(svc, db)  # new process: new graph, same checkpoint file
    assert pending(restarted, CFG)["checkpoint"] == "H4"
    restarted.invoke(Command(resume={"action": "answer", "values": {"why": "x"}}), CFG)
    assert pending(restarted, CFG)["checkpoint"] == "H5"
    # nothing before the pause re-ran: exactly one attempt before the answer, one after
    assert [c[2] for c in svc.calls if c[0] == "attempt"] == [1, 1]


def test_retry_once_then_failed(tmp_path: Path) -> None:
    svc = ScriptedService(
        [job(1)], {1: [done("failed", detail="timeout"), done("failed", detail="timeout")]}
    )
    g = graph(svc, tmp_path / "w.db")
    g.invoke({}, CFG)
    final = g.invoke(Command(resume={"action": "approve", "selection": "1"}), CFG)
    assert [c[2] for c in svc.calls if c[0] == "attempt"] == [1, 2]
    assert ("failed", 1, "timeout") in svc.calls and final["results"]["1"]["status"] == "failed"


def test_blocked_portal_stops_only_that_portal(tmp_path: Path) -> None:
    svc = ScriptedService(
        [job(1, "naukri"), job(2, "naukri"), job(3, "shine")],
        {1: [done("blocked", detail="captcha", portal="naukri")], 3: [needs_preview(), done()]},
    )
    g = graph(svc, tmp_path / "w.db")
    g.invoke({}, CFG)
    g.invoke(Command(resume={"action": "approve", "selection": "all"}), CFG)
    final = g.invoke(Command(resume={"action": "submit"}), CFG)
    assert final["results"]["1"]["status"] == "blocked"
    assert final["results"]["2"]["status"] == "skipped"  # same portal: not attempted
    assert final["results"]["3"]["status"] == "submitted"  # other portals continue
    assert not any(c[0] == "attempt" and c[1] == 2 for c in svc.calls)


def test_edit_at_h5_shows_preview_again_and_skip(tmp_path: Path) -> None:
    svc = ScriptedService(
        [job(1), job(2)],
        {1: [needs_preview(), needs_preview({"Name": "A. Rao"})], 2: [needs_input()]},
    )
    g = graph(svc, tmp_path / "w.db")
    g.invoke({}, CFG)
    g.invoke(Command(resume={"action": "approve", "selection": "1,2"}), CFG)
    g.invoke(Command(resume={"action": "edit", "values": {"name": "A. Rao"}}), CFG)
    again = pending(g, CFG)
    assert again["checkpoint"] == "H5" and again["answers"] == {"Name": "A. Rao"}
    g.invoke(Command(resume={"action": "skip"}), CFG)
    assert pending(g, CFG)["checkpoint"] == "H4"
    final = g.invoke(Command(resume={"action": "skip"}), CFG)
    assert (
        final["results"]["1"]["status"] == "skipped"
        and final["results"]["2"]["status"] == "skipped"
    )
    assert ("skip", 1, "H5") in svc.calls and ("skip", 2, "H4") in svc.calls


def test_reject_all_and_bad_selection(tmp_path: Path) -> None:
    svc = ScriptedService([job(1)], {})
    g = graph(svc, tmp_path / "w.db")
    g.invoke({}, CFG)
    g.invoke(Command(resume={"action": "approve", "selection": "7"}), CFG)
    retry = pending(g, CFG)  # asked again, with the reason, instead of failing the run
    assert retry["checkpoint"] == "H3" and "not in the list" in retry["error"]
    final = g.invoke(Command(resume={"action": "reject"}), CFG)
    assert final["queue"] == [] and ("h3", [1], []) in svc.calls


def test_no_candidates_ends_immediately(tmp_path: Path) -> None:
    final = graph(ScriptedService([], {}), tmp_path / "w.db").invoke({}, CFG)
    assert final["results"] == {}


def test_unconfirmed_submit_is_not_retried(tmp_path: Path) -> None:
    svc = ScriptedService([job(1)], {1: [done("submitted_unconfirmed", detail="no confirmation")]})
    g = graph(svc, tmp_path / "w.db", preview=False)
    g.invoke({}, CFG)
    final = g.invoke(Command(resume={"action": "approve", "selection": "1"}), CFG)
    assert [c[2] for c in svc.calls if c[0] == "attempt"] == [1]
    assert final["results"]["1"]["status"] == "submitted_unconfirmed"
    assert final["applied_count"] == 0
