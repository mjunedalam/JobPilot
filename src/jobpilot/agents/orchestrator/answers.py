"""Validation of a human answer to a pending decision, before the workflow is resumed.

Shared by every way of answering (the API, Telegram buttons), so a malformed or out-of-range
answer is refused the same way wherever it comes from."""

from __future__ import annotations

from typing import Any

from jobpilot.agents.application.selection import SelectionError, parse_selection
from jobpilot.agents.orchestrator.reply_graph import EDITABLE
from jobpilot.agents.orchestrator.runtime import PendingDecision
from jobpilot.mail.base import split_addresses


class InvalidAnswer(ValueError):
    pass


def validate_answer(decision: PendingDecision, answer: dict[str, Any]) -> None:
    """Reject malformed answers here, before the workflow is resumed."""
    allowed = decision.payload.get("allowed", [])
    action = answer.get("action")
    if action not in allowed:
        raise InvalidAnswer(f"action must be one of {allowed}")
    checkpoint = decision.payload.get("checkpoint")
    if checkpoint == "H3" and action == "approve":
        try:
            parse_selection(str(answer.get("selection", "all")), len(decision.payload["jobs"]))
        except SelectionError as exc:
            raise InvalidAnswer(str(exc)) from exc
    if checkpoint == "H4" and action == "answer":
        keys = {q["key"] for q in decision.payload["questions"]}
        values = answer.get("values")
        if not isinstance(values, dict) or set(values) - keys:
            raise InvalidAnswer("values must answer the listed fields")
        missing = [k for k in keys if not str(values.get(k, "")).strip()]
        if missing:
            raise InvalidAnswer(f"answer every field: {missing}")
    if checkpoint == "H6" and action == "draft":
        check_text(answer.get("instruction", ""), "instruction", 1000, required=False)
    if checkpoint == "H7":
        validate_h7(decision, action, answer)
    if checkpoint == "H5" and action == "edit":
        keys = set(decision.payload.get("keys", {}).values())
        if not isinstance(answer.get("values"), dict) or set(answer["values"]) - keys:
            raise InvalidAnswer("edit only fields shown in the preview")


def check_text(value: Any, name: str, limit: int, *, required: bool) -> None:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        need = "non-empty " if required else ""
        raise InvalidAnswer(f"{name} must be {need}text up to {limit}")


def check_addresses(value: Any, name: str, *, required: bool) -> None:
    ok_list = isinstance(value, list) and all(isinstance(v, str) for v in value)
    if not ok_list or len(value) > 10 or len(split_addresses(value)) != len(value):
        raise InvalidAnswer(f"{name} must be a list of addresses")
    if required and not value:
        raise InvalidAnswer(f"{name} needs at least one address")


def validate_h7(decision: PendingDecision, action: Any, answer: dict[str, Any]) -> None:
    if action == "regenerate":
        check_text(answer.get("instruction", ""), "instruction", 1000, required=False)
    if action != "edit":
        return
    unknown = set(answer) - {"action", *EDITABLE}
    if unknown:
        raise InvalidAnswer(f"cannot edit {sorted(unknown)}")
    if decision.payload.get("draft") is None and "body" not in answer:
        raise InvalidAnswer("write the reply text")
    if "to" in answer:
        check_addresses(answer["to"], "to", required=True)
    if "cc" in answer:
        check_addresses(answer["cc"], "cc", required=False)
    if "subject" in answer:
        check_text(answer["subject"], "subject", 300, required=True)
    if "body" in answer:
        check_text(answer["body"], "body", 10_000, required=True)
    if "attachments" in answer:
        items = answer["attachments"]
        if (
            not isinstance(items, list)
            or len(items) > 5
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in items)
        ):
            raise InvalidAnswer("attachments must be resume ids")
