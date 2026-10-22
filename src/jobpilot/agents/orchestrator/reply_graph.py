"""The reply workflow for one email that needs action, as a LangGraph graph.

    triage (H6) ─┬─ draft reply ─→ draft ─→ review (H7) ─┬─ approve ──→ send → end
                 └─ ignore / done → end      ↑      ↑    ├─ edit ─────→ review (new version)
                                             │      └────┼─ regenerate → draft
                                             │           └─ discard ───→ end
                                             └── attachment changed after approval

There is no path from an edit to `send`: an edit is a new version that is shown again. Pausing
nodes only read and log; drafting and sending happen in their own nodes, and sending is
idempotent per approval, so a restart can never send twice."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobpilot.agents.reply.errors import ReplyError
from jobpilot.core.status import Checkpoint


class ReplyOps(Protocol):
    def triage_view(self, email_id: int) -> dict[str, Any]: ...
    def record_h6(self, email_id: int, action: str, instruction: str) -> None: ...
    def draft(self, email_id: int, instruction: str, draft_id: int | None) -> dict[str, Any]: ...
    def draft_shell(self, email_id: int) -> int: ...
    def version_view(self, draft_id: int, n: int | None) -> dict[str, Any] | None: ...
    def edit(self, draft_id: int, base: int | None, changes: dict[str, Any]) -> int: ...
    def approve(self, draft_id: int, n: int) -> int: ...
    def regenerate(self, draft_id: int, instruction: str) -> None: ...
    def discard(self, draft_id: int | None, email_id: int) -> None: ...
    def send(self, draft_id: int, n: int, approval_id: int) -> dict[str, str]: ...


class ReplyState(TypedDict, total=False):
    email_id: int
    instruction: str
    draft_id: int | None
    version: int | None
    error: str
    next: str  # draft | review | send | end
    approval_id: int
    outcome: str  # sent | send_unknown | ignored | done | discarded


EDITABLE = ("to", "cc", "subject", "body", "attachments")


def build_reply_graph(ops: ReplyOps, *, checkpointer: Any = None) -> Any:
    def triage(state: ReplyState) -> ReplyState:
        email_id = state["email_id"]
        answer = interrupt(
            {
                "checkpoint": Checkpoint.H6_EMAIL_ACTION.value,
                "email": ops.triage_view(email_id),
                "allowed": ["draft", "ignore", "done"],
            }
        )
        action = str(answer.get("action"))
        instruction = str(answer.get("instruction", "")).strip()
        ops.record_h6(email_id, action, instruction)
        if action != "draft":
            return {"outcome": "ignored" if action == "ignore" else "done", "next": "end"}
        return {"instruction": instruction, "draft_id": None, "version": None, "next": "draft"}

    def draft(state: ReplyState) -> ReplyState:
        out = ops.draft(state["email_id"], state.get("instruction", ""), state.get("draft_id"))
        return {
            "draft_id": out["draft_id"],
            "version": out.get("version", state.get("version")),
            "error": out.get("error", ""),
            "next": "review",
        }

    def review(state: ReplyState) -> ReplyState:
        draft_id, version = state.get("draft_id"), state.get("version")
        shown = ops.version_view(draft_id, version) if draft_id and version else None
        allowed = ["edit", "regenerate", "discard"]
        if shown is not None and shown["body"].strip():
            allowed.insert(0, "approve")
        answer = interrupt(
            {
                "checkpoint": Checkpoint.H7_REPLY_APPROVAL.value,
                "email": ops.triage_view(state["email_id"]),
                "draft": shown,
                "instruction": state.get("instruction", ""),
                "error": state.get("error", ""),
                "allowed": allowed,
            }
        )
        action = answer.get("action")
        try:
            if action == "approve" and shown is not None and draft_id is not None:
                # Approve exactly the version that was shown with this decision.
                approval_id = ops.approve(draft_id, shown["n"])
                return {
                    "approval_id": approval_id, "version": shown["n"], "error": "", "next": "send",
                }  # fmt: skip
            if action == "edit":
                changes = {k: answer[k] for k in EDITABLE if k in answer}
                if draft_id is None:  # drafting failed; the user writes the reply
                    draft_id = ops.draft_shell(state["email_id"])
                n = ops.edit(draft_id, version, changes)
                return {"draft_id": draft_id, "version": n, "error": "", "next": "review"}
        except ReplyError as exc:  # e.g. no valid recipient: ask again with the reason
            return {"draft_id": draft_id, "error": str(exc), "next": "review"}
        if action == "regenerate":
            instruction = str(answer.get("instruction") or state.get("instruction", "")).strip()
            if draft_id is not None:
                ops.regenerate(draft_id, instruction)
            return {"instruction": instruction, "next": "draft"}
        ops.discard(draft_id, state["email_id"])
        return {"outcome": "discarded", "next": "end"}

    def send(state: ReplyState) -> ReplyState:
        draft_id, version = state["draft_id"], state["version"]
        assert draft_id is not None and version is not None
        result = ops.send(draft_id, version, state["approval_id"])
        if result["outcome"] == "changed":
            return {"error": result["detail"], "next": "review"}
        return {"outcome": result["outcome"], "error": result.get("detail", ""), "next": "end"}

    def route(state: ReplyState) -> str:
        return END if state.get("next") == "end" else str(state["next"])

    g = StateGraph(ReplyState)
    g.add_node("triage", triage)
    g.add_node("draft", draft)
    g.add_node("review", review)
    g.add_node("send", send)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", route, ["draft", END])
    g.add_edge("draft", "review")
    g.add_conditional_edges("review", route, ["send", "review", "draft", END])
    g.add_conditional_edges("send", route, ["review", END])
    return g.compile(checkpointer=checkpointer)
