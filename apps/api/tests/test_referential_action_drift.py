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
from pathlib import Path

import pytest
from sqlalchemy import ForeignKeyConstraint, text

from .conftest import running_on_postgresql

API_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = API_ROOT / "alembic" / "versions"

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


#: **La dérive SQLite, nommée relation par relation, avec l'action exacte.**
#:
#: Ce n'est pas une règle générique. « Ignorer toutes les contraintes `_tenant` »
#: laisserait passer une neuvième relation, une action changée ou une colonne
#: `organization_id` glissée dans un `SET NULL` — c'est-à-dire précisément ce
#: contre quoi la révision `b4f2c7d81a05` a été écrite. Chaque nom est ici, avec
#: la clause que le modèle doit déclarer et que la base SQLite ne portera pas.
#:
#: **Pourquoi SQLite ne peut pas les porter.** Son analyseur refuse
#: `ON DELETE SET NULL (colonne)` — erreur de syntaxe, mesurée — et la forme nue
#: y viderait aussi `organization_id`, NOT NULL : `NOT NULL constraint failed`.
#: Aucune déclaration unique ne convient aux deux moteurs, et la révision est
#: donc un no-op délibéré sous SQLite. Cela n'y change AUCUN comportement :
#: mesuré, supprimer un livre de prix référencé emporte bien ses versions, SQLite
#: appliquant les actions avant de vérifier ce qui reste.
#:
#: Un test confronte cette table au tableau `RELATIONS` de la révision : elle ne
#: peut donc ni s'allonger, ni se raccourcir, ni mentir sur une action.
SQLITE_DRIFT: dict[str, str] = {
    "fk_bills_of_quantities_project_tenant": "CASCADE",
    "fk_price_book_versions_price_book_tenant": "CASCADE",
    "fk_price_items_price_book_version_tenant": "CASCADE",
    "fk_boq_items_boq_tenant": "CASCADE",
    "fk_estimates_boq_tenant": "CASCADE",
    "fk_boq_items_price_item_tenant": "SET NULL (price_item_id)",
    "fk_boq_items_composite_price_tenant": "SET NULL (composite_price_id)",
    "fk_composite_components_price_item_tenant": "SET NULL (price_item_id)",
}

#: Chaque relation dérivée produit un couple `remove_fk` / `add_fk`, jamais un
#: seul des deux. Le nombre est donc exact, et il est vérifié comme tel.
SQLITE_DRIFT_OPERATIONS = 2 * len(SQLITE_DRIFT)


def unexpected_sqlite_drift(differences: list[object]) -> list[str]:
    """Ce que `compare_metadata` rend sous SQLite et qu'on ne s'explique pas.

    La liste vide est la seule réussite. Tout le reste est décrit en clair :
    une opération qui n'est pas un couple de clé étrangère, une contrainte
    absente de la table, une action différente de celle attendue, une relation
    attendue qui n'apparaît pas, ou un décompte qui ne tombe pas juste.

    Partagée plutôt que recopiée : le contrôle de dérive de la Phase 2A tourne
    le même `compare_metadata` et doit borner le même écart. Deux listes
    divergeraient dès la première correction.
    """
    anomalies: list[str] = []
    vues: dict[str, set[str]] = {nom: set() for nom in SQLITE_DRIFT}

    for operation in differences:
        if not (isinstance(operation, tuple) and operation[0] in {"remove_fk", "add_fk"}):
            anomalies.append(f"opération étrangère à la dérive connue : {operation!r}")
            continue
        verbe, contrainte = operation[0], operation[1]
        nom = str(getattr(contrainte, "name", "") or "")
        if nom not in SQLITE_DRIFT:
            anomalies.append(f"{verbe} sur « {nom} », qui n'est pas dans la table de dérive")
            continue
        attendue = SQLITE_DRIFT[nom] if verbe == "add_fk" else None
        reelle = getattr(contrainte, "ondelete", None)
        if reelle != attendue:
            anomalies.append(f"{verbe} {nom} : action {reelle!r}, attendue {attendue!r}")
        vues[nom].add(verbe)

    for nom, verbes in sorted(vues.items()):
        if verbes != {"remove_fk", "add_fk"}:
            anomalies.append(f"{nom} : {sorted(verbes) or 'aucune opération'}, couple attendu")

    if len(differences) != SQLITE_DRIFT_OPERATIONS:
        anomalies.append(f"{len(differences)} opérations, {SQLITE_DRIFT_OPERATIONS} attendues")
    return anomalies


