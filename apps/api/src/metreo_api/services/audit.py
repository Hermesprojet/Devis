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

from ..logging_config import request_id_var
from ..models import AuditEvent, new_id, utcnow


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


#: Version du schéma de hash. Elle entre dans la matière hachée : changer les
#: champs couverts change la version, et une chaîne produite sous l'ancienne
#: version ne peut pas être confondue avec une chaîne falsifiée sous la
#: nouvelle. Toute évolution du schéma exige une reprise documentée des chaînes
#: existantes — il n'y a pas de migration silencieuse d'un journal d'audit.
HASH_SCHEMA_VERSION = 2

#: Champs couverts par le hash. Tout champ dont la falsification doit être
#: détectée figure ici, et `apps/api/tests/test_audit_integrity.py` refuse
#: qu'une colonne d'AuditEvent en soit absente sans justification.
#:
#: La version 1 omettait `event_id`, `organization_id`, `actor_email` et
#: `request_id` : l'adresse de l'acteur pouvait être réécrite en base sans que
#: `verify_chain` s'en aperçoive, et un événement pouvait être déplacé d'une
#: organisation à l'autre.
HASHED_FIELDS = (
    "schema_version",
    "event_id",
    "organization_id",
    "sequence",
    "occurred_at",
    "actor_user_id",
    "actor_email",
    "request_id",
    "action",
    "object_type",
    "object_id",
    "summary",
    "payload",
    "previous_hash",
)


def compute_hash(
    *,
    event_id: str,
    organization_id: str,
    sequence: int,
    occurred_at: str,
    actor_user_id: str | None,
    actor_email: str | None,
    request_id: str | None,
    action: str,
    object_type: str,
    object_id: str | None,
    summary: str,
    payload: dict[str, Any],
    previous_hash: str | None,
) -> str:
    material = _canonical(
        {
            "schema_version": HASH_SCHEMA_VERSION,
            "event_id": event_id,
            "organization_id": organization_id,
            "sequence": sequence,
            "occurred_at": occurred_at,
            "actor_user_id": actor_user_id,
            "actor_email": actor_email,
            "request_id": request_id,
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
    # L'identifiant de requête n'a pas à être passé de main en main par chaque
    # appelant : il est déjà dans le contexte, et l'oublier une seule fois
    # rompait la corrélation entre le journal applicatif et l'audit.
    if request_id is None:
        request_id = request_id_var.get() or None
        if request_id == "-":
            request_id = None

    statement = (
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    )
    # `MAX(sequence) + 1` est une course : deux transactions concurrentes lisent
    # la même valeur et l'une des deux viole `uq_audit_org_sequence`. Le verrou
    # de ligne sérialise l'allocation par organisation — les autres tenants ne
    # sont pas retenus. SQLite n'a pas de SELECT ... FOR UPDATE, mais y sérialise
    # déjà les écritures ; la contrainte d'unicité reste le dernier rempart dans
    # les deux cas.
    if session.bind is not None and session.bind.dialect.name != "sqlite":
        statement = statement.with_for_update()
    last = session.scalars(statement).first()

    sequence = (last.sequence if last else 0) + 1
    occurred_at = utcnow()
    safe_payload = payload or {}
    event_id = new_id()

    event = AuditEvent(
        id=event_id,
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
            event_id=event_id,
            organization_id=organization_id,
            sequence=sequence,
            occurred_at=occurred_at.isoformat(),
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            request_id=request_id,
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
            event_id=event.id,
            organization_id=event.organization_id,
            sequence=event.sequence,
            occurred_at=event.occurred_at.isoformat(),
            actor_user_id=event.actor_user_id,
            actor_email=event.actor_email,
            request_id=event.request_id,
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
