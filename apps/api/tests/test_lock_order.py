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

#: Les appels qui prennent un verrou de ligne métier, et la ligne qu'ils
#: verrouillent. **Source unique** : les deux contrôles ci-dessous la lisent,
#: pour qu'une liste ne puisse pas diverger de l'autre. `lock_owned` n'y figure
#: pas parce que son modèle se lit dans son deuxième argument.
LOCK_CALLS: dict[str, str] = {
    "estimating.lock_version": "EstimateVersion",
    "pricebook_versions.lock_version": "PriceBookVersion",
    "_version_open_for_writing": "PriceBookVersion",
    "estimating.next_version_number": "Estimate",
    "pricebook_versions.next_version_number": "PriceBook",
    "documents.next_revision_number": "Document",
    "documents.claim_step_run": "DocumentRevision",
    "documents.succeed_step_run": "DocumentStepRun",
    "documents.fail_step_run": "DocumentStepRun",
    "documents.retry_failed_step_run": "DocumentStepRun",
    "_locked_item": "BoqItem",
    # Verrouille DEUX lignes, dans l'ordre déclaré : la version d'abord — pour
    # refuser une publication qui se glisserait entre le contrôle du statut et
    # l'écriture — puis le sous-détail. C'est la seconde qui est déclarée ici :
    # la première passe par `_version_open_for_writing`, déjà au tableau.
    "_sous_detail_ouvert": "CompositePriceRow",
    # Trouvées par le contrôle d'enveloppes lui-même, au-delà des trois
    # signalées : `transition_item` verrouille pour `approve_item`, et
    # `_version` pour les routes d'estimation lorsqu'on le lui demande.
    "transition_item": "BoqItem",
    "_version": "EstimateVersion",
}

#: Le primitif. Toute fonction qui l'appelle est une enveloppe de verrouillage
#: et doit être déclarée ci-dessus, faute de quoi les contrôles d'ordre la
#: rateraient en silence — c'est ce qui est arrivé à `_locked_item`, laissant
#: `update_item`, `transition_item` et `delete_item` hors de portée.
PRIMITIVE = "lock_owned"


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


