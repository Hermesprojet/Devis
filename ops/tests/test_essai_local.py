"""Les garde-fous de l'essai sur plans réels, éprouvés sans plan réel.

L'essai manipule des plans clients. Trois choses ne doivent jamais déraper, et
ces tests les lisent dans les fichiers plutôt que de les espérer :

1. la pile n'écoute que sur la boucle locale ;
2. rien de ce qui touche un plan n'atterrit dans le dépôt — ni le dossier
   privé, ni une capture, ni une trace d'échec de Playwright ;
3. le harnais ne parle qu'à une API locale, et ne convertit rien en douce.

Aucun de ces tests ne démarre de serveur : ce sont les fichiers qui décident.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
SCRIPT = RACINE / "ops" / "essai_local.sh"
WEB = RACINE / "apps" / "web"

sys.path.insert(0, str(RACINE / "scripts"))
import essai_sur_plans_reels as harnais  # noqa: E402


def _lancer(*arguments: str, dossier: Path) -> subprocess.CompletedProcess[str]:
    environnement = {
        **os.environ,
        "METREO_ESSAI_DIR": str(dossier),
        "METREO_PYTHON": sys.executable,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        env=environnement,
        timeout=60,
        check=False,
    )


# --------------------------------------------------------------------------
# La pile
# --------------------------------------------------------------------------


def test_les_serveurs_n_ecoutent_que_sur_la_boucle_locale() -> None:
    """Connexion sans mot de passe : joignable du réseau, ce serait une porte ouverte."""
    texte = SCRIPT.read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in texte
    assert "-H 127.0.0.1" in texte
    assert "0.0.0.0" not in texte


def test_un_dossier_prive_dans_le_depot_est_refuse_sans_etre_cree() -> None:
    """Un plan déposé dans le dépôt finirait dans un commit."""
    dans_le_depot = RACINE / "var" / "essai-refuse-par-le-test"
    assert not dans_le_depot.exists()
    resultat = _lancer("up", dossier=dans_le_depot)
    assert resultat.returncode == 1
    assert "DANS le dépôt" in resultat.stderr
    assert not dans_le_depot.exists(), "le dossier refusé a été créé avant d'être refusé"


def test_effacer_demande_confirmation_et_ne_touche_jamais_aux_plans(tmp_path: Path) -> None:
    prive = tmp_path / "essai"
    for dossier in ("base", "stockage", "resultats", "etat", "plans"):
        (prive / dossier).mkdir(parents=True)
        (prive / dossier / "fichier").write_text("x", encoding="utf-8")

    sans_confirmation = _lancer("effacer", dossier=prive)
    assert sans_confirmation.returncode == 1
    assert all((prive / d / "fichier").exists() for d in ("base", "stockage", "resultats", "etat"))

    confirme = _lancer("effacer", "--confirmer", dossier=prive)
    assert confirme.returncode == 0, confirme.stderr
    for dossier in ("base", "stockage", "resultats", "etat"):
        assert not (prive / dossier).exists()
    assert (prive / "plans" / "fichier").exists(), "les plans ont été effacés"


# --------------------------------------------------------------------------
# Le parcours au navigateur
# --------------------------------------------------------------------------


def test_aucune_configuration_de_ci_ne_ramasse_l_essai() -> None:
    """L'essai tourne sur des plans réels ; la CI ne doit jamais le lancer."""
    for configuration in ("playwright.config.ts", "playwright.premier-devis.config.ts"):
        texte = (WEB / configuration).read_text(encoding="utf-8")
        assert "'./essai'" not in texte and '"./essai"' not in texte


def test_les_sorties_du_navigateur_quittent_le_dossier_par_defaut() -> None:
    """`test-results/` est publié en artefact par la CI sur un échec."""
    texte = (WEB / "playwright.essai.config.ts").read_text(encoding="utf-8")
    assert "outputDir: join(CAPTURES" in texte
    assert "horsDuDepot('METREO_ESSAI_CAPTURES')" in texte
    assert "horsDuDepot('METREO_ESSAI_PLAN')" in texte
    assert "webServer" not in texte, "l'essai vise une pile déjà montée, il n'en démarre aucune"


# --------------------------------------------------------------------------
# Le harnais
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "https://preprod.metreobtp.com/api/v1",
        "http://192.168.1.10:8071/api/v1",
        "http://127.0.0.1.exemple.com/api/v1",
    ],
)
def test_le_harnais_refuse_toute_api_qui_n_est_pas_locale(base: str) -> None:
    with pytest.raises(harnais.EchecDEssai, match="pas une adresse locale"):
        harnais.exiger_la_boucle_locale(base)


def test_le_harnais_accepte_la_boucle_locale() -> None:
    assert harnais.exiger_la_boucle_locale("http://127.0.0.1:8071/api/v1/") == (
        "http://127.0.0.1:8071/api/v1"
    )


def test_le_harnais_n_ecrit_rien_dans_le_depot(tmp_path: Path) -> None:
    with pytest.raises(harnais.EchecDEssai, match="dans le dépôt"):
        harnais.ecrire_prive(RACINE / "var" / "compte-rendu.md", "x")
    ecrit = harnais.ecrire_prive(tmp_path / "prive" / "compte-rendu.md", "x")
    assert stat.S_IMODE(ecrit.stat().st_mode) == 0o600


def test_une_reference_dans_une_autre_unite_est_refusee_pas_convertie() -> None:
    """Une conversion écrite dans le harnais serait une seconde arithmétique."""
    mesure = {"libelle": "Cote", "valeur": "95.28", "unite": "cm", "incertitude": "1.1"}
    with pytest.raises(harnais.EchecDEssai, match="écrivez la référence"):
        harnais.comparer("950", mesure, "mm")
    ecart = harnais.comparer("95", mesure, "cm")
    assert ecart.absolu == Decimal("0.28")
    assert ecart.dans_le_plus_ou_moins


def test_un_clic_tombe_au_centre_d_un_pixel_entier() -> None:
    """Le harnais convertit un pixel comme l'écran : son centre, dans la zone RENDUE."""
    tuile = harnais.Tuile(
        page=1, zone=(0.5, 0.5, 0.6, 0.55), pixels=(500, 250), page_en_points=(1000.0, 500.0)
    )
    x, y = tuile.vers_la_page(10.7, 20.2)
    assert x == pytest.approx(0.5 + 10.5 / 500 * 0.1)
    assert y == pytest.approx(0.5 + 20.5 / 250 * 0.05)
    assert tuile.points_par_pixel == pytest.approx(0.2)


def test_l_ecriture_cherchee_dans_le_pdf_est_celle_du_devis() -> None:
    assert harnais.en_belge_pour_le_pdf("6.3787950927") == "6,3787950927"
    assert harnais.en_belge_pour_le_pdf("12345.5") == "12 345,5"
    assert harnais.en_belge_pour_le_pdf("-1250") == "-1 250"
