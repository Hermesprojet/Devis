"""Version du schéma de hash portée par chaque événement d'audit

Sans cette colonne, `verify_chain` jugeait un scellé d'hier avec le schéma
d'aujourd'hui : changer les champs hachés aurait fait passer toute la chaîne
existante pour falsifiée, sans moyen de distinguer une évolution légitime
d'une altération.

Les lignes existantes reçoivent la version 1. Une chaîne scellée sous une
version antérieure à celle du code est signalée `hash_schema_version_mismatch`
plutôt que déclarée falsifiée — et plutôt que déclarée valide à tort.

Revision ID: c6526f663ff3
Revises: d88792b38c2d
Create Date: 2026-08-21 03:45:38.485923+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6526f663ff3"
down_revision: str | None = "d88792b38c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "hash_schema_version",
                sa.Integer(),
                nullable=False,
                # Indispensable : la table peut déjà porter des lignes, et une
                # colonne NOT NULL sans valeur par défaut échouerait.
                server_default="1",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_column("hash_schema_version")
