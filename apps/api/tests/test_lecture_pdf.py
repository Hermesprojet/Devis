"""Que rend la lecture d'un PDF, où le place-t-elle, et que refuse-t-elle ?

Comme pour le DXF, chaque test FABRIQUE son fichier et dit en une phrase ce
qu'il met dedans — ici avec `scripts/fabriquer_pdf_de_test.py`, qui écrit les
octets à la main sans aucune bibliothèque.

**Le sujet central de ce fichier est une inversion de repère.** Le PDF a son
origine en bas à gauche, l'écran en haut à gauche. Un lecteur qui oublie
l'inversion ne plante pas : il surligne le bas du plan quand la cote est en
haut, et personne ne s'en aperçoit avant qu'un propriétaire cherche une mesure
au mauvais endroit. C'est donc vérifié sur une fixture dont la position
attendue est calculée à la main, dans le test, à partir des points PostScript.

**Le second sujet est ce que la lecture NE fait pas.** Un PDF ne porte aucune
unité de dessin : ses coordonnées sont des points PostScript, qui ne disent
rien de l'ouvrage. Aucun test ici n'attend une mesure, et l'un d'eux vérifie
explicitement que le constat n'en porte pas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metreo_api.services import lecture_pdf

pytest.importorskip(
    "pypdfium2",
    reason="l'extra « pdf » n'est pas installé : pip install './apps/api[pdf]'",
)

# `scripts/` est sur le `sys.path` par le conftest du dépôt : la fabrique de
# fixtures est partagée avec l'épreuve qui tourne DANS l'image, et deux copies
# divergeraient.
import fabriquer_pdf_de_test as fabrique


def _ecrire(dossier: Path, octets: bytes, nom: str = "plan.pdf") -> Path:
    chemin = dossier / nom
    chemin.write_bytes(octets)
    return chemin


# ---------------------------------------------------------------------------
# Le repère : l'inversion verticale, calculée à la main
# ---------------------------------------------------------------------------


def test_a_text_near_the_top_of_the_page_is_reported_near_the_top_of_the_screen(
    tmp_path: Path,
) -> None:
    """Le texte est posé à 80 points du bord BAS d'une page de 100 points.

    Dans le repère du PDF, 80/100 est donc « haut ». Dans celui de l'écran, la
    même position doit être rendue autour de 0,2 — pas de 0,8. Le calcul est
    écrit ici au lieu d'être recopié d'une exécution :

        hauteur de page = 100 pt
        hampe du texte  ≈ 88,4 pt  (corps 12, Helvetica)
        y0 = 1 − 88,4/100 ≈ 0,116

    La tolérance couvre les métriques de la police, pas le sens du calcul :
    sans inversion, le résultat vaudrait 0,798, qui est à vingt tolérances.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.une_page_avec_texte()))

    assert not constat.refuse
    assert constat.pages == 1
    assert constat.dimensions == [(200.0, 100.0)]

    (fragment,) = constat.fragments
    assert fragment.texte == "5000"
    assert fragment.page == 1

    assert fragment.cadre.y0 == pytest.approx(0.116, abs=0.01)
    assert fragment.cadre.y1 == pytest.approx(0.202, abs=0.01)
    # Et la conclusion lisible : la boîte est dans le quart HAUT de l'écran.
    assert fragment.cadre.y1 < 0.25, (
        "le texte est à 80 pt du bord bas d'une page de 100 pt, donc en haut : "
        "une boîte dans la moitié basse signifie que l'axe vertical n'a pas "
        "été inversé, et toute mesure serait surlignée au mauvais endroit"
    )


def test_the_horizontal_axis_is_not_inverted(tmp_path: Path) -> None:
    """L'axe horizontal, lui, va dans le même sens de part et d'autre.

    Ce test existe parce que la correction de l'axe vertical se copie-colle
    facilement sur l'autre : un `1 - x/largeur` symétrique serait une erreur
    exactement aussi silencieuse, et il ne resterait plus un seul axe juste.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.une_page_avec_texte(x=20.0, y=80.0)))

    (fragment,) = constat.fragments
    # 20 pt sur une page de 200 pt : un dixième depuis la GAUCHE, des deux côtés.
    assert fragment.cadre.x0 == pytest.approx(0.10, abs=0.02)
    assert fragment.cadre.x0 < fragment.cadre.x1


def test_each_fragment_keeps_its_own_position_not_its_neighbour_s(
    tmp_path: Path,
) -> None:
    """Neuf textes, neuf positions distinctes, chacune du bon côté.

    Une boucle qui réutiliserait le rectangle précédent — ou la taille de la
    première page — passerait les deux tests ci-dessus et échouerait ici.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_plusieurs_textes()))

    par_texte = {f.texte: f.cadre for f in constat.fragments}
    assert set(par_texte) == {texte for texte, _, _ in fabrique.PLACEMENTS_DU_PLAN}

    # « 5000 » est posé en haut à gauche (40, 180 sur 300 × 220), « Ech. 1:50 »
    # en bas à droite (180, 30). À l'écran, l'ordre vertical s'inverse.
    assert par_texte["5000"].x0 < par_texte["Ech. 1:50"].x0
    assert par_texte["5000"].y0 < par_texte["Ech. 1:50"].y0

    # Et aucune boîte n'est dégénérée ni hors page.
    for texte, cadre in par_texte.items():
        assert 0.0 <= cadre.x0 < cadre.x1 <= 1.0, texte
        assert 0.0 <= cadre.y0 < cadre.y1 <= 1.0, texte


