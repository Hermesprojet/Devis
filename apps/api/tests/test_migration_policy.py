"""Une migration ne choisit jamais une valeur métier à la place d'un humain.

`105f11dede7e` rencontre des lignes qui violent la contrainte qu'elle installe.
Elle nomme la contrainte, nomme les lignes, explique, et s'arrête. Elle ne
remplace aucune valeur : choisir un `resource_kind`, un `status` ou un
`confidence` à la place de quelqu'un engage un prix, donc un devis.

Ce contrôle est structurel et volontairement grossier : il lit les migrations
et refuse tout `UPDATE` portant sur une colonne métier. Une migration qui
aurait une raison légitime d'en écrire un devra le déclarer explicitement,
c'est-à-dire y réfléchir.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Migrations autorisées à écrire des données, avec la raison. Vide à ce jour.
#: Y ajouter une entrée est une décision, pas une formalité.
ALLOWED_DATA_WRITES: dict[str, str] = {}

#: Colonnes purement techniques : les renseigner n'engage aucun prix.
TECHNICAL_COLUMNS = frozenset({"hash_schema_version"})

_UPDATE = re.compile(r"\bUPDATE\s+(\w+)\s+SET\s+(\w+)", re.IGNORECASE)


def migrations() -> list[Path]:
    return sorted(path for path in VERSIONS.glob("*.py") if path.name != "__init__.py")


class TestNoAutomaticBusinessDecision:
    def test_the_suite_actually_sees_the_migrations(self) -> None:
        """Sans cela, un chemin faux rendrait tous les contrôles ci-dessous vides."""
        assert len(migrations()) >= 4, [path.name for path in migrations()]

    @pytest.mark.parametrize("path", migrations(), ids=lambda path: path.stem[:28])
    def test_no_migration_rewrites_a_business_value(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        revision = next(
            (
                line.split("=", 1)[1].strip().strip('"').strip("'")
                for line in source.splitlines()
                if line.startswith("revision:")
            ),
            path.stem,
        )
        if revision in ALLOWED_DATA_WRITES:
            pytest.skip(f"écriture déclarée : {ALLOWED_DATA_WRITES[revision]}")

        offenders = [
            f"UPDATE {table} SET {column}"
            for table, column in _UPDATE.findall(source)
            if column not in TECHNICAL_COLUMNS
        ]
        assert offenders == [], (
            f"{path.name} réécrit une valeur métier : {offenders}. "
            "Une migration qui rencontre une donnée invalide doit nommer la "
            "contrainte, nommer les lignes et s'arrêter — choisir une valeur de "
            "remplacement engage un prix, donc un devis."
        )

    @pytest.mark.parametrize("path", migrations(), ids=lambda path: path.stem[:28])
    def test_no_migration_calls_the_orm_update_or_delete(self, path: Path) -> None:
        """`op.execute(update(...))` ou `session.query(...).delete()` sont aussi des écritures."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = {"update", "delete"}
        found = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden
        ]
        assert found == [], f"{path.name} appelle {found}"


class TestTheRefusalIsExplicit:
    def test_the_check_constraint_migration_names_what_it_refuses(self) -> None:
        """Elle doit s'arrêter en nommant, pas échouer sur une IntegrityError nue."""
        source = next(
            path.read_text(encoding="utf-8")
            for path in migrations()
            if "contraintes_check" in path.name
        )
        assert "RuntimeError" in source
        assert "SELECT id FROM price_items WHERE NOT" in source
        assert "revient à un humain" in source
