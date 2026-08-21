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

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "c6526f663ff3"
down_revision: str | None = "d88792b38c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Importé ici, et non au module : une migration doit rester lisible seule,
    # mais la classification exige les algorithmes réels — les recopier ici
    # créerait deux vérités qui divergeraient.
    from metreo_api.services.audit import classify_schema_version

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hash_schema_version", sa.Integer(), nullable=True))

    connection = op.get_bind()
    events = connection.execute(
        sa.text(
            "SELECT id, organization_id, sequence, occurred_at, actor_user_id, "
            "actor_email, request_id, action, object_type, object_id, summary, "
            "payload, previous_hash, hash FROM audit_events ORDER BY sequence"
        )
    ).mappings()

    unclassified: list[str] = []
    for row in events:
        payload = row["payload"]
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)

        # `record()` scelle `occurred_at.isoformat()`. Relu depuis SQLite, le
        # même instant revient sous la forme « 2026-08-20 12:00:00 », avec une
        # espace au lieu du « T » : comparer la chaîne brute ferait échouer la
        # classification d'événements parfaitement authentiques. On repasse
        # donc par un datetime avant de resérialiser.
        occurred_at = row["occurred_at"]
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at.replace(" ", "T"))
        occurred_at = occurred_at.isoformat()

        version = classify_schema_version(
            {
                "event_id": row["id"],
                "organization_id": row["organization_id"],
                "sequence": row["sequence"],
                "occurred_at": occurred_at,
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
            continue
        connection.execute(
            sa.text("UPDATE audit_events SET hash_schema_version = :v WHERE id = :id"),
            {"v": version, "id": row["id"]},
        )

    if unclassified:
        raise RuntimeError(
            "Événements d'audit qu'aucune version de schéma ne reproduit : "
            f"{unclassified[:10]}"
            + (f" (et {len(unclassified) - 10} autres)" if len(unclassified) > 10 else "")
            + ". Ils sont falsifiés ou issus d'un schéma inconnu. "
            "La migration s'arrête : un journal d'audit ne se répare pas en silence."
        )

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.alter_column("hash_schema_version", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_column("hash_schema_version")
