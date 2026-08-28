"""Version du schéma de hash portée par chaque événement d'audit

Sans cette colonne, `verify_chain` jugeait un scellé d'hier avec le schéma
d'aujourd'hui : changer les champs hachés aurait fait passer toute la chaîne
existante pour falsifiée, sans moyen de distinguer une évolution légitime
d'une altération.

**La colonne n'est pas remplie par une valeur par défaut.** Une première
version de cette migration attribuait 1 à toutes les lignes existantes : elle
invalidait les événements scellés en v2 par le commit précédent, qui utilisait
déjà `HASH_SCHEMA_VERSION = 2` avant que la colonne n'existe. Chaque ligne est
donc **classée** — son hash est recalculé avec chaque algorithme connu, et la
version retenue est celle qui reproduit exactement le hash stocké. Aucun hash
n'est réécrit : on classe, on ne rescelle pas.

Une ligne qu'aucune version ne reproduit est soit falsifiée, soit issue d'un
schéma que ce code ne connaît pas. La migration s'arrête et le dit, plutôt que
de deviner — un journal d'audit qu'on répare en silence ne vaut plus rien.

Downgrade : la colonne est supprimée. Les événements restent lisibles et leurs
hashs intacts, mais le code d'avant ne sait vérifier que la v1 ; une chaîne
contenant des événements v2 sera signalée invalide jusqu'au retour en avant.
C'est assumé et documenté plutôt que masqué.

Revision ID: c6526f663ff3
Revises: d88792b38c2d
Create Date: 2026-08-21 03:45:38.485923+00:00
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "c6526f663ff3"
down_revision: str | None = "d88792b38c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Algorithmes figés -------------------------------------------------------
#
# Recopiés ici volontairement, et jamais réimportés de `metreo_api.services`.
# Une migration est une vérité historique : elle doit produire dans six mois
# exactement ce qu'elle produit aujourd'hui. Si elle importait le service, une
# évolution — ou une suppression — de `HASH_SCHEMAS` changerait rétroactivement
# le comportement de cette migration, ou empêcherait l'installation d'une base
# neuve. Le prix de cette duplication est assumé : c'est le seul moyen que le
# passé reste stable.

_V1_FIELDS = (
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

_V2_FIELDS = (
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


def _canonical(payload: dict) -> str:
    """Sérialisation canonique, identique à celle du service à cette date.

    `ensure_ascii` reste à sa valeur par défaut, `True` : les accents sont donc
    échappés. Une première version de cette migration passait
    `ensure_ascii=False` et n'aurait classé aucun événement dont le résumé
    portait un accent — c'est-à-dire la quasi-totalité, l'application étant
    francophone. `default=str` sérialise les dates comme le service le fait.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _digest(fields: tuple, values: dict) -> str:
    material = _canonical({field: values[field] for field in fields})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _classify(values: dict, stored_hash: str) -> int | None:
    """Retrouve la version qui reproduit exactement le hash stocké.

    Aucun hash n'est réécrit : on classe, on ne rescelle pas.
    """
    for version, fields in ((2, _V2_FIELDS), (1, _V1_FIELDS)):
        candidate = dict(values)
        candidate["schema_version"] = version
        try:
            if _digest(fields, candidate) == stored_hash:
                return version
        except KeyError:  # pragma: no cover - champ absent pour ce schéma
            continue
    return None


def upgrade() -> None:
    connection = op.get_bind()

    # Lire et classer AVANT de toucher au schéma. L'ordre inverse laissait, sur
    # SQLite — dont le DDL n'est pas transactionnel — une colonne ajoutée et une
    # révision inchangée : la migration devenait impossible à relancer
    # (« duplicate column name »). Ici, si une seule ligne est inclassable, on
    # s'arrête alors que le schéma est encore strictement identique à
    # d88792b38c2d, et une nouvelle tentative repart proprement.
    events = connection.execute(
        sa.text(
            "SELECT id, organization_id, sequence, occurred_at, actor_user_id, "
            "actor_email, request_id, action, object_type, object_id, summary, "
            "payload, previous_hash, hash FROM audit_events ORDER BY sequence"
        )
    ).mappings()

    classified: dict[str, int] = {}
    unclassified: list[str] = []
    for row in events:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)

        # `record()` scelle `occurred_at.isoformat()`. Relu depuis SQLite, le
        # même instant revient sous la forme « 2026-08-20 12:00:00 », avec une
        # espace au lieu du « T » : comparer la chaîne brute ferait échouer la
        # classification d'événements parfaitement authentiques.
        occurred_at = row["occurred_at"]
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at.replace(" ", "T"))

        version = _classify(
            {
                "event_id": row["id"],
                "organization_id": row["organization_id"],
                "sequence": row["sequence"],
                "occurred_at": occurred_at.isoformat(),
                "actor_user_id": row["actor_user_id"],
                "actor_email": row["actor_email"],
                "request_id": row["request_id"],
                "action": row["action"],
                "object_type": row["object_type"],
                "object_id": row["object_id"],
                "summary": row["summary"],
                "payload": payload or {},
                "previous_hash": row["previous_hash"] or "",
            },
            row["hash"],
        )
        if version is None:
            unclassified.append(str(row["id"]))
        else:
            classified[str(row["id"])] = version

    if unclassified:
        raise RuntimeError(
            "Événements d'audit qu'aucune version de schéma ne reproduit : "
            f"{unclassified[:10]}"
            + (f" (et {len(unclassified) - 10} autres)" if len(unclassified) > 10 else "")
            + ". Ils sont falsifiés ou issus d'un schéma inconnu. "
            "La migration s'arrête sans avoir modifié le schéma : "
            "un journal d'audit ne se répare pas en silence."
        )

    # Le schéma n'est touché qu'une fois la classification entièrement réussie.
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hash_schema_version", sa.Integer(), nullable=True))

    for event_id, version in classified.items():
        connection.execute(
            sa.text("UPDATE audit_events SET hash_schema_version = :v WHERE id = :id"),
            {"v": version, "id": event_id},
        )

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.alter_column("hash_schema_version", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_column("hash_schema_version")
