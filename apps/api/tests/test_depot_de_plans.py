"""Un plan déposé est-il reconnu pour ce qu'il est, et le DWG refusé en le disant ?

Ce fichier couvre la seule porte d'entrée d'un plan dans Metreo : la détection
du type réel, dans `services/document_storage.py`. Rien ici ne lit une
géométrie, ne mesure et ne calcule — ces capacités n'existent pas.

**Le défaut d'origine, mesuré avant correctif.** Un DXF ASCII est du texte
lisible, sans octet nul. La détection terminait donc sur
`_ressemble_a_du_texte`, qui le rendait `text/csv` : un plan était rangé en
`.csv` sur le volume, et offert au pipeline d'import de prix. Aucune erreur
n'était levée, aucune trace ne le signalait.

**Pourquoi la détection est structurelle.** Un bordereau de voirie contient
couramment le mot « SECTION » et des codes de poste de la forme `AC1032`.
Chercher ces chaînes quelque part dans le fichier prendrait ce bordereau pour
un plan, ou pour un DWG. La reconnaissance porte donc sur la STRUCTURE — la
première paire « code de groupe, valeur » d'un DXF, l'en-tête de version d'un
DWG — et les deux pièges correspondants sont éprouvés plus bas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metreo_api.services.document_storage import (
    EXTENSIONS,
    ContenuRefuse,
    TropVolumineux,
    detecter_type,
)

RACINE = Path(__file__).resolve().parents[3]
PLANS = RACINE / "fixtures" / "plans"

#: Fabriquées par `scripts/fabriquer_plans_de_test.py`, jamais commitées.
BINAIRES = ("binaire.dxf", "faux.dwg")


def _fixture(nom: str) -> Path:
    chemin = PLANS / nom
    if not chemin.exists() and nom in BINAIRES:
        pytest.skip(
            f"{nom} absent : lancez « python3 scripts/fabriquer_plans_de_test.py »",
        )
    assert chemin.exists(), f"fixture manquante : {chemin}"
    return chemin


def _type_de(nom: str) -> str:
    chemin = _fixture(nom)
    return detecter_type(chemin, chemin.stat().st_size)


# ---------------------------------------------------------------------------
# Ce qui est reconnu
# ---------------------------------------------------------------------------


def test_an_ascii_dxf_is_recognised_as_a_drawing_and_not_as_a_price_table() -> None:
    """La non-régression du défaut d'origine.

    Si ce test tombe en rendant `text/csv`, la détection de texte est repassée
    devant la détection de DXF : l'ordre des deux contrôles est le correctif.
    """
    assert _type_de("mur_simple.dxf") == "image/vnd.dxf"


def test_a_leading_comment_does_not_hide_the_drawing() -> None:
    """Le code de groupe 999 introduit un commentaire libre.

    La plupart des logiciels de dessin en posent un en tête pour s'y nommer.
    Une première version du contrôle exigeait `0` en première ligne et
    rejetait donc les fichiers les plus courants — `mur_simple.dxf` porte ce
    commentaire précisément pour l'interdire à nouveau.
    """
    tete = _fixture("mur_simple.dxf").read_text(encoding="utf-8").splitlines()
    assert tete[0].strip() == "999", "la fixture a perdu son commentaire de tête"
    assert _type_de("mur_simple.dxf") == "image/vnd.dxf"


def test_a_binary_dxf_is_recognised_by_its_sentinel() -> None:
    assert _type_de("binaire.dxf") == "image/vnd.dxf"


def test_a_drawing_is_filed_under_its_own_extension() -> None:
    """Sans cette entrée, le fichier serait rangé sous l'extension du type
    précédent, et un plan porterait `.csv` sur le volume."""
    assert EXTENSIONS["image/vnd.dxf"] == ".dxf"


# ---------------------------------------------------------------------------
# Mutations : les cas tordus qu'un plan réel apporte
# ---------------------------------------------------------------------------


def test_a_drawing_without_units_is_still_accepted_for_storage() -> None:
    """L'unité manquante n'est PAS un motif de refus au dépôt.

    Elle interdira la mesure — c'est la règle du niveau 1 : sans unité ni
    échéance d'échelle, aucune quantité n'est reprenable. Mais refuser le
    fichier priverait l'utilisateur de la visualisation et de l'archivage,
    qui ne demandent aucune unité. Les deux décisions se prennent à des
    endroits différents, et ce test fixe la première.
    """
    assert _type_de("sans_unites.dxf") == "image/vnd.dxf"
    contenu = _fixture("sans_unites.dxf").read_text(encoding="utf-8")
    assert "$INSUNITS" not in contenu, "la fixture a gagné des unités"


def test_a_truncated_drawing_is_stored_and_will_fail_later_at_reading() -> None:
    """Un fichier coupé reste un DXF : il s'annonce comme tel.

    Le dépôt ne le refuse pas, parce que la détection lit la tête et non le
    tout : prétendre valider la structure complète ici obligerait à parcourir
    le fichier entier à l'upload, dans la requête HTTP. Le refus arrive à la
    lecture, où il peut dire ce qui manque.
    """
    assert _type_de("tronque.dxf") == "image/vnd.dxf"


def test_a_dwg_is_refused_by_name_with_what_to_do_instead() -> None:
    """Le DWG est un format propriétaire que Metreo ne lit pas.

    Le refus nomme le format et donne la sortie — exporter en DXF ou en PDF —
    au lieu de rendre « type inconnu », qui laisserait croire à un fichier
    abîmé.
    """
    with pytest.raises(ContenuRefuse) as refus:
        _type_de("faux.dwg")
    message = str(refus.value)
    assert "DWG" in message
    assert "DXF" in message or "PDF" in message


def test_a_price_table_mentioning_sections_is_not_taken_for_a_drawing() -> None:
    """Le piège lexical, en conditions réelles.

    Cette fixture est un bordereau dont les codes valent `AC1032` et `AC1009`
    — des en-têtes de version DWG — et dont les libellés contiennent
    « SECTION ». Elle doit rester un CSV : ni refusée comme DWG, ni rangée
    comme plan.
    """
    assert _type_de("piege_section.csv") == "text/csv"


def test_a_file_over_the_cap_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """Le plafond vaut pour un plan comme pour le reste.

    Il est de 25 Mio par défaut, ce qui est BAS pour un DXF d'exécution : la
    décision de le relever, et pour quels types, est consignée dans
    `docs/adr/0007-lecture-de-plans.md`. Ce test ne fixe pas la valeur, il
    fixe le fait qu'un dépassement est refusé en tant que tel.
    """
    from metreo_api.services.document_storage import StockageLocal

    stockage = StockageLocal(tmp_path / "volume")
    plafond = 1024
    # Deux morceaux, pour que le refus tombe sur le SECOND : c'est ce qui
    # prouve que le compte se fait pendant le flux et non après l'avoir
    # entièrement absorbé.
    morceaux = [b"  0\nSECTION\n" + b"0" * plafond, b"0" * plafond]

    with pytest.raises(TropVolumineux):
        stockage.ecrire(
            organization_id="org-essai",
            document_id="doc-essai",
            revision_id="rev-essai",
            morceaux=morceaux,
            plafond=plafond,
            declared_media_type="image/vnd.dxf",
        )

    restes = list((tmp_path / "volume").rglob("*")) if (tmp_path / "volume").exists() else []
    fichiers = [c for c in restes if c.is_file()]
    assert fichiers == [], f"un reste a été laissé sur le volume : {fichiers}"
