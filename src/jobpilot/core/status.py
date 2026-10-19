"""Application status lifecycle and HITL checkpoint identifiers."""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    FOUND = "found"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"
    VIEWED = "viewed"
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


S = JobStatus
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    S.FOUND: frozenset({S.APPROVED, S.SKIPPED}),
    S.APPROVED: frozenset({S.APPLYING, S.SKIPPED}),
    S.APPLYING: frozenset({S.APPLIED, S.FAILED, S.SKIPPED}),
    S.APPLIED: frozenset({S.VIEWED, S.SHORTLISTED, S.INTERVIEW, S.REJECTED, S.OFFER}),
    S.VIEWED: frozenset({S.SHORTLISTED, S.INTERVIEW, S.REJECTED}),
    S.SHORTLISTED: frozenset({S.INTERVIEW, S.REJECTED}),
    S.INTERVIEW: frozenset({S.OFFER, S.REJECTED}),
    S.FAILED: frozenset(),
    S.SKIPPED: frozenset(),
    S.OFFER: frozenset(),
    S.REJECTED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def check_transition(current: JobStatus, target: JobStatus) -> JobStatus:
    """Return target if the move is allowed, else raise InvalidTransition."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot move from {current} to {target}")
    return target


class Checkpoint(StrEnum):
    H1_PORTAL_LOGIN = "H1"
    H2_RECENCY = "H2"
    H3_JOB_APPROVAL = "H3"
    H4_UNKNOWN_FIELD = "H4"
    H5_PRESUBMIT = "H5"
    H6_EMAIL_ACTION = "H6"
    H7_REPLY_APPROVAL = "H7"


class PortalStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    NEEDS_ATTENTION = "needs_attention"
    BLOCKED = "blocked"