def _relations_of(path: Path) -> list[tuple[object, ...]]:
    """Le tableau `RELATIONS` d'une révision, lu sans l'exécuter.

    Lu et non recopié : une table de vérité dupliquée dans un test cesse d'être
    une vérification dès la première divergence. `ast.literal_eval` la lit sans
    importer la révision, donc sans dépendre de son état d'application.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        cible: str | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            cible = node.target.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            cible = node.targets[0].id
        if cible == "RELATIONS" and node.value is not None:
            return [tuple(row) for row in ast.literal_eval(node.value)]
    return []


def _actions_skipped_under_sqlite() -> dict[str, tuple[str, str | None]]:
    """Les relations d'une révision qui ne s'applique PAS sous SQLite.

    Une révision qui ne pose que `CASCADE` s'exécute sur les deux moteurs et ne
    dérive donc pas — c'est le cas de `c9d3a5e71b62`. Seules celles qui portent
    un `SET NULL` sautent SQLite en entier, et ce sont leurs relations, toutes,
    qui dérivent.
    """
    sautees: dict[str, tuple[str, str | None]] = {}
    for chemin in revisions_posing_actions():
        relations = [r for r in _relations_of(chemin) if len(r) == 5]
        if not any(r[4] == "SET NULL" for r in relations):
            continue
        for _child, colonne, _parent, nom, action in relations:
            sautees[str(nom)] = (str(colonne), None if action is None else str(action))
    return sautees


def revisions_posing_actions() -> list[Path]:
    """Toute révision qui déclare des actions, et pas une seule d'entre elles.

    Trouvé en simulant l'intégration : ce module ne lisait qu'un fichier. Une
    fois PR #8 fusionnée dans PR #9, les modèles portaient seize clés composites
    et la table lue n'en décrivait que neuf — le contrôle tombait au rouge sur
    une branche que personne n'avait touchée. La liste se découvre donc.
    """
    return sorted(
        chemin
        for chemin in VERSIONS.glob("*.py")
        if any(len(relation) == 5 for relation in _relations_of(chemin))
    )


def migration_actions() -> dict[str, tuple[str, str | None]]:
    """Ce que les révisions posent, fusionné sur l'ensemble de la chaîne."""
    posees: dict[str, tuple[str, str | None]] = {}
    origines: dict[str, Path] = {}
    for chemin in revisions_posing_actions():
        for relation in _relations_of(chemin):
            if len(relation) != 5:
                continue
            _child, column, _parent, name, action = (
                str(x) if x is not None else None for x in relation
            )
            assert name is not None and column is not None
            if name in posees and posees[name] != (column, action):
                # Deux révisions qui posent des actions différentes sur la même
                # contrainte demandent de savoir laquelle passe en dernier. Tant
                # que le cas ne s'est pas présenté, on refuse de le deviner.
                raise AssertionError(
                    f"{name} reçoit deux actions différentes : "
                    f"{origines[name].name} pose {posees[name]}, "
                    f"{chemin.name} pose {(column, action)}"
                )
            posees[name] = (column, action)
            origines[name] = chemin
    assert posees, "aucun tableau RELATIONS porteur d'actions n'a pu être lu"
    return posees


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


