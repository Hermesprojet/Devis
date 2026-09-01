"""Les tests ignorés sur SQLite sont contrôlés par leur identité, pas leur nombre.

« 13 ignorés » ne dit rien : une variation dangereuse produirait treize skips
différents. Un test de concurrence pourrait disparaître pendant qu'un test
SQLite ordinaire devient accidentellement ignoré, et le compte resterait juste.

Ce contrôle nomme les modules dont les tests exigent un vrai PostgreSQL, et
vérifie trois choses :

* aucun autre module ne s'ignore sur SQLite — un test ordinaire qui devient
  ignoré est une régression silencieuse ;
* chacun de ces modules porte bien sa garde, et pour la bonne raison ;
* aucun n'a disparu.

Que ces mêmes tests *passent* sous PostgreSQL est vérifié par la CI, dont une
étape refuse un résumé portant `skipped` ou `failed` sur ce jeu de fichiers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: Modules dont TOUS les tests exigent PostgreSQL, et la raison de fond.
#: Cette liste est le contrat : y ajouter ou en retirer une entrée est une
#: décision, et les contrôles ci-dessous refusent toute divergence silencieuse.
POSTGRES_ONLY: dict[str, str] = {
    "test_audit_concurrency.py": "allocation concurrente de la séquence d'audit",
    "test_version_concurrency.py": "courses de numérotation, publication, gel, quantité approuvée",
    "test_write_contention.py": "interblocage entre écritures indépendantes",
    "test_import_idempotence.py": "double validation d'un import",
    # Une contrainte constatée au flush par le serveur, et la visibilité d'une
    # écriture depuis une seconde CONNEXION en `READ COMMITTED` : ni l'une ni
    # l'autre n'existe sur SQLite, où « une autre session » partage le fichier.
    "test_frontieres_postgres.py": "frontières transactionnelles constatées par le serveur",
}

#: Modules qui n'ignorent que CERTAINS de leurs tests, avec la raison.
PARTIALLY_SKIPPED: dict[str, str] = {
    # Un seul cas s'ignore : celui qui fait DÉLIBÉRÉMENT lire à deux
    # connexions le même maximum avant validation. PostgreSQL rend cet
    # entrelacement impossible — c'est le verrou de séquence qui fait son
    # travail — et l'y forcer interbloquerait le test au lieu de prouver
    # quoi que ce soit. La garantie y est prouvée autrement.
    "test_devis_emis.py": "seule la course sans verrou s'ignore",
    "test_audit_migration.py": "un cas déjà couvert autrement sur SQLite",
    # Ses contrôles de propriété — noms possédés, cible destructive disparue —
    # tournent partout ; seule la classe qui touche un vrai serveur s'ignore.
    "test_migration_roundtrip.py": "seule la classe contre un serveur réel s'ignore",
    # Cinq scénarios multi-tenant tournent partout — lot mixte, import hors
    # services, session à deux organisations, les deux moitiés d'un
    # déplacement. Seules les trois classes qui font réellement se croiser deux
    # transactions s'ignorent : SQLite sérialise les écritures et les y faire
    # tourner donnerait du vert sans rien démontrer.
    "test_tenant_concurrency.py": "seules les trois classes de course s'ignorent",
    # Les contrôles de catalogue et l'ordre des déclencheurs sont propres à
    # PostgreSQL ; les trois qui portent sur le RÉSULTAT d'une suppression
    # tournent partout, et c'est le point : la correction ne devait rien
    # changer au comportement observable.
    "test_deletion_determinism.py": "seuls les contrôles de catalogue s'ignorent",
    # Un seul cas s'ignore : deux poses de logo simultanées. La sérialisation
    # repose sur un verrou de LIGNE, que SQLite n'a pas — il verrouille la base
    # entière au moment d'écrire, ce qui ne recouvre pas la fenêtre entre la
    # lecture de l'ancienne clé et l'écriture de la nouvelle. Tout le reste du
    # module — décodeur, permissions, audit, résidus — tourne partout.
    "test_profil_entreprise.py": "seule la course de deux poses de logo s'ignore",
}


def _module_level_skipif(path: Path) -> ast.Call | None:
    """Le `pytestmark = pytest.mark.skipif(...)` du module, s'il existe."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Call):
            return node.value
    return None


class TestTheInventoryIsExact:
    def test_every_declared_module_exists(self) -> None:
        missing = [name for name in POSTGRES_ONLY if not (TESTS / name).exists()]
        assert missing == [], f"modules déclarés PostgreSQL-only mais absents : {missing}"

    @pytest.mark.parametrize("name", sorted(POSTGRES_ONLY))
    def test_a_declared_module_really_skips_without_postgresql(self, name: str) -> None:
        """Sans cette garde, le module tournerait sur SQLite et prouverait le contraire."""
        marker = _module_level_skipif(TESTS / name)
        assert marker is not None, f"{name} ne porte pas de pytestmark au niveau du module"
        source = ast.unparse(marker)
        assert "running_on_postgresql" in source, source

    def test_no_other_module_skips_itself_entirely(self) -> None:
        """Un module ordinaire qui devient ignoré en entier est une régression."""
        unexpected: list[str] = []
        for path in sorted(TESTS.glob("test_*.py")):
            if path.name in POSTGRES_ONLY:
                continue
            marker = _module_level_skipif(path)
            if marker is None:
                continue
            if "running_on_postgresql" in ast.unparse(marker):
                unexpected.append(path.name)
        assert unexpected == [], (
            f"ces modules s'ignorent sur SQLite sans être déclarés : {unexpected} — "
            "les ajouter à POSTGRES_ONLY avec leur raison, ou retirer la garde"
        )

    def test_the_partially_skipped_modules_still_exist(self) -> None:
        for name in PARTIALLY_SKIPPED:
            assert (TESTS / name).exists(), name

    def test_every_module_orchestrating_threads_is_declared(self) -> None:
        """Un test de concurrence qui cesserait d'être exécuté sous PostgreSQL.

        Tout module qui synchronise des fils doit figurer à l'inventaire :
        c'est là que se logent les défauts les plus chers de ce dépôt, et c'est
        là qu'un oubli coûterait le plus.
        """
        forgotten = [
            path.name
            for path in sorted(TESTS.glob("test_*.py"))
            if path.name not in POSTGRES_ONLY
            and path.name not in PARTIALLY_SKIPPED
            # Ce module cite lui-même les chaînes qu'il cherche.
            and path.name != Path(__file__).name
            and (
                "threading.Barrier" in path.read_text(encoding="utf-8")
                or "threading.Event" in path.read_text(encoding="utf-8")
            )
        ]
        assert forgotten == [], (
            f"ces modules orchestrent des fils sans être déclarés PostgreSQL-only : {forgotten}"
        )

    @pytest.mark.parametrize("name", sorted(PARTIALLY_SKIPPED))
    def test_a_partially_skipped_module_gates_on_the_verified_engine(self, name: str) -> None:
        """La garde partielle doit dépendre du moteur réellement vérifié.

        Un module à l'inventaire des ignorés partiels sans aucune garde nommant
        `running_on_postgresql` ne s'ignore pas du tout : l'entrée mentirait, et
        des scénarios que SQLite ne peut pas démontrer passeraient pour prouvés.
        """
        source = (TESTS / name).read_text(encoding="utf-8")
        assert "running_on_postgresql" in source, (
            f"{name} est déclaré partiellement ignoré mais ne porte aucune garde "
            "conditionnée au moteur réellement vérifié"
        )
