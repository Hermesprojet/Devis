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

    def test_two_locks_in_one_function_follow_the_documented_order(self) -> None:
        """Un appelant qui prend deux lignes métier suit LOCK_ORDER.

        Un seul le fait aujourd'hui — la validation d'un import verrouille le
        lot puis la version. Inverser les deux appels créerait un cycle avec
        tout futur appelant qui prendrait l'ordre inverse.
        """
        from metreo_api.services import locking

        rank = {name: index for index, name in enumerate(locking.LOCK_ORDER)}
        # Quel modèle chaque appel verrouille-t-il ?
        locked_model = {
            "estimating.lock_version": "EstimateVersion",
            "pricebook_versions.lock_version": "PriceBookVersion",
            "_version_open_for_writing": "PriceBookVersion",
            "estimating.next_version_number": "Estimate",
            "pricebook_versions.next_version_number": "PriceBook",
            "_locked_item": "BoqItem",
        }
        checked = 0
        for source in sorted(ROUTERS.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                taken = []
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    callee = _callee(node)
                    model = locked_model.get(callee)
                    # lock_owned(session, Model, ...) — le modèle est le 2e argument.
                    if (
                        model is None
                        and callee == "lock_owned"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Name)
                    ):
                        model = node.args[1].id
                    if model is not None:
                        taken.append((node.lineno, model))
                if len(taken) < 2:
                    continue
                checked += 1
                taken.sort()
                ranks = [rank[model] for _, model in taken if model in rank]
                assert ranks == sorted(ranks), (
                    f"{source.name}:{function.name} verrouille "
                    f"{[model for _, model in taken]} — hors de l'ordre documenté "
                    f"{list(locking.LOCK_ORDER)}"
                )
        assert checked >= 1, "aucune fonction ne prend deux verrous : le test ne prouve rien"

    def test_an_unlisted_model_is_refused(self) -> None:
        """Un modèle ajouté sans décision explicite ne passe pas en silence."""
        from metreo_api.models import Project
        from metreo_api.services import locking

        with pytest.raises(AssertionError, match="LOCK_ORDER"):
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
