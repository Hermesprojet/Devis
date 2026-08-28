"""Les suppressions ne dépendent plus de l'ordre de création des contraintes.

Chaque relation protégée porte deux clés : une simple, qui garde son action
référentielle — `CASCADE`, `SET NULL` ou rien — et une composite, qui tenait la
frontière multi-tenant sans action du tout.

**Ce que la mesure a montré.** PostgreSQL déclenche les contrôles d'intégrité
référentielle dans l'ordre des OID, c'est-à-dire dans l'ordre de CRÉATION des
contraintes. Sur deux tables construites pour l'essai :

* clé simple créée d'abord → la suppression du parent réussit, l'enfant est mis
  à NULL ou emporté, comme prévu ;
* clé composite créée d'abord → la suppression est **refusée** par la composite,
  qui voit encore l'enfant pointer le parent.

`NO ACTION` ne sauve pas : son report en fin d'instruction ne suffit pas quand
son déclencheur passe avant celui qui applique l'action.

**Pourquoi cela tenait quand même.** Les migrations créent les clés simples
avant les composites, et `pg_dump` réémet les contraintes par ordre
alphabétique de nom : `boq_items_price_item_id_fkey` trie avant
`fk_boq_items_price_item_tenant`. Le bon ordre sortait d'un accident de
nommage. Renommer cette seule clé simple en `zz_…`, exporter, restaurer — et la
suppression d'un prix passe de « référence mise à NULL » à
`ForeignKeyViolation`. Mesuré sur le schéma réel, de bout en bout.

**La correction.** La clé composite reflète désormais l'action de la clé simple
qu'elle double. Quel que soit celui des deux déclencheurs qui passe en premier,
l'état final est le même :

* action `CASCADE` sur la simple → `CASCADE` sur la composite ;
* action `SET NULL` sur la simple → `SET NULL (colonne_enfant)` sur la
  composite, la liste de colonnes évitant que `organization_id`, NOT NULL, soit
  vidée elle aussi ;
* aucune action sur la simple → aucune sur la composite : rien à ordonner.

Les protections multi-tenant ne bougent pas — déplacer un parent référencé,
insérer un enfant croisé, rattacher un enfant au parent d'un autre restent
refusés, à l'identique, mesuré avant et après.

**SQLite n'est pas concerné, et ce n'est pas une supposition.** Les deux ordres
de déclaration y donnent le même résultat : SQLite applique les actions avant de
vérifier ce qui reste. Il ne sait d'ailleurs pas analyser
`ON DELETE SET NULL (colonne)`. Cette révision ne fait donc rien sous SQLite,
et le dit.

Révision : b4f2c7d81a05
Révise : 7c1e4a9b2d30
"""

from __future__ import annotations

from alembic import op

revision = "b4f2c7d81a05"
down_revision = "7c1e4a9b2d30"
branch_labels = None
depends_on = None


#: enfant, colonne, parent, nom de la contrainte composite, action à refléter.
#:
#: L'action est celle que porte DÉJÀ la clé simple de la même relation. Elle
#: n'est pas choisie ici : elle est recopiée, relation par relation, pour que
#: les deux contrôles produisent le même état final quel que soit leur ordre.
RELATIONS: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "bills_of_quantities",
        "project_id",
        "projects",
        "fk_bills_of_quantities_project_tenant",
        "CASCADE",
    ),
    (
        "price_book_versions",
        "price_book_id",
        "price_books",
        "fk_price_book_versions_price_book_tenant",
        "CASCADE",
    ),
    (
        "price_items",
        "price_book_version_id",
        "price_book_versions",
        "fk_price_items_price_book_version_tenant",
        "CASCADE",
    ),
    ("boq_items", "boq_id", "bills_of_quantities", "fk_boq_items_boq_tenant", "CASCADE"),
    ("boq_items", "price_item_id", "price_items", "fk_boq_items_price_item_tenant", "SET NULL"),
    (
        "boq_items",
        "composite_price_id",
        "composite_prices",
        "fk_boq_items_composite_price_tenant",
        "SET NULL",
    ),
    (
        "composite_components",
        "price_item_id",
        "price_items",
        "fk_composite_components_price_item_tenant",
        "SET NULL",
    ),
    ("estimates", "boq_id", "bills_of_quantities", "fk_estimates_boq_tenant", "CASCADE"),
    # La clé simple de cette relation ne porte aucune action : supprimer une
    # version tarifaire référencée par un devis est refusé, dans les deux
    # ordres. Il n'y a rien à refléter.
    (
        "estimates",
        "price_book_version_id",
        "price_book_versions",
        "fk_estimates_price_book_version_tenant",
        None,
    ),
)


def _clause(column: str, action: str | None) -> str:
    if action is None:
        return ""
    if action == "SET NULL":
        # La liste de colonnes est ce qui rend l'action portable ici :
        # sans elle, PostgreSQL viderait aussi `organization_id`, NOT NULL,
        # et la suppression échouerait. Disponible depuis PostgreSQL 15.
        return f" ON DELETE SET NULL ({column})"
    return f" ON DELETE {action}"


def _rebuild(with_actions: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Mesuré : les deux ordres de déclaration donnent le même résultat sous
        # SQLite, qui applique les actions avant de vérifier ce qui reste. Et il
        # ne sait pas analyser la liste de colonnes. Rien à faire, donc rien de
        # fait — plutôt qu'une reconstruction de tables sans objet.
        return
    for child, column, parent, name, action in RELATIONS:
        op.drop_constraint(name, child, type_="foreignkey")
        clause = _clause(column, action) if with_actions else ""
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}, organization_id) "
            f"REFERENCES {parent} (id, organization_id){clause}"
        )


def upgrade() -> None:
    _rebuild(with_actions=True)


def downgrade() -> None:
    _rebuild(with_actions=False)
