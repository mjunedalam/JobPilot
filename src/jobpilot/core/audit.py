"""Append-only audit trail for agent actions and HITL decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobpilot.core.hashing import content_sha256
from jobpilot.core.models import AuditLog
from jobpilot.core.redaction import redact_keys as redact
from jobpilot.core.status import Checkpoint


def log_action(
    session: Session,
    actor: str,
    action: str,
    *,
    entity_type: str = "",
    entity_id: str | int = "",
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload=redact(payload or {}),
    )
    session.add(row)
    session.flush()
    return row


def log_decision(
    session: Session,
    checkpoint: Checkpoint,
    decision: str,
    *,
    approved_content: Any,
    entity_type: str = "",
    entity_id: str | int = "",
) -> AuditLog:
    """Record a human decision with the exact content that was approved.

    The SHA-256 of the content is stored so the harness can prove that what it executes
    is byte-for-byte what the user approved."""
    row = AuditLog(
        actor="user",
        action=f"decision:{decision}",
        checkpoint=checkpoint.value,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload={
            **redact({"decision": decision, "approved_content": approved_content}),
            "content_sha256": content_sha256(approved_content),
        },
    )
    session.add(row)
    session.flush()
    return row


def find_decision(
    session: Session, checkpoint: Checkpoint, entity_type: str, entity_id: str | int
) -> AuditLog | None:
    """Latest logged decision for an entity at a checkpoint, used as the approval gate."""
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.checkpoint == checkpoint.value,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == str(entity_id),
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()