def test_a_fragment_is_normalised_against_its_own_page_not_the_first(
    tmp_path: Path,
) -> None:
    """Deux pages de formats différents, le cas d'un plan et de sa note.

    La seconde page fait 400 × 400 et porte son texte à 320 pt du bas : la
    **ligne de base** est donc au même endroit relatif que sur la première
    (80 pt sur 100), et les deux `y1` doivent coïncider.

    Les `y0`, eux, ne coïncident PAS, et c'est la preuve la plus nette que la
    normalisation est bien faite page par page : la hampe du texte mesure
    8,4 pt dans les deux cas, ce qui pèse 8,4 % d'une page de 100 pt et 2,1 %
    d'une page de 400. La boîte de la seconde page doit donc être **quatre
    fois plus plate**. Un lecteur qui diviserait tout par la hauteur de la
    première page rendrait deux boîtes de même épaisseur.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.deux_pages_de_tailles_differentes()))

    assert constat.pages == 2
    assert constat.dimensions == [(200.0, 100.0), (400.0, 400.0)]

    par_texte = {f.texte: f for f in constat.fragments}
    assert par_texte["PREMIERE"].page == 1
    assert par_texte["SECONDE"].page == 2

    premiere = par_texte["PREMIERE"].cadre
    seconde = par_texte["SECONDE"].cadre

    assert premiere.y1 == pytest.approx(0.20, abs=0.01)
    assert seconde.y1 == pytest.approx(0.20, abs=0.01), (
        "les deux lignes de base sont au même endroit relatif de leur page : "
        "un `y1` différent signifie que la normalisation n'utilise pas la "
        "hauteur de la page du fragment"
    )

    assert (premiere.y1 - premiere.y0) == pytest.approx(4 * (seconde.y1 - seconde.y0), rel=0.05), (
        "le même corps de 12 points sur une page quatre fois plus haute doit "
        "donner une boîte quatre fois plus plate ; deux épaisseurs égales "
        "signifient qu'une seule hauteur de page a servi pour les deux"
    )


def test_pages_are_numbered_from_one_like_a_citation(tmp_path: Path) -> None:
    """Pas de page 0 : une citation documentaire se lit « page 1 », et la
    base porte déjà cette convention pour les citations de texte."""
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.deux_pages_de_tailles_differentes()))

    assert sorted({f.page for f in constat.fragments}) == [1, 2]


# ---------------------------------------------------------------------------
# Ce que la lecture ne fait pas
# ---------------------------------------------------------------------------


def test_reading_a_pdf_produces_no_measurement_and_no_unit(tmp_path: Path) -> None:
    """Un PDF ne déclare aucune unité de dessin, donc rien n'est mesurable.

    Ce test garde une frontière, pas un calcul : le jour où quelqu'un
    ajouterait un champ `valeur` ou `unite` à ce constat, il faudra qu'il ait
    d'abord écrit comment l'échelle a été confirmée par un humain. Les
    dimensions des pages restent en points PostScript, brutes.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_plusieurs_textes()))

    champs = set(vars(constat))
    assert "unite_source" not in champs
    assert not {champ for champ in champs if "mesur" in champ or "valeur" in champ}

    # Les dimensions sont des points PostScript, telles que le document les
    # donne : 300 × 220 et pas une conversion en millimètres.
    assert constat.dimensions == [(300.0, 220.0)]


def test_the_text_is_returned_as_written_without_normalisation(
    tmp_path: Path,
) -> None:
    """« Ech. 1:50 » revient avec son point et ses deux-points.

    Metreo récolte ce que le dessinateur a écrit ; il ne le réécrit pas. Une
    normalisation ici ferait disparaître l'information qui permet justement de
    reconnaître une échelle — et elle la ferait disparaître AVANT qu'un humain
    l'ait vue.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_plusieurs_textes()))

    textes = {f.texte for f in constat.fragments}
    assert "Ech. 1:50" in textes
    assert "NIV +0.00" in textes


# ---------------------------------------------------------------------------
# Les refus, chacun pour sa propre raison
# ---------------------------------------------------------------------------


def test_a_file_that_is_not_a_pdf_is_refused_for_that_reason(tmp_path: Path) -> None:
    """Un DXF déposé comme PDF est refusé sur sa SIGNATURE.

    L'ordre compte : si l'ouverture passait avant, le même fichier serait
    refusé pour « PDF invalide », et l'utilisateur chercherait une corruption
    dans un fichier parfaitement sain mais mal rangé.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.pas_un_pdf()))

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "pas_un_pdf"


