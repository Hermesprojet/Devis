"""Source de prix unique par poste — dernier rempart SQL

Un poste tire son prix d'une bibliothèque **ou** d'un sous-détail, jamais des
deux : porter les deux ne veut rien dire, le moteur devrait choisir et son
choix serait arbitraire. La règle était appliquée dans les routes, mais rien
n'empêchait une écriture directe — script, correctif à la main, migration
future — de créer la situation interdite.

Les lignes existantes sont **inspectées avant** la création de la contrainte.
S'il en existe d'incohérentes, la migration s'arrête en donnant leurs
identifiants plutôt que de choisir à la place d'un humain quelle source
supprimer : ce choix engage un prix, donc un devis.

Revision ID: e2be18fcac1b
Revises: 105f11dede7e
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2be18fcac1b"
down_revision: str | None = "105f11dede7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_boq_item_single_price_source"
CONDITION = "price_item_id IS NULL OR composite_price_id IS NULL"


def upgrade() -> None:
    connection = op.get_bind()
    conflicting = [
        str(row[0])
        for row in connection.execute(
            sa.text(
                "SELECT id FROM boq_items "
                "WHERE price_item_id IS NOT NULL AND composite_price_id IS NOT NULL"
            )
        )
    ]
    if conflicting:
        raise RuntimeError(
            "Postes portant à la fois un prix de bibliothèque et un sous-détail : "
            f"{conflicting[:10]}"
            + (f" (et {len(conflicting) - 10} autres)" if len(conflicting) > 10 else "")
            + ". Retirer l'une des deux sources sur chacun avant de rejouer cette "
            "migration. Le choix engage un prix : il revient à un humain, pas à "
            "une migration."
        )

    with op.batch_alter_table("boq_items", schema=None) as batch_op:
        batch_op.create_check_constraint(CONSTRAINT, CONDITION)


def downgrade() -> None:
    with op.batch_alter_table("boq_items", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
