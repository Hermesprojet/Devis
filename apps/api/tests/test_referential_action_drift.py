"""Une action référentielle ne peut plus exister d'un seul côté.

La révision `b4f2c7d81a05` donne à chaque clé composite l'action de la clé
simple qu'elle double, pour que le résultat d'une suppression ne dépende pas de
l'ordre de création des contraintes. Les modèles, eux, ne déclaraient rien : le
commentaire disait que l'action « appartient aux migrations ».

Ce n'était pas tenable, et ce n'est pas une question de style. `alembic check`
sur une base à jour rendait **seize opérations** — huit couples
`remove_fk` / `add_fk` — parce qu'il voit une contrainte nommée pareillement
mais définie autrement. Tant que rien ne lançait `alembic check`, la dérive
restait invisible ; elle serait apparue le jour où quelqu'un l'aurait branché,
ou aurait monté un schéma depuis les modèles.

Les modèles déclarent donc désormais l'action que la migration pose. Ce fichier
empêche l'écart de revenir, dans les deux sens : une action ajoutée à une
migration sans l'être au modèle, ou l'inverse.

**Pourquoi la comparaison au catalogue est PostgreSQL-only.** SQLite ne sait pas
analyser `ON DELETE SET NULL (colonne)` — mesuré, erreur de syntaxe — et un
`SET NULL` composite nu y échoue sur `NOT NULL constraint failed:
organization_id`, exactement le piège que la liste de colonnes évite. La
révision est donc un no-op délibéré sous SQLite, où les deux ordres de
déclaration donnent de toute façon le même résultat. Trois relations ne sont
pas représentables fidèlement sous SQLite ; elles sont nommées ci-dessous, et
un test vérifie que cette liste ne s'allonge pas en silence.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import ForeignKeyConstraint, text

from .conftest import running_on_postgresql

API_ROOT = Path(__file__).resolve().parents[1]
REVISION = API_ROOT / "alembic" / "versions" / "20260828_0001_deterministic_deletes.py"

#: Les relations dont l'action n'est PAS représentable sous SQLite, et pourquoi.
#:
#: `ON DELETE SET NULL (colonne)` est refusé par l'analyseur SQLite, et la forme
#: nue viderait `organization_id`, NOT NULL. La migration ne pose donc rien sous
#: SQLite pour celles-ci — ce qui n'y change aucun résultat, SQLite appliquant
#: les actions avant de vérifier ce qui reste.
NOT_EXPRESSIBLE_IN_SQLITE: frozenset[str] = frozenset(
    {
        "fk_boq_items_price_item_tenant",
        "fk_boq_items_composite_price_tenant",
        "fk_composite_components_price_item_tenant",
    }
)


def migration_actions() -> dict[str, tuple[str, str | None]]:
    """Ce que la révision pose, lu dans son propre tableau `RELATIONS`.

    Lu et non recopié : une table de vérité dupliquée dans un test cesse d'être
    une vérification dès la première divergence.
    """
    source = REVISION.read_text(encoding="utf-8")
    block = source[source.index("RELATIONS:") : source.index("\ndef _clause")]
    rows = re.findall(
        r'\(\s*"([a-z_]+)",\s*"([a-z_]+)",\s*"([a-z_]+)",\s*"(fk_[a-z_]+)",\s*(None|"[A-Z ]+")\s*,?\s*\)',
        block,
    )
    assert rows, "le tableau RELATIONS de la révision n'a pas pu être lu"
    return {
        name: (column, None if action == "None" else action.strip('"'))
        for _child, column, _parent, name, action in rows
    }


def model_actions() -> dict[str, str | None]:
    """Ce que les modèles déclarent, pour les clés composites tenant."""
    from metreo_api.models import Base

    return {
        constraint.name: constraint.ondelete
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name
        and constraint.name.endswith("_tenant")
    }


def expected_clause(column: str, action: str | None) -> str | None:
    """La forme exacte que le modèle doit déclarer pour cette action."""
    if action is None:
        return None
    if action == "SET NULL":
        return f"SET NULL ({column})"
    return action


class TestNoActionLivesOnOneSideOnly:
    """L'invariant, dans les deux sens."""

    def test_every_migration_action_is_declared_in_the_model(self) -> None:
        posees = migration_actions()
        declarees = model_actions()
        manquantes = {
            name: expected_clause(column, action)
            for name, (column, action) in posees.items()
            if expected_clause(column, action) != declarees.get(name)
        }
        assert manquantes == {}, (
            "des actions existent dans la migration et pas dans le modèle "
            f"(ou sous une autre forme) : {manquantes}. "
            "Un modèle qui tait l'action de sa contrainte fait dériver "
            "`alembic check` sans que rien ne le signale."
        )

    def test_no_model_declares_an_action_the_migration_does_not_pose(self) -> None:
        posees = migration_actions()
        inventees = {
            name: action
            for name, action in model_actions().items()
            if action is not None and name in posees and expected_clause(*posees[name]) != action
        }
        assert inventees == {}, (
            f"des modèles déclarent une action que la migration ne pose pas : {inventees}"
        )

    def test_the_two_sides_cover_exactly_the_same_constraints(self) -> None:
        assert set(migration_actions()) == set(model_actions()), (
            "la révision et les modèles ne parlent pas des mêmes contraintes"
        )

    def test_a_set_null_never_lists_the_organisation_column(self) -> None:
        """La liste de colonnes est ce qui rend `SET NULL` utilisable ici.

        Sans elle, PostgreSQL viderait aussi `organization_id`, NOT NULL, et la
        suppression échouerait. C'est mesuré : la forme nue rend
        `NotNullViolation` sur PostgreSQL et `NOT NULL constraint failed` sur
        SQLite.
        """
        for name, action in model_actions().items():
            if action and action.startswith("SET NULL"):
                assert "organization_id" not in action, (
                    f"{name} viderait `organization_id`, qui est NOT NULL : {action}"
                )


