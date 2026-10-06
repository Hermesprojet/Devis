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


# ---------------------------------------------------------------------------
# Le repère, suite : l'origine de la page et sa rotation
# ---------------------------------------------------------------------------


def test_a_page_whose_box_does_not_start_at_zero_is_normalised_the_same(
    tmp_path: Path,
) -> None:
    """Une page de `MediaBox [100 50 300 150]` se lit comme une page à l'origine.

    **Le défaut que ce test ferme, reproduit avant d'être corrigé.**
    `page.get_size()` rend des DIMENSIONS — 200 × 100 — et ne dit pas où la
    page commence. Les rectangles de texte, eux, arrivent en coordonnées
    absolues : (120, 130) et non (20, 80). Diviser l'un par l'autre donnait
    `x0 = 0,60` au lieu de 0,10, et surtout `y0 = y1 = 0` : une boîte PLATE,
    que la contrainte `ck_source_citation_bbox` refuse en base.

    Le rognage silencieux rendait ce défaut invisible — il transformait une
    boîte hors cadre en boîte au bord, sans un mot.
    """
    reference = lecture_pdf.lire(_ecrire(tmp_path, fabrique.une_page_avec_texte(), "origine.pdf"))
    decalee = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_boite_decalee(), "decalee.pdf"))

    (attendu,) = reference.fragments
    (obtenu,) = decalee.fragments
    assert obtenu.texte == attendu.texte
    assert obtenu.position == "exacte"
    assert obtenu.cadre is not None and attendu.cadre is not None

    for nom in ("x0", "y0", "x1", "y1"):
        assert getattr(obtenu.cadre, nom) == pytest.approx(getattr(attendu.cadre, nom), abs=1e-6), (
            f"{nom} diffère : l'origine de la boîte n'est pas prise en compte"
        )

    assert obtenu.cadre.y1 > obtenu.cadre.y0, "la boîte ne doit pas être plate"


@pytest.mark.parametrize(
    ("rotation", "encre"),
    [
        (0, (0.102, 0.115, 0.228, 0.195)),
        (90, (0.800, 0.102, 0.880, 0.228)),
        (180, (0.767, 0.800, 0.895, 0.880)),
        (270, (0.115, 0.770, 0.195, 0.895)),
    ],
)
def test_the_page_rotation_is_applied_to_the_position(
    tmp_path: Path, rotation: int, encre: tuple[float, float, float, float]
) -> None:
    """Les quatre rotations, comparées à la boîte d'encre relevée au pixel.

    **Les valeurs attendues ne viennent pas d'un raisonnement.** Pour chacune
    des quatre rotations, la page a été rendue en PNG et la boîte des pixels
    sombres a été relevée. Déduire une convention de rotation a une chance sur
    huit d'être juste, et se tromper ne casse rien : le surlignage tombe
    ailleurs, et personne ne s'en aperçoit avant qu'un propriétaire cherche sa
    cote au mauvais endroit.

    L'écart toléré couvre la différence entre la boîte du texte, qui inclut
    les approches de la police, et l'encre réelle des glyphes.
    """
    constat = lecture_pdf.lire(
        _ecrire(tmp_path, fabrique.page_tournee(rotation), f"r{rotation}.pdf")
    )

    (fragment,) = constat.fragments
    assert fragment.cadre is not None
    obtenu = (
        fragment.cadre.x0,
        fragment.cadre.y0,
        fragment.cadre.x1,
        fragment.cadre.y1,
    )
    for nom, valeur, attendu in zip(("x0", "y0", "x1", "y1"), obtenu, encre, strict=True):
        assert valeur == pytest.approx(attendu, abs=0.01), (
            f"/Rotate {rotation} : {nom} vaut {valeur:.3f}, l'encre est à {attendu:.3f}"
        )


