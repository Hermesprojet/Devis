"""Une ligne de bordereau peut dire de quelle mesure de plan elle vient.

Revision ID: b5c6d7e8090a
Revises: a4b5c6d70809
Create Date: 2026-10-07

**Le défaut comblé.** `calibration_de_plan.reprenables()` définissait depuis
plusieurs livraisons quelles mesures un bordereau a le droit de reprendre — ni
une mesure rejetée, ni une mesure sur laquelle personne n'a tranché. Aucun code
ne les reprenait. La règle existait sans point d'application, et une quantité
mesurée sur un plan ne pouvait arriver dans un devis que recopiée à la main,
donc sans provenance.

**Deux colonnes, et chacune pour une raison différente.**

`source_proposal_id` est le LIEN : il pointe la proposition d'extraction
reprise, et c'est lui qui permet de remonter de la ligne de devis au plan, à la
page et à la boîte où la mesure a été pointée. La clé composite le borne au
tenant de la ligne, comme les trois autres clés de cette table.

`source_mesure` est l'EMPREINTE : la valeur mesurée, son unité, son
incertitude, la décision humaine et son motif, figés à l'instant de la reprise.
Elle existe parce que le lien ne survit pas à tout. **Rien ne détruit
aujourd'hui une proposition reprise**, et deux verrous s'y opposent : la seule
purge livrée est celle d'une organisation entière, qui emporte aussi ses
bordereaux ; et une mesure reprenable porte nécessairement une décision
humaine, or `validation_decisions` est en ajout seul — un déclencheur refuse la
suppression hors purge autorisée.

L'action référentielle est donc choisie pour ce qui viendra — une purge par
document, une rétention par révision — et non pour un cas déjà atteignable. Ce
qu'elle fixe : la disparition d'une proposition mettrait le lien à `NULL` et
**ne détruirait pas la ligne de bordereau**, là où un `CASCADE` aurait fait
disparaître un montant de devis. L'action déclarée est éprouvée par
`test_reprise_d_une_mesure.py`, et confrontée au catalogue PostgreSQL par
`test_referential_action_drift.py`.

L'action référentielle est donc `SET NULL`, et sur PostgreSQL elle ne vide que
`source_proposal_id` : `organization_id` est `NOT NULL`, et un `SET NULL` nu
sur la clé composite viderait les deux. C'est la forme déjà employée par
`fk_boq_items_price_item_tenant` depuis la révision `b4f2c7d81a05`.

**Une mesure ne se reprend qu'une fois par bordereau.** `uq_boq_item_source`
l'impose. Sans elle, deux reprises de la même mesure compteraient deux fois la
même quantité d'ouvrage — le double comptage est, sur un métré, l'erreur la
plus coûteuse et la plus difficile à voir. La contrainte ne porte que sur
`(boq_id, source_proposal_id)` : la même mesure peut alimenter deux bordereaux
distincts, par exemple une variante, et c'est légitime.

Réversible : `downgrade` retire la contrainte, les deux clés et les deux
colonnes. Il ne détruit aucune ligne de bordereau — seulement la trace de leur
origine, ce que l'opérateur sait en revenant en arrière.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5c6d7e8090a"
down_revision = "a4b5c6d70809"
branch_labels = None
depends_on = None

#: La clé composite de tenant, décrite comme la révision `b4f2c7d81a05` le fait.
#:
#: Lue — et non recopiée — par `test_referential_action_drift.py`, qui confronte
#: ce tableau aux modèles et refuse qu'une action vive d'un seul côté. La forme
#: est celle du tableau de cette révision : (enfant, colonne, parent, nom,
#: action).
RELATIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "boq_items",
        "source_proposal_id",
        "extraction_proposals",
        "fk_boq_items_source_proposal_tenant",
        "SET NULL",
    ),
)

FK_SIMPLE = "fk_boq_items_source_proposal"
FK_TENANT = "fk_boq_items_source_proposal_tenant"
UNICITE = "uq_boq_item_source"


def upgrade() -> None:
    op.add_column("boq_items", sa.Column("source_proposal_id", sa.String(length=36), nullable=True))
    op.add_column("boq_items", sa.Column("source_mesure", sa.JSON(), nullable=True))

    # UN SEUL bloc `batch_alter_table` : sur SQLite il recopie la table
    # entière, et deux blocs la recréeraient deux fois — la leçon écrite à côté
    # de la boucle de la révision `20260827_0001`.
    with op.batch_alter_table("boq_items") as lot:
        lot.create_unique_constraint(UNICITE, ["boq_id", "source_proposal_id"])
        lot.create_foreign_key(
            FK_SIMPLE,
            "extraction_proposals",
            ["source_proposal_id"],
            ["id"],
            ondelete="SET NULL",
        )
        # Posée SANS action, comme la révision `20260827_0001` pose les huit
        # autres clés composites de tenant : c'est tout ce que SQLite sait
        # porter. L'action arrive juste après, et seulement sur PostgreSQL.
        lot.create_foreign_key(
            FK_TENANT,
            "extraction_proposals",
            ["organization_id", "source_proposal_id"],
            ["organization_id", "id"],
        )

    # **L'action de la clé composite est PostgreSQL-only, et c'est délibéré.**
    #
    # Elle doit nommer sa colonne — `SET NULL (source_proposal_id)` — sinon
    # elle viderait aussi `organization_id`, qui est NOT NULL, et la
    # suppression échouerait au lieu de dénouer le lien. SQLite ne sait pas
    # analyser cette forme : erreur de syntaxe, mesurée par la révision
    # `b4f2c7d81a05`, et la forme nue y échoue sur le NOT NULL.
    #
    # Ne rien poser sous SQLite ne change aucun résultat — la clé simple y
    # suffit à mettre la colonne à NULL, et SQLite applique les actions avant
    # de vérifier ce qui reste. C'est exactement la situation des trois
    # relations `SET NULL` déjà existantes, et la dérive de déclaration qui en
    # découle est nommée dans `SQLITE_DRIFT` et bornée par un test.
    if op.get_bind().dialect.name == "postgresql":
        for enfant, colonne, parent, nom, action in RELATIONS:
            op.drop_constraint(nom, enfant, type_="foreignkey")
            op.execute(
                sa.text(
                    f"ALTER TABLE {enfant} ADD CONSTRAINT {nom} "  # noqa: S608 - noms internes
                    f"FOREIGN KEY (organization_id, {colonne}) "
                    f"REFERENCES {parent} (organization_id, id) "
                    f"ON DELETE {action} ({colonne})"
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("boq_items") as lot:
        lot.drop_constraint(FK_TENANT, type_="foreignkey")
        lot.drop_constraint(FK_SIMPLE, type_="foreignkey")
        lot.drop_constraint(UNICITE, type_="unique")
    op.drop_column("boq_items", "source_mesure")
    op.drop_column("boq_items", "source_proposal_id")
