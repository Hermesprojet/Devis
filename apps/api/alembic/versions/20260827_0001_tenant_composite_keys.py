"""Clés étrangères composites : la base refuse un parent d'une autre organisation.

Neuf relations de la Phase 1 laissaient PostgreSQL indifférent à l'organisation
du parent. Mesuré avant ce travail : neuf sur neuf acceptées par des `INSERT`
directs. Les routes tenaient la frontière — elles répondent 404 — mais rien
d'autre : un script d'exploitation, une correction manuelle en base ou un
import mal écrit passaient au travers, et le calcul produisait ensuite un prix
tiré des tarifs de quelqu'un d'autre.

Chaque parent reçoit une unicité `(id, organization_id)` — techniquement
redondante puisque `id` est déjà unique, mais nécessaire : PostgreSQL exige que
les colonnes référencées portent une contrainte d'unicité. Chaque enfant
référence alors `(parent_id, organization_id)`.

**Aucune action référentielle** sur ces clés composites. Les clés simples
existantes gardent les leurs : `ON DELETE SET NULL` sur une clé composite
tenterait de vider aussi `organization_id`, qui est NOT NULL, et la suppression
échouerait. La clé simple met le parent à NULL, puis la clé composite —
`MATCH SIMPLE` — ne vérifie plus rien, une colonne étant NULL. Les deux chemins
de suppression sont couverts par des tests.

**Ce que cette migration ne fait pas** : elle ne corrige aucune donnée. Si une
ligne croise déjà deux organisations, elle s'arrête et nomme la relation et le
nombre. Réattribuer une ligne à une autre organisation ou la rattacher à un
autre parent est une décision métier — le montant d'un devis en dépend — et
appartient à un opérateur, pas à une migration.

**Verrous attendus sur PostgreSQL.** `ADD CONSTRAINT UNIQUE` construit un index
et prend un `ACCESS EXCLUSIVE` sur la table parente pendant sa construction.
`ADD CONSTRAINT FOREIGN KEY` prend un `SHARE ROW EXCLUSIVE` sur l'enfant **et**
sur le parent, et valide en scannant l'enfant. La variante
`NOT VALID` + `VALIDATE CONSTRAINT` a été évaluée et écartée : Alembic exécute
la révision dans une seule transaction, si bien que le verrou est de toute
façon tenu jusqu'au commit et que la séparation n'apporte rien. Elle ne vaudrait
que découpée en deux déploiements distincts, ce qui rendrait le `downgrade`
ambigu. Sur des tables de la taille actuelle, le scan se compte en
millisecondes ; sur un volume important, prévoir une fenêtre.

Revision ID: 7c1e4a9b2d30
Revises: e2be18fcac1b
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e4a9b2d30"
down_revision: str | None = "e2be18fcac1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Table parente → nom de l'unicité `(id, organization_id)`.
PARENT_KEYS: tuple[tuple[str, str], ...] = (
    ("projects", "uq_projects_id_organization"),
    ("bills_of_quantities", "uq_bills_of_quantities_id_organization"),
    ("price_books", "uq_price_books_id_organization"),
    ("price_book_versions", "uq_price_book_versions_id_organization"),
    ("price_items", "uq_price_items_id_organization"),
    ("composite_prices", "uq_composite_prices_id_organization"),
)

#: (table enfant, colonne portant le parent, table parente, nom de la contrainte).
#: L'ordre suit les dépendances : un parent doit porter son unicité avant qu'un
#: enfant ne la référence.
RELATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("bills_of_quantities", "project_id", "projects", "fk_bills_of_quantities_project_tenant"),
    (
        "price_book_versions",
        "price_book_id",
        "price_books",
        "fk_price_book_versions_price_book_tenant",
    ),
    (
        "price_items",
        "price_book_version_id",
        "price_book_versions",
        "fk_price_items_price_book_version_tenant",
    ),
    ("boq_items", "boq_id", "bills_of_quantities", "fk_boq_items_boq_tenant"),
    ("boq_items", "price_item_id", "price_items", "fk_boq_items_price_item_tenant"),
    ("boq_items", "composite_price_id", "composite_prices", "fk_boq_items_composite_price_tenant"),
    (
        "composite_components",
        "price_item_id",
        "price_items",
        "fk_composite_components_price_item_tenant",
    ),
    ("estimates", "boq_id", "bills_of_quantities", "fk_estimates_boq_tenant"),
    (
        "estimates",
        "price_book_version_id",
        "price_book_versions",
        "fk_estimates_price_book_version_tenant",
    ),
)


def inconsistencies(connection: sa.Connection) -> dict[str, int]:
    """Combien de lignes croisent déjà deux organisations, relation par relation.

    Une requête explicite par relation, et **rien d'autre que des compteurs** :
    ni identifiant, ni référence de projet, ni nom de client ne doit atteindre
    un journal de migration.
    """
    found: dict[str, int] = {}
    for child, column, parent, name in RELATIONS:
        count = connection.execute(
            sa.text(
                f"SELECT count(*) FROM {child} AS c "  # noqa: S608 - noms internes, jamais d'entrée
                f"JOIN {parent} AS p ON p.id = c.{column} "
                f"WHERE c.organization_id <> p.organization_id"
            )
        ).scalar_one()
        if count:
            found[name] = int(count)
    return found


def _refuse(found: dict[str, int]) -> None:
    lines = "\n".join(f"  {name} : {count} ligne(s)" for name, count in sorted(found.items()))
    raise RuntimeError(
        "Migration refusée : des lignes rattachent déjà une organisation au parent "
        "d'une autre.\n"
        f"{lines}\n\n"
        "Aucune donnée n'a été modifiée, et cette migration n'en modifiera aucune : "
        "réattribuer une ligne à une autre organisation ou la rattacher à un autre "
        "parent change un montant, et c'est une décision d'exploitation.\n"
        "Procédure : pour chaque relation nommée ci-dessus, lister les lignes "
        "fautives avec la requête correspondante de `inconsistencies()`, décider "
        "avec le métier — corriger l'organisation, corriger le parent, ou archiver "
        "la ligne — appliquer la décision, puis relancer la migration."
    )


#: Ordre de traitement : un parent doit porter son unicité avant qu'un enfant ne
#: la référence. Trois tables sont à la fois parentes et enfants.
ORDER: tuple[str, ...] = (
    "projects",
    "price_books",
    "bills_of_quantities",
    "price_book_versions",
    "price_items",
    "composite_prices",
    "boq_items",
    "composite_components",
    "estimates",
)


def upgrade() -> None:
    connection = op.get_bind()
    found = inconsistencies(connection)
    if found:
        _refuse(found)

    # UN SEUL bloc par table. Sur SQLite, `batch_alter_table` recopie la table
    # entière : trois blocs sur `boq_items` la recréaient trois fois. Pire, une
    # table à la fois parente et enfant — `bills_of_quantities`,
    # `price_book_versions`, `price_items` — perdait l'unicité posée dans un bloc
    # précédent lors de sa propre recréation. Tout ce qui la concerne se fait
    # donc en une passe.
    parents = dict(PARENT_KEYS)
    for table in ORDER:
        with op.batch_alter_table(table) as batch:
            if table in parents:
                batch.create_unique_constraint(parents[table], ["id", "organization_id"])
            for child, column, parent, name in RELATIONS:
                if child != table:
                    continue
                batch.create_foreign_key(
                    name,
                    parent,
                    [column, "organization_id"],
                    ["id", "organization_id"],
                )


def downgrade() -> None:
    """Symétrique, et sans toucher une seule ligne.

    L'ordre est inversé : une unicité ne peut disparaître tant qu'une clé
    étrangère la référence.
    """
    parents = dict(PARENT_KEYS)
    for table in reversed(ORDER):
        with op.batch_alter_table(table) as batch:
            for child, _, _, name in RELATIONS:
                if child == table:
                    batch.drop_constraint(name, type_="foreignkey")
            if table in parents:
                batch.drop_constraint(parents[table], type_="unique")
