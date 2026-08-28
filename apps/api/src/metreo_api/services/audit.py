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
from ..models import AuditEvent, Organization, new_id, utcnow


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


#: Version sous laquelle les nouveaux événements sont scellés.
HASH_SCHEMA_VERSION = 2

#: Champs couverts par chaque version du schéma.
#:
#: La v1 était le schéma d'origine. Elle omettait `event_id`,
#: `organization_id`, `actor_email` et `request_id` : l'adresse de l'acteur
#: pouvait être réécrite en base sans que `verify_chain` s'en aperçoive, et un
#: événement pouvait être déplacé d'une organisation à l'autre. La v2 les
#: couvre et inscrit son propre numéro dans la matière hachée.
#:
#: **Les deux algorithmes restent implémentés.** Un journal d'audit se vérifie
#: des années après avoir été écrit : supprimer une ancienne version
#: reviendrait à déclarer falsifié tout ce qui a été scellé avec elle. Chaque
#: événement porte sa version en base, et c'est elle qui décide de
#: l'algorithme — jamais la version que le code utilise aujourd'hui.
HASHED_FIELDS_V1 = (
    "sequence",
    "occurred_at",
    "actor_user_id",
    "action",
    "object_type",
    "object_id",
    "summary",
    "payload",
    "previous_hash",
)

HASHED_FIELDS_V2 = (
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

#: Champs couverts par la version en cours. `test_audit_integrity.py` refuse
#: qu'une colonne d'AuditEvent en soit absente sans justification.
HASHED_FIELDS = HASHED_FIELDS_V2


class UnsupportedHashSchemaError(Exception):
    """Version de schéma que ce code ne sait pas vérifier."""


def _digest(fields: tuple[str, ...], values: dict[str, Any]) -> str:
    missing = set(fields) - set(values)
    if missing:
        raise RuntimeError(f"Champs manquants pour le hash : {sorted(missing)}")
    material = _canonical({field: values[field] for field in fields})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _compute_v1(values: dict[str, Any]) -> str:
    return _digest(HASHED_FIELDS_V1, values)


def _compute_v2(values: dict[str, Any]) -> str:
    return _digest(HASHED_FIELDS_V2, values)


#: Registre des algorithmes. Ajouter une version, c'est ajouter une entrée ici
#: et une migration qui classe les événements existants — jamais remplacer.
HASH_SCHEMAS: dict[int, Any] = {1: _compute_v1, 2: _compute_v2}


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
    schema_version: int = HASH_SCHEMA_VERSION,
) -> str:
    """Scelle un événement selon la version demandée.

    `schema_version` est explicite et sans valeur par défaut silencieuse au
    moment de l'écriture : `record()` passe toujours la version en cours. Le
    défaut n'existe que pour les appelants qui ne scellent pas mais
    revérifient.
    """
    algorithm = HASH_SCHEMAS.get(schema_version)
    if algorithm is None:
        raise UnsupportedHashSchemaError(f"Schéma de hash inconnu : {schema_version}")
    values: dict[str, Any] = {
        "schema_version": schema_version,
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
    return str(algorithm(values))


def classify_schema_version(values: dict[str, Any], stored_hash: str) -> int | None:
    """Retrouve la version sous laquelle un événement a été scellé.

    Sert la migration : elle recalcule le hash avec chaque algorithme connu et
    retient celui qui reproduit exactement le hash stocké. Aucun hash n'est
    réécrit — on classe, on ne rescelle pas. Un événement qu'aucune version ne
    reproduit est soit falsifié, soit issu d'un schéma inconnu : dans les deux
    cas il faut le savoir, pas le deviner.
    """
    for version, algorithm in sorted(HASH_SCHEMAS.items(), reverse=True):
        candidate = dict(values)
        candidate["schema_version"] = version
        try:
            if algorithm(candidate) == stored_hash:
                return version
        except RuntimeError:  # pragma: no cover - champs absents pour ce schéma
            continue
    return None


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

    # `MAX(sequence) + 1` est une course : deux transactions concurrentes lisent
    # la même valeur et l'une des deux viole `uq_audit_org_sequence`.
    #
    # Verrouiller le dernier événement ne suffisait pas : pour le PREMIER
    # événement d'une organisation, il n'y a aucune ligne à verrouiller, et les
    # deux transactions calculaient `sequence = 1`. C'est donc la ligne
    # `Organization` — qui existe toujours — qui porte le verrou. Elle sérialise
    # l'allocation par tenant sans retenir les autres.
    #
    # Le mode de verrou compte, et `FOR UPDATE` était le mauvais. Toute
    # insertion d'une ligne portant `organization_id` fait vérifier la clé
    # étrangère, et PostgreSQL prend pour cela un `FOR KEY SHARE` sur cette
    # même ligne — un verrou faible, que deux transactions obtiennent
    # ensemble. Chacune demandait ensuite `FOR UPDATE`, incompatible avec le
    # `FOR KEY SHARE` de l'autre : montée de verrou croisée, cycle, et
    # PostgreSQL en tue une avec « deadlock detected ». Deux écritures sans
    # aucun rapport dans la même organisation suffisaient, et la route
    # concernée rendait un HTTP 500.
    #
    # `FOR NO KEY UPDATE` s'oppose à lui-même — donc les allocateurs de
    # séquence restent sérialisés — mais pas au `FOR KEY SHARE` des clés
    # étrangères. C'est exactement la distinction pour laquelle ce mode
    # existe : la table de compatibilité de PostgreSQL dit que `FOR KEY
    # SHARE` ne s'oppose qu'à `FOR UPDATE`.
    #
    # SQLite n'a pas de `SELECT ... FOR UPDATE` mais sérialise déjà ses
    # écritures ; la contrainte d'unicité reste le dernier rempart des deux
    # côtés.
    on_postgres = session.bind is not None and session.bind.dialect.name != "sqlite"
    if on_postgres:
        session.execute(
            select(Organization.id)
            .where(Organization.id == organization_id)
            .with_for_update(key_share=True)
        )

    last = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).first()

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
        hash_schema_version=HASH_SCHEMA_VERSION,
        hash=compute_hash(
            schema_version=HASH_SCHEMA_VERSION,
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
        if event.hash_schema_version not in HASH_SCHEMAS:
            # Une version que ce code ne connaît pas ne peut pas être jugée :
            # le dire, plutôt que de déclarer falsifié ce qu'on ne sait pas
            # relire. Le cas se produit après un retour en arrière du code sur
            # une base déjà migrée.
            return {
                "valid": False,
                "checked": len(events),
                "failed_at_sequence": event.sequence,
                "reason": "unsupported_hash_schema_version",
                "event_schema_version": event.hash_schema_version,
                "supported_versions": sorted(HASH_SCHEMAS),
            }
        expected = compute_hash(
            schema_version=event.hash_schema_version,
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
