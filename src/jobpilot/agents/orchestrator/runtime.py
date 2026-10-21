"""Runs workflows in the background and exposes their pending decisions.

One worker thread: applications run strictly one at a time (spec), and Playwright's sync API
stays on a single thread per run. Decisions are answered by resuming the thread with
`Command(resume=...)`; the graph state, including pending decisions, lives in the LangGraph
SQLite checkpointer, so everything survives a restart (`recover()` continues interrupted runs)."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session, sessionmaker

from jobpilot.core.db import session_scope
from jobpilot.core.killswitch import KillSwitch, KillSwitchEngaged
from jobpilot.core.models import WorkflowRun

log = logging.getLogger(__name__)
GraphFactory = Callable[[Any], Any]  # checkpointer -> compiled graph
STALE = "that decision is no longer pending (refresh and try again)"


@dataclass(frozen=True)
class PendingDecision:
    thread_id: str
    kind: str
    interrupt_id: str
    payload: dict[str, Any]


class WorkflowError(RuntimeError):
    pass


class WorkflowRuntime:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        kill_switch: KillSwitch,
        checkpoint_path: Path,
        graphs: dict[str, GraphFactory],
    ) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self._saver = SqliteSaver(self._conn)
        self._graphs = {kind: factory(self._saver) for kind, factory in graphs.items()}
        self._sessions = sessions
        self._kill = kill_switch
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jobpilot-workflow")

    def _cfg(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 500}

    def _set(
        self,
        thread_id: str,
        status: str,
        error: str = "",
        summary: dict[str, Any] | None = None,
        kind: str | None = None,
    ) -> None:
        with session_scope(self._sessions) as s:
            run = s.get(WorkflowRun, thread_id)
            if run is None:
                run = WorkflowRun(thread_id=thread_id, kind=kind or "", status=status)
                s.add(run)
            run.status, run.error = status, error[:1000]
            if summary is not None:
                run.summary = summary

    # ---- driving ----------------------------------------------------------------------

    def _drive(self, kind: str, thread_id: str, payload: Any) -> None:
        graph = self._graphs[kind]
        try:
            self._kill.check()
            out = graph.invoke(payload, self._cfg(thread_id))
        except KillSwitchEngaged as exc:
            self._set(thread_id, "halted", f"kill switch: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - recorded on the run, never lost
            log.exception("workflow %s failed", thread_id)
            self._set(thread_id, "failed", f"{type(exc).__name__}: {exc}")
            return
        waiting = bool(out.get("__interrupt__")) if isinstance(out, dict) else False
        summary = {
            k: out.get(k)
            for k in ("results", "applied_count", "stopped_portals")
            if isinstance(out, dict) and k in out
        }
        self._set(thread_id, "waiting" if waiting else "done", summary=summary)

    def start(self, kind: str, initial: dict[str, Any] | None = None) -> tuple[str, Future[None]]:
        if kind not in self._graphs:
            raise WorkflowError(f"unknown workflow '{kind}'")
        self._kill.check()
        thread_id = uuid.uuid4().hex
        self._set(thread_id, "running", kind=kind)
        return thread_id, self._worker.submit(self._drive, kind, thread_id, initial or {})

    def resume(self, thread_id: str, interrupt_id: str, answer: dict[str, Any]) -> Future[None]:
        self._kill.check()
        with session_scope(self._sessions) as s:
            run = s.get(WorkflowRun, thread_id)
            if run is None:
                raise WorkflowError("no such workflow")
            kind = run.kind
        if interrupt_id not in self._interrupt_ids(kind, thread_id):
            raise WorkflowError(STALE)
        # Claim the run atomically: of two concurrent answers to the same decision (double
        # click, two tabs), exactly one moves it from waiting to running; the other is refused.
        with session_scope(self._sessions) as s:
            result = s.execute(
                update(WorkflowRun)
                .where(WorkflowRun.thread_id == thread_id, WorkflowRun.status == "waiting")
                .values(status="running", error="")
            )
            claimed = cast(CursorResult[Any], result).rowcount
        if not claimed:
            raise WorkflowError(STALE)
        return self._worker.submit(self._drive_resume, kind, thread_id, interrupt_id, answer)

    def _drive_resume(
        self, kind: str, thread_id: str, interrupt_id: str, answer: dict[str, Any]
    ) -> None:
        # Check again on the worker: the decision must still be the one the user answered.
        if interrupt_id not in self._interrupt_ids(kind, thread_id):
            log.warning(
                "decision %s on %s is no longer pending; not resuming", interrupt_id, thread_id
            )
            self._set(thread_id, "waiting")
            return
        # Bound to that interrupt id, so the answer can never land on a different question.
        self._drive(kind, thread_id, Command(resume={interrupt_id: answer}))

    def _interrupt_ids(self, kind: str, thread_id: str) -> set[str]:
        state = self._graphs[kind].get_state(self._cfg(thread_id))
        return {intr.id for task in state.tasks for intr in task.interrupts}

    def recover(self) -> list[str]:
        """After a restart: continue runs that were mid-step when the process stopped. Runs that
        were waiting stay waiting; their decisions are still pending in the checkpointer."""
        with session_scope(self._sessions) as s:
            stuck = [
                (r.thread_id, r.kind)
                for r in s.scalars(select(WorkflowRun).where(WorkflowRun.status == "running"))
            ]
        for thread_id, kind in stuck:
            if kind in self._graphs:
                self._worker.submit(self._drive, kind, thread_id, None)
        return [t for t, _ in stuck]

    # ---- reading ----------------------------------------------------------------------

    def pending(self) -> list[PendingDecision]:
        with session_scope(self._sessions) as s:
            waiting = [
                (r.thread_id, r.kind)
                for r in s.scalars(select(WorkflowRun).where(WorkflowRun.status == "waiting"))
            ]
        out: list[PendingDecision] = []
        for thread_id, kind in waiting:
            state = self._graphs[kind].get_state(self._cfg(thread_id))
            for task in state.tasks:
                for intr in task.interrupts:
                    out.append(PendingDecision(thread_id, kind, intr.id, dict(intr.value)))
        return out

    def runs(self, limit: int = 20) -> list[WorkflowRun]:
        with session_scope(self._sessions) as s:
            stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit)
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def shutdown(self, wait: bool = False) -> None:
        self._worker.shutdown(wait=wait, cancel_futures=True)
        self._conn.close()
