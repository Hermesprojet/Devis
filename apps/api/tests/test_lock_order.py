"""L'ordre de verrouillage est une règle, pas un commentaire.

Ces contrôles sont statiques : ils lisent le code, n'ouvrent aucune base, et
tournent donc partout. Les mettre avec les tests de concurrence les aurait
fait ignorer hors PostgreSQL — c'est-à-dire là où on développe.

Le module `services/locking.py` a d'abord déclaré l'ordre inverse de celui que
le code suivait réellement, avec une justification fausse à l'appui. Un
commentaire ne se vérifie pas ; ces tests, si.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "routers"

#: Les appels qui prennent un verrou de ligne métier.
LOCK_CALLS = {
    "estimating.lock_version",
    "pricebook_versions.lock_version",
    "_version_open_for_writing",
    "estimating.next_version_number",
    "pricebook_versions.next_version_number",
}


def _callee(node: ast.Call) -> str:
    """Nom appelé, sous la forme « module.fonction » ou « fonction »."""
    target = node.func
    if isinstance(target, ast.Attribute):
        if isinstance(target.value, ast.Name):
            return f"{target.value.id}.{target.attr}"
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


class TestLockOrder:
    """L'ordre de verrouillage est une règle, pas un commentaire.

    Deux transactions qui prennent les mêmes verrous dans des ordres opposés
    ne se disputent plus rien : elles s'interbloquent. C'est pire qu'une
    course, parce que l'échec survient même quand la contention était inoffensive.
    """

    def test_only_business_rows_are_lockable(self) -> None:
        from metreo_api.services import locking

        assert "Organization" not in locking.LOCKABLE, (
            "Organization est verrouillée en dernier par audit.record ; "
            "la rendre verrouillable ici inviterait à inverser l'ordre."
        )

    def test_an_unlisted_model_is_refused(self) -> None:
        """Un modèle ajouté sans décision explicite ne passe pas en silence."""
        from metreo_api.models import Project
        from metreo_api.services import locking

        with pytest.raises(AssertionError, match="LOCKABLE"):
            locking.lock_owned(object(), Project, "org", "id")  # type: ignore[arg-type]

    def test_audit_locks_the_organization_after_the_business_row(self) -> None:
        """La séquence réelle, relue dans le code plutôt que supposée.

        Dans **chaque fonction** qui prend un verrou métier, ce verrou doit
        précéder l'appel à `audit.record` — qui verrouille l'organisation.
        La comparaison est faite fonction par fonction : comparer les minima
        d'un fichier entier ne veut rien dire, une route qui n'audite qu'après
        coup n'ayant aucun rapport avec une autre qui verrouille.
        """
        checked = 0
        for source in sorted(ROUTERS.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                locks = [
                    node.lineno
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call) and _callee(node) in LOCK_CALLS
                ]
                audits = [
                    node.lineno
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call) and _callee(node) == "audit.record"
                ]
                if not locks or not audits:
                    continue
                checked += 1
                assert min(locks) < min(audits), (
                    f"{source.name}:{function.name} — le verrou métier doit "
                    "précéder audit.record, qui verrouille l'organisation"
                )
        assert checked >= 2, f"trop peu de fonctions couvertes : {checked}"
