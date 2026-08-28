"""Les sept relations tenant restantes passent sous la garde de la base.

La première tranche — révision `7c1e4a9b2d30` — a couvert neuf relations. Sept
autres lient encore deux ressources possédées par une organisation sans que rien
n'oblige les deux à appartenir à la MÊME. Mesuré sur PostgreSQL 16, dans une
base créée pour l'expérience : **sept sur sept** acceptent un parent d'une autre
organisation, lignes construites par l'ORM pour franchir toutes les autres
contraintes.

Aucune n'est atteignable par une route — les services valident leurs parents par
`get_owned` — d'où leur classement en P2 plutôt qu'en P1. Un script de reprise,
un seed ou une correction manuelle passent en revanche au travers, et le calcul
produit ensuite un montant tiré des données de quelqu'un d'autre.

**Les actions référentielles ne sont pas recopiées d'une tranche à l'autre.**
Chacune est décidée relation par relation, et vaut ce que vaut la clé simple
qu'elle double : six `CASCADE`, et rien du tout pour
`estimate_versions.price_book_version_id`, dont la clé simple ne porte aucune
action — supprimer une version tarifaire gelée dans un devis reste refusé. Sans
ce reflet, le résultat d'une suppression dépendrait de l'ordre de création des
contraintes, ce que la révision `b4f2c7d81a05` a démontré et corrigé pour la
première tranche.

Aucune de ces sept n'est nullable, donc aucune n'a besoin d'une liste de
colonnes : `CASCADE` s'exprime sur les deux moteurs, et les deux moteurs
reçoivent ici la même chose.

Deux unicités parentes manquaient : `boq_items` — qui se référence elle-même par
`parent_id` — et `estimates`.

**Un préflight compte, et ne répare rien.** Sept requêtes séparées avant toute
écriture ; une incohérence arrête la migration en nommant la relation et le
nombre. Réattribuer une ligne change un montant de devis : c'est une décision
d'exploitation, pas une ligne de migration.

Révision : c9d3a5e71b62
Révise : b4f2c7d81a05
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "c9d3a5e71b62"
down_revision = "b4f2c7d81a05"
branch_labels = None
depends_on = None


#: Les unicités parentes qui manquaient encore.
PARENT_KEYS: tuple[tuple[str, str], ...] = (
    ("boq_items", "uq_boq_items_id_organization"),
    ("estimates", "uq_estimates_id_organization"),
)

#: enfant, colonne, parent, nom de la contrainte, action reflétée.
RELATIONS: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "composite_prices",
        "price_book_version_id",
        "price_book_versions",
        "fk_composite_prices_price_book_version_tenant",
        "CASCADE",
    ),
    (
        "composite_components",
        "composite_price_id",
        "composite_prices",
        "fk_composite_components_composite_price_tenant",
        "CASCADE",
    ),
    (
        "import_batches",
        "price_book_version_id",
        "price_book_versions",
        "fk_import_batches_price_book_version_tenant",
        "CASCADE",
    ),
    ("boq_items", "parent_id", "boq_items", "fk_boq_items_parent_tenant", "CASCADE"),
    ("estimates", "project_id", "projects", "fk_estimates_project_tenant", "CASCADE"),
    (
        "estimate_versions",
        "estimate_id",
        "estimates",
        "fk_estimate_versions_estimate_tenant",
        "CASCADE",
    ),
    # Sa clé simple ne porte aucune action : supprimer une version tarifaire
    # gelée dans un devis est refusé, et doit le rester.
    (
        "estimate_versions",
        "price_book_version_id",
        "price_book_versions",
        "fk_estimate_versions_price_book_version_tenant",
        None,
    ),
)

#: Un seul bloc par table, en ordre de dépendance. Sur SQLite,
#: `batch_alter_table` recrée la table entière : plusieurs blocs sur la même
#: table la recréeraient plusieurs fois, et une table à la fois parente et
#: enfant perdrait l'unicité posée dans un bloc précédent.
ORDER: tuple[str, ...] = (
    "composite_prices",
    "boq_items",
    "estimates",
    "estimate_versions",
    "import_batches",
    "composite_components",
)


def inconsistencies(connection) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Compter, relation par relation. Rien d'autre que des compteurs ne sort.

    Ni identifiant, ni référence de projet, ni nom de client n'atteint un
    journal de migration.
    """
    found: dict[str, int] = {}
    for child, column, parent, name, _action in RELATIONS:
        count = connection.execute(
            text(
                f"SELECT count(*) FROM {child} c "
                f"LEFT JOIN {parent} p "
                f"  ON p.id = c.{column} AND p.organization_id = c.organization_id "
                f"WHERE c.{column} IS NOT NULL AND p.id IS NULL"
            )
        ).scalar_one()
        if count:
            found[name] = int(count)
    return found


def _refuse(found: dict[str, int]) -> None:
    lines = "\n".join(f"  {name} : {count} ligne(s)" for name, count in sorted(found.items()))
    raise RuntimeError(
        "Migration refusée : des lignes rattachent déjà une organisation au parent "
        f"d'une autre.\n{lines}\n\n"
        "Aucune donnée n'a été modifiée, et cette migration n'en modifiera aucune : "
        "réattribuer une ligne à une autre organisation ou la rattacher à un autre "
        "parent change un montant, et c'est une décision d'exploitation.\n"
        "Procédure : pour chaque relation nommée ci-dessus, lister les lignes fautives "
        "avec la requête correspondante de `inconsistencies()`, décider avec le métier "
        "— corriger l'organisation, corriger le parent, ou archiver la ligne — appliquer "
        "la décision, puis relancer la migration."
    )


def upgrade() -> None:
    found = inconsistencies(op.get_bind())
    if found:
        _refuse(found)

    parents = dict(PARENT_KEYS)
    for table in ORDER:
        with op.batch_alter_table(table) as batch:
            if table in parents:
                batch.create_unique_constraint(parents[table], ["id", "organization_id"])
            for child, column, parent, name, action in RELATIONS:
                if child != table:
                    continue
                batch.create_foreign_key(
                    name,
                    parent,
                    [column, "organization_id"],
                    ["id", "organization_id"],
                    ondelete=action,
                )


def downgrade() -> None:
    parents = dict(PARENT_KEYS)
    for table in reversed(ORDER):
        with op.batch_alter_table(table) as batch:
            for child, _column, _parent, name, _action in RELATIONS:
                if child == table:
                    batch.drop_constraint(name, type_="foreignkey")
            if table in parents:
                batch.drop_constraint(parents[table], type_="unique")
