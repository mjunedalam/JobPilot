"""Approval tokens bind a HITL decision to the exact payload the harness will execute."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobpilot.core.models import AuditLog
from jobpilot.core.status import Checkpoint

APPROVING_DECISIONS = frozenset({"approve", "approve_and_send", "submit"})


@dataclass(frozen=True)
class ApprovalToken:
    checkpoint: Checkpoint
    audit_id: int


def verify_approval(
    session: Session, token: ApprovalToken | None, checkpoint: Checkpoint, payload_sha256: str
) -> str | None:
    """None when the approval is valid, otherwise the reason it is not."""
    if token is None:
        return "no approval supplied"
    if token.checkpoint is not checkpoint:
        return f"approval is for {token.checkpoint.value}, tool needs {checkpoint.value}"
    row = session.get(AuditLog, token.audit_id)
    if row is None or row.checkpoint != checkpoint.value:
        return "approval record not found"
    if row.payload.get("decision") not in APPROVING_DECISIONS:
        return f"decision was '{row.payload.get('decision')}', not an approval"
    if row.payload.get("content_sha256") != payload_sha256:
        return "payload changed after approval; it must be approved again"
    return None
