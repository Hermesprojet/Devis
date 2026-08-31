"""Le contrôle des chemins cités attrape bien ce qu'il prétend attraper.

Un garde-fou qui passe toujours ne garde rien. Celui-ci a été écrit après
avoir trouvé, dans la documentation du dépôt, trois affirmations devenues
fausses en trois PR : sept tables absentes de `docs/DATA_MODEL.md`, la moitié
des modules absents de `docs/ARCHITECTURE.md`, et un `scripts/README.md` qui
annonçait un répertoire vide alors qu'il portait sept scripts.

Ces tests-ci ne relisent pas la documentation : ils vérifient que le contrôle
refuse une rupture, accepte un chemin abrégé, et accepte un fichier que
l'exploitant crée lui-même.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]


def _charger() -> ModuleType:
    chemin = ROOT / "scripts" / "verifier_chemins_documentes.py"
    spec = importlib.util.spec_from_file_location("verifier_chemins_documentes", chemin)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verifier_chemins_documentes"] = module
    spec.loader.exec_module(module)
    return module


verificateur = _charger()


def test_la_documentation_du_depot_ne_cite_aucun_chemin_disparu() -> None:
    """Le contrôle réel, sur les documents réels."""
    manquants = verificateur.introuvables()
    assert not manquants, "chemins cités et introuvables : " + "; ".join(
        f"{d.name} → {sorted(c)}" for d, c in manquants.items()
    )


def test_un_chemin_abrege_se_resout() -> None:
    """La convention de `docs/CONVENTIONS.md`, éprouvée plutôt que supposée.

    La documentation écrit `services/tenant.py`, pas
    `apps/api/src/metreo_api/services/tenant.py`. Si la résolution cessait de
    fonctionner, le contrôle rougirait partout d'un coup — mais si elle se
    mettait à tout accepter, il ne rougirait plus jamais. C'est ce second cas
    que ce test surveille.
    """
    assert verificateur.resoudre("services/tenant.py") is not None
    assert verificateur.resoudre("adr/0002-multi-tenancy.md") is not None
    assert verificateur.resoudre("apps/api/src/metreo_api/models.py") is not None


def test_un_chemin_disparu_est_refuse() -> None:
    """La moitié qui compte : le contrôle doit savoir dire non."""
    assert verificateur.resoudre("services/ce_module_n_existe_pas.py") is None
    assert verificateur.resoudre("docs/UN_DOCUMENT_IMAGINAIRE.md") is None


def test_un_fichier_cree_par_l_exploitant_est_accepte_grace_a_son_modele() -> None:
    """`infra/staging.env` n'existe pas, et ne doit jamais exister.

    `docs/EXPLOITATION.md` le cite et dit lui-même qu'il n'est pas versionné.
    Son `.example` prouve que la citation vise un fichier prévu. Sans cette
    règle, le contrôle exigerait de committer un fichier de secrets — soit
    exactement le contraire de ce que le document demande.
    """
    assert not (ROOT / "infra" / "staging.env").exists()
    assert verificateur.resoudre("infra/staging.env") is not None
    # Et la règle ne s'étend pas à n'importe quoi : sans modèle, pas de laissez-passer.
    assert verificateur.resoudre("infra/imaginaire.env") is None
