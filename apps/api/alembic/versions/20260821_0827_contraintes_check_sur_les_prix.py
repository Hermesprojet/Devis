"""Contraintes CHECK sur les prix — dernier rempart

Le contrat applicatif (`services/price_contract.py`) produit les erreurs
lisibles ; ces contraintes protègent ce qui ne passe pas par lui : scripts
d'exploitation, migrations futures, correctifs appliqués à la main, et
défauts applicatifs à venir.

Elles sont volontairement grossières. Une contrainte SQL ne remplace pas une
validation métier — elle ne sait pas dire *pourquoi* — mais elle rend
impossible une écriture qu'aucun humain n'a voulue.

Revision ID: 105f11dede7e
Revises: c6526f663ff3
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "105f11dede7e"
down_revision: str | None = "c6526f663ff3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Écrites en SQL portable : ni ENUM PostgreSQL, ni fonction propre à SQLite.
CONSTRAINTS = (
    (
        "ck_price_item_resource_kind",
        "resource_kind IN ('material','labor','equipment','transport',"
        "'disposal','subcontract','other')",
    ),
    (
        "ck_price_item_status",
        "status IN ('active','draft','archived','superseded')",
    ),
    (
        "ck_price_item_confidence",
        "confidence IN ('declared','quoted','contracted','estimated')",
    ),
    (
        # Les deux dates sont facultatives ; la contrainte ne mord que si les
        # deux existent.
        "ck_price_item_validity_range",
        "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
    ),
    (
        "ck_price_item_lead_time",
        "lead_time_days IS NULL OR (lead_time_days >= 0 AND lead_time_days <= 3650)",
    ),
)


def upgrade() -> None:
    # batch_alter_table : SQLite ne sait pas ajouter une contrainte à une table
    # existante, Alembic la recrée donc.
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        for name, condition in CONSTRAINTS:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        for name, _ in reversed(CONSTRAINTS):
            batch_op.drop_constraint(name, type_="check")