def test_a_truncated_pdf_is_refused_as_invalid_not_as_the_wrong_type(
    tmp_path: Path,
) -> None:
    """La signature est bonne, la table d'offsets a disparu.

    Le pendant exact du cas DXF tronqué : un fichier abîmé ne doit pas être
    confondu avec un fichier d'un autre type, sans quoi le message envoie
    chercher la cause ailleurs.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.tronque()))

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "pdf_invalide"


def test_an_encrypted_pdf_is_refused_and_no_password_is_asked(
    tmp_path: Path,
) -> None:
    """Un PDF réellement chiffré — RC4 40 bits, écrit depuis la spécification.

    La fixture n'est pas un PDF portant une fausse étiquette de chiffrement :
    son flux de contenu est chiffré pour de bon, et PDFium s'arrête sur le mot
    de passe. C'est la seule façon de prouver que le code lit la bonne branche.

    Le message doit dire que Metreo **ne demande pas** le mot de passe : un
    écran qui en réclamerait un créerait un champ où coller un secret, et ce
    secret finirait dans un journal.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.chiffre()))

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "pdf_chiffre"
    assert "mot de passe" in constat.motif_du_refus.message
    assert constat.fragments == [], "rien du contenu chiffré ne doit ressortir"


def test_a_document_of_too_many_pages_is_refused_before_any_page_is_read(
    tmp_path: Path,
) -> None:
    """Le plafond est contrôlé AVANT la boucle, et le constat reste vide.

    Un refus qui arriverait après avoir parcouru cinquante et une pages aurait
    déjà payé le coût qu'il prétend éviter.
    """
    combien = lecture_pdf.PLAFOND_PAGES + 1
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.beaucoup_de_pages(combien)))

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "pdf_trop_de_pages"
    assert str(combien) in constat.motif_du_refus.message
    assert constat.pages == 0
    assert constat.dimensions == []


def test_a_document_at_exactly_the_page_ceiling_is_accepted(tmp_path: Path) -> None:
    """Le plafond est inclusif. Un `>=` au lieu d'un `>` refuserait un
    document conforme, et le message parlerait d'un dépassement inexistant."""
    constat = lecture_pdf.lire(
        _ecrire(tmp_path, fabrique.beaucoup_de_pages(lecture_pdf.PLAFOND_PAGES))
    )

    assert not constat.refuse
    assert constat.pages == lecture_pdf.PLAFOND_PAGES


def test_a_missing_file_is_refused_without_raising(tmp_path: Path) -> None:
    """Le lecteur rend un CONSTAT, jamais une exception : c'est la convention
    du lecteur DXF, et l'appelant n'a donc qu'un seul chemin à écrire."""
    constat = lecture_pdf.lire(tmp_path / "jamais-ecrit.pdf")

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "fichier_illisible"


# ---------------------------------------------------------------------------
# L'anomalie qui dégrade sans bloquer
# ---------------------------------------------------------------------------


def test_a_page_without_extractable_text_is_flagged_but_not_refused(
    tmp_path: Path,
) -> None:
    """Un scan reste consultable : l'aperçu sert, l'extraction non.

    Refuser serait le mauvais geste — le propriétaire a le droit de VOIR son
    plan scanné, et de le mesurer à la main. L'anomalie dit seulement que les
    cotes n'en seront pas tirées, et elle nomme la reconnaissance optique
    comme l'étape qui manque, sans promettre qu'elle existe.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_sans_texte()))

    assert not constat.refuse
    assert constat.pages == 1
    assert constat.fragments == []
    assert not constat.porte_du_texte

    codes = [a.code for a in constat.anomalies]
    assert codes == ["texte_absent"]
    assert "reconnaissance optique" in constat.anomalies[0].message


def test_a_vector_plan_is_not_mistaken_for_a_scan(tmp_path: Path) -> None:
    """Le contre-exemple du test précédent, et il est nécessaire.

    Sans lui, un lecteur qui signalerait `texte_absent` sur TOUS les documents
    passerait le test du scan. Le seuil doit séparer, pas affirmer.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_plusieurs_textes()))

    assert constat.porte_du_texte
    assert constat.anomalies == []


def test_the_fragment_ceiling_stops_the_reading_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Au plafond, la lecture s'arrête — et l'extraction se déclare incomplète.

    Le plafond réel est à 50 000 fragments ; une fixture de cette taille
    coûterait des secondes pour éprouver une comparaison. Il est donc abaissé
    à 3 le temps du test, ce qui éprouve la même ligne.

    Ce qui est vérifié n'est pas l'arrêt — c'est qu'il se DISE. Une lecture
    tronquée en silence serait présentée comme complète, et un métré manquerait
    des cotes sans que rien ne l'indique.
    """
    monkeypatch.setattr(lecture_pdf, "PLAFOND_FRAGMENTS", 3)
    monkeypatch.setattr(lecture_pdf, "SEUIL_TEXTE_MAIGRE", 1)

    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_plusieurs_textes()))

    assert not constat.refuse
    assert len(constat.fragments) == 3
    codes = [a.code for a in constat.anomalies]
    assert "trop_de_fragments" in codes
    message = next(a.message for a in constat.anomalies if a.code == "trop_de_fragments")
    assert "complète" in message