def _own_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Les appels de CETTE fonction, sans descendre dans les fonctions internes.

    `ast.walk` traverse les définitions imbriquées : une fonction locale
    définie dans une route mélangerait ses appels à ceux de son hôte, et
    l'ordre constaté ne serait celui d'aucune exécution réelle.
    """
    calls: list[ast.Call] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _locks_taken(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """(ligne, modèle verrouillé) pour chaque verrou pris par cette fonction."""
    taken: list[tuple[int, str]] = []
    for node in _own_calls(function):
        callee = _callee(node)
        model = LOCK_CALLS.get(callee)
        if (
            model is None
            and callee == PRIMITIVE
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
        ):
            model = node.args[1].id
        if model is not None:
            taken.append((node.lineno, model))
    return sorted(taken)


def _functions(source: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


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
        assert "Document" in locking.LOCKABLE
        assert "DocumentRevision" in locking.LOCKABLE
        assert "DocumentStepRun" in locking.LOCKABLE

    def test_document_numbering_locks_its_parent(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "services" / "documents.py"
        )
        function = next(item for item in _functions(source) if item.name == "next_revision_number")
        assert [model for _, model in _locks_taken(function)] == ["Document"]

    @pytest.mark.parametrize(
        ("function_name", "model"),
        (
            ("claim_step_run", "DocumentRevision"),
            ("succeed_step_run", "DocumentStepRun"),
            ("fail_step_run", "DocumentStepRun"),
            ("retry_failed_step_run", "DocumentStepRun"),
        ),
    )
    def test_document_step_services_lock_the_row_they_decide(
        self, function_name: str, model: str
    ) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "services" / "documents.py"
        )
        function = next(item for item in _functions(source) if item.name == function_name)
        assert [locked for _, locked in _locks_taken(function)] == [model]

    def test_every_wrapper_of_the_primitive_is_declared(self) -> None:
        """Une nouvelle enveloppe de `lock_owned` ne doit pas passer inaperçue.

        C'est ce qui est arrivé : `_locked_item` a été ajouté sans entrer dans
        la liste, et les trois routes BOQ qui l'utilisent — `update_item`,
        `transition_item`, `delete_item` — sont restées hors de portée des
        contrôles d'ordre, qui affirmaient pourtant couvrir chaque fonction.
        """
        # Une route qui verrouille pour elle-même est vue directement par
        # `_locks_taken`. Le danger est l'ENVELOPPE : une fonction qui
        # verrouille pour le compte d'autres, et que la détection ne reconnaît
        # pas — ses appelants deviennent alors invisibles.
        called_elsewhere: set[str] = set()
        locking_functions: list[tuple[str, str]] = []
        for source in sorted(ROUTERS.glob("*.py")):
            for function in _functions(source):
                callees = {_callee(node) for node in _own_calls(function)}
                called_elsewhere |= {name for name in callees if name != function.name}
                if PRIMITIVE in callees or _locks_taken(function):
                    locking_functions.append((source.name, function.name))

        undeclared = [
            f"{module}:{name}"
            for module, name in locking_functions
            if name in called_elsewhere and name not in LOCK_CALLS
        ]
        assert undeclared == [], (
            f"enveloppes de verrouillage non déclarées dans LOCK_CALLS : {undeclared} — "
            "leurs appelants échapperaient aux contrôles d'ordre"
        )

    def test_audit_locks_the_organization_after_the_business_row(self) -> None:
        """Dans chaque fonction qui verrouille, le verrou précède `audit.record`.

        Fonction par fonction, sans descendre dans les fonctions imbriquées :
        comparer les minima d'un fichier entier ne veut rien dire, et mélanger
        une fonction locale à son hôte donnerait un ordre qui n'est celui
        d'aucune exécution.
        """
        covered: list[str] = []
        for source in sorted(ROUTERS.glob("*.py")):
            for function in _functions(source):
                locks = [line for line, _ in _locks_taken(function)]
                audits = [
                    node.lineno for node in _own_calls(function) if _callee(node) == "audit.record"
                ]
                if not locks or not audits:
                    continue
                covered.append(f"{source.name}:{function.name}")
                assert min(locks) < min(audits), (
                    f"{source.name}:{function.name} — le verrou métier doit "
                    "précéder audit.record, qui verrouille l'organisation"
                )
        # Les trois routes BOQ que l'ancienne liste ratait doivent y être.
        for expected in ("boq.py:update_item", "boq.py:transition_item", "boq.py:delete_item"):
            assert expected in covered, f"{expected} n'est pas couvert : {covered}"
        assert len(covered) >= 6, covered

    def test_two_locks_in_one_function_follow_the_documented_order(self) -> None:
        """Un appelant qui prend deux lignes métier suit LOCK_ORDER."""
        from metreo_api.services import locking

        rank = {name: index for index, name in enumerate(locking.LOCK_ORDER)}
        checked = 0
        for source in sorted(ROUTERS.glob("*.py")):
            for function in _functions(source):
                taken = _locks_taken(function)
                if len(taken) < 2:
                    continue
                checked += 1
                ranks = [rank[model] for _, model in taken if model in rank]
                assert ranks == sorted(ranks), (
                    f"{source.name}:{function.name} verrouille "
                    f"{[model for _, model in taken]} — hors de l'ordre documenté "
                    f"{list(locking.LOCK_ORDER)}"
                )
        assert checked >= 1, "aucune fonction ne prend deux verrous : le test ne prouve rien"


class TestTheLockableRefusal:
    """Le refus de verrouiller une ligne hors liste, et sa survie sous `-O`.

    `locking.py` a choisi `raise` plutôt qu'`assert` précisément parce que
    `python -O` supprime les assertions, et son commentaire annonce que ce
    choix est « vérifié dans un sous-processus par
    `test_the_refusal_survives_python_optimised_mode` ». Ce test n'existait
    nulle part dans le dépôt : le commentaire promettait une vérification
    absente. Mesuré par ailleurs par une campagne de mutation — en remplaçant
    `if model.__name__ not in LOCKABLE:` par `if False:`, la suite complète
    restait verte.

    Le refus tombe avant toute utilisation de la session, d'où le `None` passé
    en premier argument : si le contrôle disparaissait, l'appel échouerait sur
    la session et non par un `RuntimeError`, ce qui se voit.
    """

    def test_locking_a_model_outside_the_list_is_refused(self) -> None:
        from metreo_api.models import User
        from metreo_api.services import locking

        assert "User" not in locking.LOCKABLE

        with pytest.raises(RuntimeError) as refus:
            locking.lock_owned(None, User, "une-organisation", "un-objet")
        assert "verrouillables" in str(refus.value)

    def test_the_refusal_survives_python_optimised_mode(self) -> None:
        """Dans un vrai sous-processus `-O`, sinon la garantie n'est pas testée.

        Le sous-processus commence par prouver que les assertions sont bien
        supprimées. Sans ce contrôle, un sous-processus lancé sans `-O`
        passerait ce test en ne démontrant rien — vérifié en retirant `-O` de
        l'appel ci-dessous : le test échoue alors sur ce contrôle.
        """
        import subprocess
        import sys

        programme = (
            "import sys\n"
            "assertions_actives = False\n"
            "try:\n"
            "    assert False\n"
            "except AssertionError:\n"
            "    assertions_actives = True\n"
            "if assertions_actives:\n"
            "    print('ASSERTIONS-ACTIVES'); sys.exit(2)\n"
            "from metreo_api.models import User\n"
            "from metreo_api.services.locking import lock_owned\n"
            "try:\n"
            "    lock_owned(None, User, 'une-organisation', 'un-objet')\n"
            "except RuntimeError:\n"
            "    print('REFUS-TENU'); sys.exit(0)\n"
            "except Exception as erreur:\n"
            "    print('AUTRE:' + type(erreur).__name__); sys.exit(3)\n"
            "print('AUCUN-REFUS'); sys.exit(1)\n"
        )
        acheve = subprocess.run(
            [sys.executable, "-O", "-c", programme],
            capture_output=True,
            text=True,
            timeout=120,
        )
        sortie = acheve.stdout.strip()
        assert sortie != "ASSERTIONS-ACTIVES", (
            "le sous-processus ne tournait pas en mode optimisé : ce test n'aurait rien prouvé"
        )
        assert sortie == "REFUS-TENU", f"sortie={sortie!r} stderr={acheve.stderr[-400:]}"
        assert acheve.returncode == 0
