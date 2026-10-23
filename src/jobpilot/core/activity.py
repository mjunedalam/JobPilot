"""One plain-language line per audit entry, for the live feed. Uses only safe fields (actions,
checkpoints, ids, categories and counts), never message bodies or answers."""

from __future__ import annotations

from typing import Any

AGENT_NAMES = {
    "profile_agent": "Profile",
    "search_agent": "Job Search",
    "matching_agent": "Matching & Ranking",
    "application_agent": "Application",
    "application_service": "Application",
    "email_monitor": "Email Monitor",
    "reply_drafting": "Reply Drafting",
    "reply_service": "Reply Drafting",
    "portal_connector": "Portal Connector & Auth",
    "mail_service": "Mailbox",
    "orchestrator": "Orchestrator",
    "user": "You",
}

DECISIONS = {
    ("H3", "approve"): "You approved job #{id}",
    ("H3", "reject"): "You passed on job #{id}",
    ("H4", "answer"): "You answered a question for job #{id}",
    ("H4", "skip"): "You skipped job #{id} at a question",
    ("H5", "submit"): "You approved the filled application for job #{id}",
    ("H5", "skip"): "You skipped job #{id} at the preview",
    ("H6", "draft"): "You asked for a reply to email #{id}",
    ("H6", "ignore"): "You set email #{id} aside",
    ("H6", "done"): "You marked email #{id} done",
    ("H6", "apply_status"): "You applied the status email #{id} suggested",
    ("H7", "approve_and_send"): "You approved a reply for sending",
    ("H7", "edit"): "You edited a reply draft",
    ("H7", "regenerate"): "You asked for a new reply draft",
    ("H7", "discard"): "You discarded a reply draft",
}

ACTIONS = {
    "jobs_scored": lambda p: f"Ranked {p.get('count', 0)} jobs · {p.get('flagged', 0)} flagged",
    "email_stored": lambda p: f"New job email: {str(p.get('category', 'other')).replace('_', ' ')}",
    "attempt:submitted": lambda p: "Application submitted and confirmed",
    "attempt:submitted_unconfirmed": lambda p: "Submitted, but no confirmation: check it",
    "attempt:needs_input": lambda p: "A form question needs you",
    "attempt:needs_preview": lambda p: "Filled form ready for your preview",
    "attempt:blocked": lambda p: "A portal asked for a manual check",
    "attempt:failed": lambda p: "An application attempt failed",
    "reply:sent": lambda p: "Reply sent",
    "reply:refused": lambda p: "The mailbox refused to send a reply",
    "reply:send_unknown": lambda p: "A reply may not have been sent: check",
    "mail_connected": lambda p: "Mailbox connected",
    "mail_disconnected": lambda p: "Mailbox disconnected",
    "otp_lookup": lambda p: (
        "Looked up a sign-in code" + ("" if p.get("found") else " (none found)")
    ),
    "profile_version_created": lambda p: "Profile updated",
    "resume_added": lambda p: "Resume saved",
    "autopilot_run": lambda p: (
        f"Autopilot ran · {p.get('new_jobs', 0)} new jobs"
        + (" · shortlist waiting for you" if p.get("review_started") else "")
    ),
    "autopilot_settings": lambda p: (
        f"Autopilot {'on' if p.get('enabled') else 'off'} · {p.get('time', '')}"
    ),
}


def describe(actor: str, action: str, checkpoint: str | None, entity_id: str,
             payload: dict[str, Any] | None = None) -> str:  # fmt: skip
    payload = payload or {}
    if action.startswith("decision:") and checkpoint:
        template = DECISIONS.get((checkpoint, action.split(":", 1)[1]))
        if template:
            return template.format(id=entity_id or "?")
        return f"You decided at {checkpoint}"
    if (fn := ACTIONS.get(action)) is not None:
        return fn(payload)
    who = AGENT_NAMES.get(actor, actor.replace("_", " ").title())
    if action == "run_completed":
        task = str(payload.get("task", "")).replace("_", " ")
        return f"{who} finished {task}".strip()
    if action == "run_failed":
        return f"{who} stopped: {str(payload.get('task', '')).replace('_', ' ')} failed"
    if action.startswith("tool:"):
        return f"{who} used {action.split(':', 1)[1]}"
    return f"{who}: {action.replace('_', ' ')}"
