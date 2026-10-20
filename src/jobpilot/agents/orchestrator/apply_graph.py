"""The apply workflow as a LangGraph graph with human-in-the-loop interrupts.

    review (H3) → next → attempt ─┬─ needs input ──→ ask_field (H4) ──→ attempt
                    ↑             ├─ needs preview → ask_preview (H5) → attempt
                    │             ├─ failed (1st) ──→ attempt (retry once)
                    └─────────────┴─ done / failed / blocked / skipped

Nodes that pause (`review`, `ask_field`, `ask_preview`) only call `interrupt()` and log the
answer; LangGraph re-runs an interrupted node from the top on resume, so they never touch the
browser. State is JSON and checkpointed, so pending decisions survive restarts."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobpilot.agents.application.selection import SelectionError, parse_selection
from jobpilot.core.status import Checkpoint

MAX_ATTEMPTS = 2  # spec: retry once, then "Failed – needs manual action"


class ApplyService(Protocol):
    def candidates(self, limit: int = 25) -> list[dict[str, Any]]: ...
    def record_h3(self, shown: list[int], approved: list[int]) -> dict[str, int]: ...
    def record_h4(
        self, job_id: int, values: dict[str, str], labels: dict[str, str], save: list[str]
    ) -> None: ...
    def record_h5(self, job_id: int, answers: dict[str, str]) -> int: ...
    def record_skip(self, job_id: int, checkpoint: Checkpoint, reason: str) -> None: ...
    def run_attempt(
        self,
        job_id: int,
        attempt: int,
        overrides: dict[str, str],
        approval_audit_id: int | None,
        preview: bool,
    ) -> dict[str, Any]: ...
    def mark_failed(self, job_id: int, reason: str, url: str) -> None: ...


class ApplyState(TypedDict, total=False):
    jobs: list[dict[str, Any]]  # the H3 table
    h3_tokens: dict[str, int]  # job_id -> audit id of its approval
    h5_tokens: dict[str, int]
    queue: list[int]
    current: int | None
    attempt: int
    overrides: dict[str, dict[str, str]]  # job_id -> field key -> value (H4 / H5 edits)
    last: dict[str, Any]  # the latest attempt summary
    stopped_portals: list[str]
    results: dict[str, dict[str, str]]  # job_id -> {status, detail}
    preview: bool
    applied_count: int


def build_apply_graph(
    service: ApplyService,
    *,
    preview: bool,
    delay: tuple[float, float],
    sleep: Callable[[float], None] = time.sleep,
    checkpointer: Any = None,
) -> Any:
    def review(state: ApplyState) -> ApplyState:
        jobs = state.get("jobs") or service.candidates()
        if not jobs:
            return {"jobs": [], "queue": [], "results": {}}
        shown = [j["job_id"] for j in jobs]
        picked: list[int] = []
        error = ""
        while True:  # ask again on an invalid selection instead of failing the run
            answer = interrupt(
                {
                    "checkpoint": Checkpoint.H3_JOB_APPROVAL.value,
                    "jobs": jobs,
                    "allowed": ["approve", "reject"],
                    "error": error,
                }
            )
            if answer.get("action") != "approve":
                break
            try:
                positions = parse_selection(str(answer.get("selection", "all")), len(jobs))
            except SelectionError as exc:
                error = str(exc)
                continue
            picked = [jobs[p - 1]["job_id"] for p in positions]
            break
        tokens = service.record_h3(shown, picked)
        return {
            "jobs": jobs,
            "h3_tokens": tokens,
            "queue": [j for j in picked if str(j) in tokens],
            "results": {},
            "overrides": {},
            "h5_tokens": {},
            "stopped_portals": [],
            "preview": preview,
            "applied_count": 0,
        }

    def next_job(state: ApplyState) -> ApplyState:
        queue = list(state.get("queue", []))
        stopped = set(state.get("stopped_portals", []))
        while queue:
            job_id = queue.pop(0)
            portal = next(
                (j["portals"][0] for j in state["jobs"] if j["job_id"] == job_id and j["portals"]),
                "",
            )
            if portal in stopped:
                results = dict(state.get("results", {}))
                results[str(job_id)] = {"status": "skipped", "detail": f"{portal} stopped this run"}
                state = {**state, "results": results}
                continue
            if state.get("current") is not None:
                sleep(random.uniform(*delay))  # human-paced gap between applications
            return {
                "queue": queue,
                "current": job_id,
                "attempt": 1,
                "results": state.get("results", {}),
            }
        return {"queue": [], "current": None, "results": state.get("results", {})}

    def attempt(state: ApplyState) -> ApplyState:
        job_id = state["current"]
        assert job_id is not None
        key = str(job_id)
        token = (
            state.get("h5_tokens", {}).get(key) if preview else state.get("h3_tokens", {}).get(key)
        )
        last = service.run_attempt(
            job_id, state.get("attempt", 1), state.get("overrides", {}).get(key, {}), token, preview
        )
        return {"last": last}

    def after_attempt(state: ApplyState) -> str:
        outcome = state["last"]["outcome"]
        if outcome == "needs_input":
            return "ask_field"
        if outcome == "needs_preview":
            return "ask_preview"
        if outcome == "failed" and state.get("attempt", 1) < MAX_ATTEMPTS:
            return "retry"
        return "record"

    def retry(state: ApplyState) -> ApplyState:
        return {"attempt": state.get("attempt", 1) + 1}

    def record(state: ApplyState) -> ApplyState:
        job_id, last = _current(state), state["last"]
        results = dict(state.get("results", {}))
        stopped = list(state.get("stopped_portals", []))
        outcome = last["outcome"]
        if outcome == "failed":
            service.mark_failed(job_id, last.get("detail", ""), last.get("url", ""))
        if outcome in ("blocked", "cap_reached") and last.get("portal") not in stopped:
            stopped.append(last["portal"])  # this portal stops; others continue
        results[str(job_id)] = {"status": outcome, "detail": last.get("detail", "")}
        applied = state.get("applied_count", 0) + (outcome == "submitted")
        return {"results": results, "stopped_portals": stopped, "applied_count": applied}

    def ask_field(state: ApplyState) -> ApplyState:
        job_id, last = _current(state), state["last"]
        answer = interrupt(
            {
                "checkpoint": Checkpoint.H4_UNKNOWN_FIELD.value,
                "job_id": job_id,
                "job": _job(state, job_id),
                "questions": last["questions"],
                "allowed": ["answer", "skip"],
            }
        )
        key = str(job_id)
        if answer.get("action") == "skip":
            service.record_skip(job_id, Checkpoint.H4_UNKNOWN_FIELD, "skipped at H4")
            results = {
                **state.get("results", {}),
                key: {"status": "skipped", "detail": "you skipped it"},
            }
            return {"results": results, "last": {"outcome": "skipped"}}
        values = {k: str(v) for k, v in dict(answer.get("values", {})).items()}
        labels = {q["key"]: q["label"] for q in last["questions"]}
        service.record_h4(job_id, values, labels, list(answer.get("save", [])))
        overrides = {**state.get("overrides", {})}
        overrides[key] = {**overrides.get(key, {}), **values}
        return {"overrides": overrides, "last": {"outcome": "answered"}}

    def ask_preview(state: ApplyState) -> ApplyState:
        job_id, last = _current(state), state["last"]
        answer = interrupt(
            {
                "checkpoint": Checkpoint.H5_PRESUBMIT.value,
                "job_id": job_id,
                "job": _job(state, job_id),
                "answers": last["answers"],
                "keys": last["answer_keys"],
                "allowed": ["submit", "edit", "skip"],
            }
        )
        key = str(job_id)
        action = answer.get("action")
        if action == "skip":
            service.record_skip(job_id, Checkpoint.H5_PRESUBMIT, "skipped at H5")
            results = {
                **state.get("results", {}),
                key: {"status": "skipped", "detail": "you skipped it"},
            }
            return {"results": results, "last": {"outcome": "skipped"}}
        if action == "edit":
            overrides = {**state.get("overrides", {})}
            overrides[key] = {
                **overrides.get(key, {}),
                **{k: str(v) for k, v in dict(answer.get("values", {})).items()},
            }
            return {"overrides": overrides, "last": {"outcome": "edited"}}  # preview again
        audit_id = service.record_h5(job_id, last["answers"])  # exactly what was shown
        return {
            "h5_tokens": {**state.get("h5_tokens", {}), key: audit_id},
            "last": {"outcome": "approved"},
        }

    def after_question(state: ApplyState) -> str:
        return "next" if state["last"]["outcome"] == "skipped" else "attempt"

    g = StateGraph(ApplyState)
    g.add_node("review", review)
    g.add_node("next", next_job)
    g.add_node("attempt", attempt)
    g.add_node("retry", retry)
    g.add_node("record", record)
    g.add_node("ask_field", ask_field)
    g.add_node("ask_preview", ask_preview)
    g.add_edge(START, "review")
    g.add_edge("review", "next")
    g.add_conditional_edges(
        "next", lambda s: "attempt" if s.get("current") is not None else END, ["attempt", END]
    )
    g.add_conditional_edges(
        "attempt", after_attempt, ["ask_field", "ask_preview", "retry", "record"]
    )
    g.add_edge("retry", "attempt")
    g.add_edge("record", "next")
    g.add_conditional_edges("ask_field", after_question, ["attempt", "next"])
    g.add_conditional_edges("ask_preview", after_question, ["attempt", "next"])
    return g.compile(checkpointer=checkpointer)


def _current(state: ApplyState) -> int:
    job_id = state.get("current")
    if job_id is None:
        raise RuntimeError("no job is in progress")
    return job_id


def _job(state: ApplyState, job_id: int | None) -> dict[str, Any]:
    return next((j for j in state.get("jobs", []) if j["job_id"] == job_id), {})
