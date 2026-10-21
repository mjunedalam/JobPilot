from __future__ import annotations

from pathlib import Path

import pytest

from jobpilot.agents.orchestrator.apply_graph import build_apply_graph
from jobpilot.agents.orchestrator.runtime import WorkflowError, WorkflowRuntime
from jobpilot.core.killswitch import KillSwitchEngaged
from tests.unit.test_apply_graph import ScriptedService, done, job, needs_preview


def runtime(sessions, kill_switch, tmp_path: Path, svc: ScriptedService) -> WorkflowRuntime:  # noqa: ANN001
    graphs = {
        "apply": lambda saver: build_apply_graph(
            svc, preview=True, delay=(0, 0), sleep=lambda _: None, checkpointer=saver
        )
    }
    return WorkflowRuntime(
        sessions=sessions,
        kill_switch=kill_switch,
        checkpoint_path=tmp_path / "workflows.db",
        graphs=graphs,
    )


def test_start_wait_resume_done(sessions, kill_switch, tmp_path) -> None:
    svc = ScriptedService([job(1)], {1: [needs_preview(), done()]})
    rt = runtime(sessions, kill_switch, tmp_path, svc)
    thread, fut = rt.start("apply")
    fut.result(timeout=10)
    [h3] = rt.pending()
    assert h3.thread_id == thread and h3.payload["checkpoint"] == "H3"
    assert rt.runs()[0].status == "waiting"

    rt.resume(thread, h3.interrupt_id, {"action": "approve", "selection": "1"}).result(timeout=10)
    [h5] = rt.pending()
    assert h5.payload["checkpoint"] == "H5"
    with pytest.raises(WorkflowError, match="no longer pending"):
        rt.resume(thread, h3.interrupt_id, {"action": "submit"})  # stale UI must not answer
    rt.resume(thread, h5.interrupt_id, {"action": "submit"}).result(timeout=10)
    run = rt.runs()[0]
    assert run.status == "done" and run.summary["applied_count"] == 1 and rt.pending() == []


def test_decisions_survive_restart(sessions, kill_switch, tmp_path) -> None:
    svc = ScriptedService([job(1)], {1: [needs_preview(), done()]})
    thread, fut = runtime(sessions, kill_switch, tmp_path, svc).start("apply")
    fut.result(timeout=10)
    restarted = runtime(sessions, kill_switch, tmp_path, svc)  # new process
    [h3] = restarted.pending()
    assert h3.thread_id == thread
    restarted.resume(thread, h3.interrupt_id, {"action": "approve", "selection": "1"}).result(
        timeout=10
    )
    assert restarted.pending()[0].payload["checkpoint"] == "H5"


def test_kill_switch_halts(sessions, kill_switch, tmp_path) -> None:
    svc = ScriptedService([job(1)], {1: [needs_preview()]})
    rt = runtime(sessions, kill_switch, tmp_path, svc)
    thread, fut = rt.start("apply")
    fut.result(timeout=10)
    [h3] = rt.pending()
    kill_switch.engage("testing")
    with pytest.raises(KillSwitchEngaged):
        rt.resume(thread, h3.interrupt_id, {"action": "approve", "selection": "1"})
    with pytest.raises(KillSwitchEngaged):
        rt.start("apply")


def test_crash_is_recorded(sessions, kill_switch, tmp_path) -> None:
    class Broken(ScriptedService):
        def candidates(self, limit: int = 25) -> list[dict]:
            raise RuntimeError("database exploded")

    rt = runtime(sessions, kill_switch, tmp_path, Broken([], {}))
    _, fut = rt.start("apply")
    fut.result(timeout=10)
    run = rt.runs()[0]
    assert run.status == "failed" and "database exploded" in run.error


def test_unknown_workflow_and_thread(sessions, kill_switch, tmp_path) -> None:
    rt = runtime(sessions, kill_switch, tmp_path, ScriptedService([], {}))
    with pytest.raises(WorkflowError):
        rt.start("nope")
    with pytest.raises(WorkflowError):
        rt.resume("missing", "x", {})


def test_concurrent_answers_to_one_decision_resume_once(sessions, kill_switch, tmp_path) -> None:
    import threading

    svc = ScriptedService([job(1)], {1: [needs_preview(), done()]})
    rt = runtime(sessions, kill_switch, tmp_path, svc)
    thread, fut = rt.start("apply")
    fut.result(timeout=10)
    [h3] = rt.pending()
    # Hold both requests right after their "is it still pending?" check, so both pass it
    # before either claims the run: the window a double click or a second tab could hit.
    barrier, checked = threading.Barrier(2, timeout=5), rt._interrupt_ids
    gated = iter([True, True])

    def check_then_wait(kind: str, thread_id: str) -> set[str]:
        ids = checked(kind, thread_id)
        if next(gated, False):
            barrier.wait()
        return ids

    rt._interrupt_ids = check_then_wait  # type: ignore[method-assign]
    futures, errors = [], []

    def answer() -> None:
        try:
            futures.append(rt.resume(thread, h3.interrupt_id, {"action": "approve"}))
        except WorkflowError as exc:
            errors.append(exc)

    workers = [threading.Thread(target=answer) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)
    for f in futures:
        f.result(timeout=10)
    assert len(futures) == 1 and len(errors) == 1
    assert [c for c in svc.calls if c[0] == "h3"] == [("h3", [1], [1])]


def test_resume_rechecks_the_decision_on_the_worker(sessions, kill_switch, tmp_path) -> None:
    svc = ScriptedService([job(1)], {1: [needs_preview(), done()]})
    rt = runtime(sessions, kill_switch, tmp_path, svc)
    thread, fut = rt.start("apply")
    fut.result(timeout=10)
    rt._drive_resume("apply", thread, "not-the-pending-id", {"action": "approve"})
    assert rt.runs()[0].status == "waiting" and not [c for c in svc.calls if c[0] == "h3"]