class TestTheSqliteDriftIsNamedAndBounded:
    """Sous SQLite, la révision ne pose rien — donc les modèles y dérivent.

    Trouvé en simulant l'intégration, pas en lisant le code : PR #6 porte un
    contrôle de dérive qui tourne `compare_metadata` sur le moteur de la suite.
    Sous SQLite il rendait **seize opérations** dès que PR #8 arrivait — huit
    couples `remove_fk` / `add_fk`, un par action posée. La porte de CI de PR #8
    est PostgreSQL-only : elle ne pouvait pas le voir.

    Cette dérive n'est pas un défaut à corriger mais une conséquence à nommer.
    Un modèle SQLAlchemy porte UNE déclaration ; PostgreSQL a besoin de
    `SET NULL (colonne)` — sans quoi il vide `organization_id`, NOT NULL — et
    SQLite ne sait pas l'analyser. Aucune déclaration unique ne satisfait les
    deux. Mesuré par ailleurs : l'absence d'action sur la composite ne change
    RIEN au comportement de SQLite — supprimer un livre de prix emporte bien
    ses versions — parce que SQLite applique les actions avant de vérifier ce
    qui reste.

    Ce qui se vérifie ici, c'est donc que la dérive reste EXACTEMENT celle-là :
    les clés composites tenant, et rien d'autre.
    """

    @pytest.mark.skipif(
        running_on_postgresql(),
        reason="la dérive mesurée ici est propre à SQLite ; sur PostgreSQL elle doit être nulle",
    )
    def test_under_sqlite_the_drift_is_exactly_the_named_eight(self, migrated: None) -> None:
        """Huit relations, seize opérations, une action exacte pour chacune."""
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        from metreo_api.db import get_engine
        from metreo_api.models import Base

        with get_engine().connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        anomalies = unexpected_sqlite_drift(list(differences))
        assert anomalies == [], "\n".join(anomalies)

    def test_the_drift_table_matches_the_revision_it_describes(self) -> None:
        """La table ne peut ni s'allonger ni mentir : la révision l'arbitre.

        Sans ce contrôle, élargir l'exception suffirait à faire taire une vraie
        dérive — il suffirait d'ajouter un nom. La table doit valoir exactement
        les relations que la révision non applicable sous SQLite pose avec une
        action, rendues sous la forme que le modèle déclare.
        """
        posees = {
            nom: expected_clause(colonne, action)
            for nom, (colonne, action) in _actions_skipped_under_sqlite().items()
            if action is not None
        }
        assert posees == SQLITE_DRIFT, (
            "la table de dérive SQLite et la révision divergent : "
            f"en trop {sorted(set(SQLITE_DRIFT) - set(posees))}, "
            f"manquantes {sorted(set(posees) - set(SQLITE_DRIFT))}"
        )

    def test_no_excepted_clause_would_empty_the_organisation_column(self) -> None:
        """Une exception ne doit jamais couvrir une clause qui viderait le tenant."""
        for nom, clause in SQLITE_DRIFT.items():
            assert "organization_id" not in clause, (
                f"{nom} : la table de dérive accepte « {clause} », qui viderait "
                "`organization_id`, NOT NULL"
            )

    @pytest.mark.skipif(
        not running_on_postgresql(),
        reason="l'absence de dérive n'a de sens que là où la révision s'applique vraiment",
    )
    def test_under_postgresql_there_is_no_drift_at_all(self, migrated: None) -> None:
        """Le pendant : là où la révision s'applique, l'écart doit être nul."""
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        from metreo_api.db import get_engine
        from metreo_api.models import Base

        with get_engine().connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        assert differences == [], differences


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
        """Et la révision doit bien ne rien faire sous SQLite, pas le prétendre.

        Seules les révisions qui posent une action non représentable sous
        SQLite ont besoin de ce garde : celles qui ne posent que `CASCADE`
        s'appliquent aux deux moteurs, et c'est ce que fait `c9d3a5e71b62`.
        """
        for chemin in revisions_posing_actions():
            actions = {
                str(relation[4])
                for relation in _relations_of(chemin)
                if len(relation) == 5 and relation[4] is not None
            }
            if "SET NULL" not in actions:
                continue
            tree = ast.parse(chemin.read_text(encoding="utf-8"))
            gardes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Compare)
                and "dialect" in ast.unparse(node)
                and "postgresql" in ast.unparse(node)
            ]
            assert gardes, (
                f"{chemin.name} doit tester le dialecte avant d'émettre du DDL "
                "PostgreSQL-only ; aucun garde trouvé"
            )