@pytest.mark.skipif(
    not running_on_postgresql(),
    reason=(
        "SQLite ne sait pas représenter `ON DELETE SET NULL (colonne)` ; "
        "la confrontation au catalogue n'a de sens que sur PostgreSQL."
    ),
)
class TestTheCatalogueAgreesWithTheModels:
    """Ce que la base porte vraiment, et non ce que le code prétend poser."""

    def test_every_composite_key_carries_the_action_its_model_declares(
        self, migrated: None
    ) -> None:
        from metreo_api.db import get_engine

        declarees = model_actions()
        with get_engine().connect() as connection:
            reelles = dict(
                connection.execute(
                    text(
                        "SELECT c.conname, pg_get_constraintdef(c.oid) "
                        "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                        "WHERE c.conname LIKE 'fk\\_%\\_tenant' "
                        "  AND t.relnamespace = ("
                        "        SELECT relnamespace FROM pg_class "
                        "        WHERE oid = 'boq_items'::regclass)"
                    )
                ).all()
            )

        assert set(reelles) == set(declarees), (
            f"catalogue {sorted(set(reelles) - set(declarees))} / "
            f"modèles {sorted(set(declarees) - set(reelles))}"
        )
        for name, definition in sorted(reelles.items()):
            marqueur = " ON DELETE "
            position = definition.find(marqueur)
            en_base = definition[position + len(marqueur) :].strip() if position >= 0 else None
            assert en_base == declarees[name], (
                f"{name} : la base porte {en_base!r}, le modèle déclare {declarees[name]!r}"
            )

    def test_no_set_null_list_contains_the_organisation_column(self, migrated: None) -> None:
        """Relu dans `confdelsetcols`, pas dans le texte de la définition."""
        from metreo_api.db import get_engine

        with get_engine().connect() as connection:
            fautives = (
                connection.execute(
                    text(
                        "SELECT c.conname FROM pg_constraint c "
                        "WHERE c.conname LIKE 'fk\\_%\\_tenant' AND c.confdelsetcols IS NOT NULL "
                        "  AND EXISTS (SELECT 1 FROM unnest(c.confdelsetcols) n "
                        "              JOIN pg_attribute a ON a.attrelid = c.conrelid "
                        "                                AND a.attnum = n "
                        "              WHERE a.attname = 'organization_id')"
                    )
                )
                .scalars()
                .all()
            )
        assert list(fautives) == [], fautives


class TestTheSqliteExceptionIsNamedAndBounded:
    """Trois relations, pas une de plus, et la raison écrite.

    Une exception qui s'allonge sans que personne ne le voie cesse d'en être
    une. Ce contrôle tombe si une quatrième action devient non représentable
    sous SQLite sans être déclarée ici.
    """

    def test_only_set_null_relations_are_excepted(self) -> None:
        posees = migration_actions()
        set_null = {name for name, (_c, action) in posees.items() if action == "SET NULL"}
        assert set_null == NOT_EXPRESSIBLE_IN_SQLITE, (
            "la liste des relations non représentables sous SQLite doit valoir "
            f"exactement les relations SET NULL : {sorted(set_null)}"
        )

    def test_the_revision_really_skips_sqlite(self) -> None:
        """Et la révision doit bien ne rien faire sous SQLite, pas le prétendre."""
        tree = ast.parse(REVISION.read_text(encoding="utf-8"))
        gardes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and "dialect" in ast.unparse(node)
            and "postgresql" in ast.unparse(node)
        ]
        assert gardes, (
            "la révision doit tester le dialecte avant d'émettre du DDL "
            "PostgreSQL-only ; aucun garde trouvé"
        )
