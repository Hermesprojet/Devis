"""Append-only audit journal with a per-organisation hash chain.

Each event hashes its own content together with the hash of the previous event
of the same organisation. Removing or editing a row breaks every hash after it,
which :func:`verify_chain` reports. This makes tampering *detectable*; making it
*impossible* needs write-once storage and is scoped to Phase 5.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditEvent, utcnow


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(
    *,
    sequence: int,
    occurred_at: str,
    actor_user_id: str | None,
    action: str,
    object_type: str,
    object_id: str | None,
    summary: str,
    payload: dict[str, Any],
    previous_hash: str | None,
) -> str:
    material = _canonical(
        {
            "sequence": sequence,
            "occurred_at": occurred_at,
            "actor_user_id": actor_user_id,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "summary": summary,
            "payload": payload,
            "previous_hash": previous_hash or "",
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record(
    session: Session,
    *,
    organization_id: str,
    action: str,
    object_type: str,
    summary: str,
    object_id: str | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """Append one event. Never raises for a business reason; failures are bugs.

    ``payload`` must not contain document contents, credentials or personal data
    beyond what identifies the object.
    """
    last = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).first()
    sequence = (last.sequence if last else 0) + 1
    occurred_at = utcnow()
    safe_payload = payload or {}

    event = AuditEvent(
        organization_id=organization_id,
        sequence=sequence,
        occurred_at=occurred_at,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        action=action,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        payload=safe_payload,
        request_id=request_id,
        previous_hash=last.hash if last else None,
        hash=compute_hash(
            sequence=sequence,
            occurred_at=occurred_at.isoformat(),
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            summary=summary,
            payload=safe_payload,
            previous_hash=last.hash if last else None,
        ),
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session, organization_id: str) -> dict[str, Any]:
    """Recompute the whole chain and report the first inconsistency."""
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.sequence.asc())
    ).all()
    previous_hash: str | None = None
    for index, event in enumerate(events):
        expected_sequence = index + 1
        if event.sequence != expected_sequence:
            return {
                "valid": False,
                "checked": len(events),
                "failed_at_sequence": event.sequence,
                "reason": "sequence_gap",
            }
        expected = compute_hash(
            sequence=event.sequence,
            occurred_at=event.occurred_at.isoformat(),
            actor_user_id=event.actor_user_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            summary=event.summary,
            payload=event.payload,
            previous_hash=previous_hash,
        )
        if event.hash != expected or event.previous_hash != previous_hash:
            return {
                "valid": False,
                "checked": len(events),
                "failed_at_sequence": event.sequence,
                "reason": "hash_mismatch",
            }
        previous_hash = event.hash
    return {"valid": True, "checked": len(events), "head_hash": previous_hash}


def count_events(session: Session, organization_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.organization_id == organization_id)
        )
        or 0
    )