def test_a_rotated_page_is_announced(tmp_path: Path) -> None:
    """La rotation est appliquée, et elle est DITE.

    Un surlignage qui tomberait à côté viendrait de là en premier : l'anomalie
    est ce qui permet de le chercher au bon endroit au lieu de soupçonner
    l'extraction.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_tournee(90), "tournee.pdf"))
    assert "pages_tournees" in [a.code for a in constat.anomalies]

    droite = lecture_pdf.lire(_ecrire(tmp_path, fabrique.une_page_avec_texte(), "droite.pdf"))
    assert "pages_tournees" not in [a.code for a in droite.anomalies], (
        "sans contre-exemple, une anomalie posée sur tous les documents "
        "passerait le test précédent et ne voudrait plus rien dire"
    )


def test_a_text_outside_the_page_keeps_its_text_and_loses_its_position(
    tmp_path: Path,
) -> None:
    """Hors de la page, l'emplacement est INCONNU — pas « au bord ».

    L'écraser sur un bord inventerait une position, et le propriétaire
    chercherait la cote là. Le texte, lui, reste : une échelle écrite dans un
    cartouche hors cadre vaut toujours d'être lue.
    """
    constat = lecture_pdf.lire(_ecrire(tmp_path, fabrique.page_avec_texte_hors_cadre(), "hors.pdf"))

    assert not constat.refuse
    (fragment,) = constat.fragments
    assert fragment.texte == fabrique.TEXTE_REFERENCE
    assert fragment.cadre is None
    assert fragment.position == "inconnue"
    assert "fragments_sans_position" in [a.code for a in constat.anomalies]


def test_a_fragment_inside_the_page_is_not_reported_as_clipped(
    tmp_path: Path,
) -> None:
    """Le contre-exemple des deux anomalies de position.

    Sans lui, un lecteur qui déclarerait « recadré » ou « hors cadre » tous
    les fragments passerait les tests ci-dessus, et les deux avertissements ne
    voudraient plus rien dire.
    """
    constat = lecture_pdf.lire(
        _ecrire(tmp_path, fabrique.page_avec_plusieurs_textes(), "dedans.pdf")
    )

    assert all(f.position == "exacte" for f in constat.fragments)
    codes = [a.code for a in constat.anomalies]
    assert "fragments_recadres" not in codes
    assert "fragments_sans_position" not in codes


# ---------------------------------------------------------------------------
# La tuile de détail : ce sans quoi « confirmer ou corriger » n'a pas de sens
# ---------------------------------------------------------------------------


def test_a_detail_tile_magnifies_a_fragment_enough_to_read_it(
    tmp_path: Path,
) -> None:
    """Un aperçu de page ne permet pas de RELIRE une cote ; une tuile si.

    Mesuré sur les quatre plans réels : à 2 000 pixels de grand côté, la
    hauteur médiane d'un texte est de 3,5 à 4,4 pixels. Or le propriétaire
    doit confirmer ou corriger une mesure, ce qui demande de la lire.

    La tuile vise `COTE_TUILE` sur son grand côté, et rend la hauteur que le
    texte atteint réellement — plutôt que de la supposer.
    """
    from metreo_api.services import rendu_pdf

    chemin = _ecrire(tmp_path, fabrique.page_avec_plusieurs_textes(), "plan.pdf")
    constat = lecture_pdf.lire(chemin)
    fragment = next(f for f in constat.fragments if f.texte == "5000")
    assert fragment.cadre is not None

    tuile = rendu_pdf.rendre_une_zone(
        chemin,
        page=fragment.page,
        zone=(
            fragment.cadre.x0,
            fragment.cadre.y0,
            fragment.cadre.x1,
            fragment.cadre.y1,
        ),
    )

    assert tuile.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert max(tuile.largeur, tuile.hauteur) <= rendu_pdf.COTE_TUILE
    assert tuile.hauteur_du_texte_px is not None
    assert tuile.hauteur_du_texte_px > 4.0, (
        "si la tuile ne rend pas le texte plus grand que l'aperçu, elle ne sert à rien"
    )

    # Et le plein aperçu, lui, ne prétend pas rendre un texte lisible.
    apercu = rendu_pdf.rendre(chemin)
    assert apercu.hauteur_du_texte_px is None


def test_a_tile_shows_the_drawing_around_the_text_not_only_the_text(
    tmp_path: Path,
) -> None:
    """La marge est prise sur la HAUTEUR du texte, pas sur la taille du cadre.

    **Vu à l'œil avant d'être écrit ici.** Avec une marge d'une demie du
    cadre, la tuile d'un « 1040 » d'un plan réel montrait « - 1040 E » et pas
    un trait du dessin : le propriétaire y lisait la cote sans pouvoir juger
    CE QU'ELLE cote, ce qui est précisément la question qu'on lui pose. (Ce
    « 1040 », d'ailleurs, était le code postal d'Etterbeek dans le cartouche.)

    La tuile doit donc être nettement plus large que le texte lui-même.
    """
    from metreo_api.services import rendu_pdf

    chemin = _ecrire(tmp_path, fabrique.page_avec_plusieurs_textes(), "plan.pdf")
    constat = lecture_pdf.lire(chemin)
    fragment = next(f for f in constat.fragments if f.texte == "5000")
    assert fragment.cadre is not None
    hauteur_du_cadre = fragment.cadre.y1 - fragment.cadre.y0

    tuile = rendu_pdf.rendre_une_zone(
        chemin,
        page=fragment.page,
        zone=(
            fragment.cadre.x0,
            fragment.cadre.y0,
            fragment.cadre.x1,
            fragment.cadre.y1,
        ),
    )
    assert tuile.hauteur_du_texte_px is not None

    # La hauteur montrée vaut au moins dix fois celle du texte : la marge est
    # de huit fois de chaque côté, donc dix-sept fois en tout, rognée par les
    # bords de la page.
    part_du_texte = tuile.hauteur_du_texte_px / tuile.hauteur
    assert part_du_texte < 0.1, (
        f"le texte occupe {part_du_texte:.0%} de la hauteur de la tuile : "
        "elle ne montre pas assez de dessin autour pour juger la cote"
    )
    assert hauteur_du_cadre > 0


def test_a_measurement_whose_position_is_unknown_cannot_be_shown(
    tmp_path: Path,
) -> None:
    """Pas de tuile sans position, et le refus le dit.

    C'est la conséquence directe de `position="inconnue"` : on ne fabrique pas
    une zone pour une mesure dont on ignore l'emplacement.
    """
    from metreo_api.services import rendu_pdf

    chemin = _ecrire(tmp_path, fabrique.une_page_avec_texte(), "plan.pdf")

    with pytest.raises(rendu_pdf.RenduRefuse) as refus:
        rendu_pdf.rendre_une_zone(chemin, page=1, zone=(0.5, 0.5, 0.5, 0.9))
    assert refus.value.code == "zone_invalide"
