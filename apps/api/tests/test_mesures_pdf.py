"""Calibrer un PDF, mesurer dessus, et dire ce que la mesure vaut.

Tous les cas de ce fichier ont une réponse connue **avant** l'exécution : une
calibration de 100 points pour 5 000 mm donne 50 mm par point, un carré de
50 × 50 points fait donc 2 500 × 2 500 mm, soit 6,25 m². Quand une assertion
tombe, c'est l'arithmétique qui est fausse — pas la fixture.

**Le sujet central n'est pas la valeur, c'est l'incertitude.** Une mesure
tirée d'un PDF ne peut pas être exacte : elle descend de deux clics. Ce que le
module doit garantir, c'est que le nombre rendu arrive avec de quoi juger s'il
est utilisable — dans la même unité que lui, et calculé depuis la résolution du
pointage plutôt que supposé.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from metreo_api.services import lecture_pdf, mesures_pdf
from metreo_api.services.mesures_pdf import Calibration, MesureRefusee, Point

#: Une page de 200 × 100 points, à l'origine. La même que la fixture du lecteur.
PAGE = (0.0, 0.0, 200.0, 100.0)

#: Cent points pour cinq mètres : un facteur de 50 mm par point, soit ce que
#: donnerait un plan au 1:50 imprimé à l'échelle. Rond exprès — une assertion
#: doit se vérifier de tête.
CENT_POINTS_POUR_CINQ_METRES = Calibration(
    premier=Point(0.0, 0.0),
    second=Point(100.0, 0.0),
    distance_reelle=Decimal("5000"),
    unite="mm",
    resolution_du_pointage=0.5,
)


# ---------------------------------------------------------------------------
# Le repère : l'aller-retour avec le lecteur
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("relatif", [(0.0, 0.0), (1.0, 1.0), (0.1, 0.8), (0.75, 0.05), (0.5, 0.5)])
def test_the_screen_to_page_conversion_is_the_exact_inverse_of_the_reader(
    rotation: int, relatif: tuple[float, float]
) -> None:
    """Un point de la page, mis à l'écran puis ramené, revient où il était.

    **Les deux tables de rotation vivent dans deux modules**, et rien dans le
    code n'oblige la seconde à être l'inverse de la première. Une faute de
    signe dans l'une des huit lignes ne casse rien de visible : elle déplace
    une mesure, et la mesure reste un nombre plausible.

    Le test compare donc directement aux deux tables, `_VERS_L_ECRAN` d'un côté
    et `_DEPUIS_L_ECRAN` de l'autre — y compris sur les COINS, où une inversion
    mal écrite se confond avec une inversion juste au centre de la page.

    Passer par `lecture_pdf.normaliser` serait moins direct ET moins complet :
    cette fonction rogne ce qui dépasse, et un point de coin en ressortirait
    « recadré » plutôt que transformé. Le rognage est juste là-bas — il situe
    un texte — et il n'a rien à voir avec ce qui est vérifié ici.
    """
    a, b = relatif
    gauche, bas, droite, haut = PAGE
    attendu_u = gauche + a * (droite - gauche)
    attendu_v = bas + b * (haut - bas)

    # Aller : le repère de la page vers celui de l'écran, par la table du lecteur.
    x, y = lecture_pdf._VERS_L_ECRAN[rotation](a, b)  # type: ignore[operator]
    # Retour.
    revenu = mesures_pdf.vers_la_page((x, y), PAGE, rotation)

    assert math.hypot(revenu.u - attendu_u, revenu.v - attendu_v) < 1e-9, (
        f"/Rotate {rotation} : ({attendu_u}, {attendu_v}) est passé par "
        f"l'écran en ({x:.4f}, {y:.4f}) et est revenu en "
        f"({revenu.u:.4f}, {revenu.v:.4f})"
    )


def test_a_page_whose_box_does_not_start_at_zero_is_handled() -> None:
    """Le décalage de la boîte est rendu, comme il a été retiré.

    Même défaut que côté lecture : `get_size()` ne dit pas où la page commence,
    et un point désigné à l'écran doit revenir en coordonnées ABSOLUES de la
    page — sans quoi la mesure serait juste et son emplacement faux.
    """
    boite = (100.0, 50.0, 300.0, 150.0)
    # Le centre de l'écran, c'est le centre de la page : (200, 100).
    centre = mesures_pdf.vers_la_page((0.5, 0.5), boite, 0)
    assert centre.u == pytest.approx(200.0)
    assert centre.v == pytest.approx(100.0)


def test_an_impossible_rotation_is_refused_rather_than_guessed() -> None:
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.vers_la_page((0.5, 0.5), PAGE, 45)
    assert refus.value.code == "rotation_inconnue"


def test_a_page_without_dimensions_is_refused() -> None:
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.vers_la_page((0.5, 0.5), (0.0, 0.0, 0.0, 0.0), 0)
    assert refus.value.code == "page_sans_dimension"


# ---------------------------------------------------------------------------
# La calibration
# ---------------------------------------------------------------------------


def test_the_factor_is_the_real_distance_over_the_pointed_one() -> None:
    """Cent points pour cinq mètres : cinquante millimètres par point."""
    assert mesures_pdf.facteur(CENT_POINTS_POUR_CINQ_METRES) == Decimal("50")


def test_the_factor_does_not_depend_on_the_direction_of_the_calibration() -> None:
    """Calibrer de gauche à droite ou en diagonale donne le même facteur.

    Une calibration oblique de 100 points — 60/80 — doit rendre exactement le
    même facteur qu'une horizontale de 100 points. Un module qui ne mesurerait
    qu'une projection sur un axe rendrait 83,3 au lieu de 50.
    """
    oblique = Calibration(
        premier=Point(0.0, 0.0),
        second=Point(60.0, 80.0),
        distance_reelle=Decimal("5000"),
        unite="mm",
    )
    assert mesures_pdf.facteur(oblique) == Decimal("50")


def test_a_calibration_too_short_to_determine_anything_is_refused() -> None:
    """En deçà du seuil, le facteur n'est pas incertain : il n'a pas de sens.

    Le refus porte une explication qui dit QUOI FAIRE — désigner deux points
    plus éloignés — parce qu'un refus qui ne dit pas comment s'en sortir est
    un cul-de-sac.
    """
    trop_courte = Calibration(
        premier=Point(0.0, 0.0),
        second=Point(5.0, 0.0),
        distance_reelle=Decimal("250"),
        unite="mm",
    )
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.facteur(trop_courte)
    assert refus.value.code == "calibration_trop_courte"
    assert "plus éloignés" in refus.value.message


def test_a_calibration_at_the_threshold_is_accepted() -> None:
    """Le contre-exemple : sans lui, un seuil infini passerait le test ci-dessus."""
    juste_assez = Calibration(
        premier=Point(0.0, 0.0),
        second=Point(mesures_pdf.CALIBRATION_MINIMALE_EN_POINTS, 0.0),
        distance_reelle=Decimal("500"),
        unite="mm",
    )
    assert mesures_pdf.facteur(juste_assez) > 0


@pytest.mark.parametrize("distance", [Decimal("0"), Decimal("-100")])
def test_a_non_positive_real_distance_is_refused(distance: Decimal) -> None:
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.facteur(Calibration(Point(0.0, 0.0), Point(100.0, 0.0), distance, "mm"))
    assert refus.value.code == "distance_non_positive"


def test_a_unit_that_is_not_a_length_is_refused() -> None:
    """Une calibration déclare une DISTANCE. « m2 » ou « kg » n'en sont pas.

    Le contrôle passe par `get_unit`, donc par la table du dépôt : une unité
    inconnue lève `UnknownUnitError` plus bas, et une unité connue mais d'une
    autre dimension est refusée ici en la nommant.
    """
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.facteur(Calibration(Point(0.0, 0.0), Point(100.0, 0.0), Decimal("5"), "m2"))
    assert refus.value.code == "unite_non_lineaire"


# ---------------------------------------------------------------------------
# Les longueurs
# ---------------------------------------------------------------------------


def test_a_segment_is_measured_in_the_unit_that_was_calibrated() -> None:
    """Calibré en millimètres, rendu en millimètres. Aucune conversion.

    Convertir imposerait un choix que personne n'a fait, et couperait le lien
    direct entre ce que la personne a saisi et ce qu'elle relit.
    """
    mesure = mesures_pdf.longueur([Point(0.0, 0.0), Point(0.0, 50.0)], CENT_POINTS_POUR_CINQ_METRES)
    assert mesure.valeur == Decimal("2500")
    assert mesure.unite == "mm"


def test_a_polyline_adds_its_segments() -> None:
    """Trois points, deux segments, et la somme des deux."""
    mesure = mesures_pdf.longueur(
        [Point(0.0, 0.0), Point(30.0, 40.0), Point(30.0, 90.0)],
        CENT_POINTS_POUR_CINQ_METRES,
    )
    # 50 points puis 50 points, à 50 mm le point : 5 000 mm.
    assert mesure.valeur == Decimal("5000")


def test_a_single_point_measures_nothing_and_says_so() -> None:
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.longueur([Point(0.0, 0.0)], CENT_POINTS_POUR_CINQ_METRES)
    assert refus.value.code == "segment_incomplet"


def test_two_identical_points_are_refused_rather_than_measured_as_zero() -> None:
    """Zéro n'est pas une longueur : c'est l'absence de longueur.

    La rendre comme une mesure ferait apparaître une quantité nulle dans un
    métré, où elle serait indiscernable d'un ouvrage réellement absent.
    """
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.longueur([Point(10.0, 10.0), Point(10.0, 10.0)], CENT_POINTS_POUR_CINQ_METRES)
    assert refus.value.code == "longueur_nulle"


# ---------------------------------------------------------------------------
# Les surfaces
# ---------------------------------------------------------------------------


def test_a_square_has_the_area_it_should() -> None:
    """50 × 50 points à 50 mm le point : 2 500 × 2 500 mm, soit 6,25 m²."""
    mesure = mesures_pdf.aire(
        [Point(0.0, 0.0), Point(50.0, 0.0), Point(50.0, 50.0), Point(0.0, 50.0)],
        CENT_POINTS_POUR_CINQ_METRES,
    )
    assert mesure.valeur == Decimal("6.25")
    assert mesure.unite == "m2"


def test_a_triangle_has_half_the_area_of_its_rectangle() -> None:
    """La formule du lacet, sur un cas où la réponse se calcule de tête."""
    mesure = mesures_pdf.aire(
        [Point(0.0, 0.0), Point(50.0, 0.0), Point(0.0, 50.0)],
        CENT_POINTS_POUR_CINQ_METRES,
    )
    assert mesure.valeur == Decimal("3.125")


def test_the_area_does_not_depend_on_the_drawing_direction() -> None:
    """Un contour tracé à l'envers rend la même aire.

    La formule du lacet rend un nombre NÉGATIF dans un sens. Le sens du tracé
    n'est pas une information que l'utilisateur a voulu donner — il tourne
    comme sa main va.
    """
    horaire = [Point(0.0, 0.0), Point(0.0, 50.0), Point(50.0, 50.0), Point(50.0, 0.0)]
    antihoraire = list(reversed(horaire))

    assert (
        mesures_pdf.aire(horaire, CENT_POINTS_POUR_CINQ_METRES).valeur
        == mesures_pdf.aire(antihoraire, CENT_POINTS_POUR_CINQ_METRES).valeur
    )


def test_the_area_does_not_depend_on_which_corner_was_clicked_first() -> None:
    """Commencer par un autre sommet donne le même contour, donc la même aire."""
    depart = [Point(0.0, 0.0), Point(50.0, 0.0), Point(50.0, 50.0), Point(0.0, 50.0)]
    decale = depart[2:] + depart[:2]

    assert (
        mesures_pdf.aire(depart, CENT_POINTS_POUR_CINQ_METRES).valeur
        == mesures_pdf.aire(decale, CENT_POINTS_POUR_CINQ_METRES).valeur
    )


def test_the_same_shape_calibrated_in_metres_or_millimetres_has_the_same_area() -> None:
    """L'invariant qui attrape une conversion fausse.

    Cinq mètres et cinq mille millimètres sont la même distance. Les deux
    calibrations doivent donc rendre exactement la même surface — et c'est le
    seul test qui tombe si le facteur de conversion vers le mètre carré est
    écrit à la main plutôt que tiré de `units.py`.
    """
    en_metres = Calibration(
        premier=Point(0.0, 0.0),
        second=Point(100.0, 0.0),
        distance_reelle=Decimal("5"),
        unite="m",
    )
    contour = [Point(0.0, 0.0), Point(50.0, 0.0), Point(50.0, 50.0), Point(0.0, 50.0)]

    assert (
        mesures_pdf.aire(contour, en_metres).valeur
        == mesures_pdf.aire(contour, CENT_POINTS_POUR_CINQ_METRES).valeur
    )


def test_fewer_than_three_points_enclose_nothing() -> None:
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.aire([Point(0.0, 0.0), Point(50.0, 0.0)], CENT_POINTS_POUR_CINQ_METRES)
    assert refus.value.code == "contour_incomplet"


def test_three_collinear_points_enclose_nothing_and_it_is_said() -> None:
    """Alignés, ils n'enferment aucune surface — et zéro n'est pas une réponse."""
    with pytest.raises(MesureRefusee) as refus:
        mesures_pdf.aire(
            [Point(0.0, 0.0), Point(50.0, 0.0), Point(100.0, 0.0)],
            CENT_POINTS_POUR_CINQ_METRES,
        )
    assert refus.value.code == "surface_nulle"


def test_a_contour_that_crosses_itself_is_flagged_not_corrected() -> None:
    """Un « nœud papillon » : la formule rend un nombre, qui ne veut rien dire.

    Deviner ce que l'utilisateur voulait dessiner n'appartient pas à ce module.
    La réserve est posée, la mesure part « à vérifier », et c'est à l'écran de
    proposer de reprendre le tracé.
    """
    # Dissymétrique exprès : un nœud papillon PARFAIT a deux lobes égaux de
    # signes opposés, dont la somme est nulle — il serait refusé comme
    # « surface nulle » avant même d'être examiné, et le test ne prouverait
    # rien de la détection de croisement.
    noeud = [Point(0.0, 0.0), Point(50.0, 50.0), Point(50.0, 0.0), Point(0.0, 30.0)]
    mesure = mesures_pdf.aire(noeud, CENT_POINTS_POUR_CINQ_METRES)

    assert mesure.valeur > 0, "la mesure est rendue, avec sa réserve"
    assert "contour_qui_se_recoupe" in mesure.reserves
    assert mesure.fiabilite == "a_confirmer", (
        "une réserve posée sur une mesure annoncée « mesurable » laisserait "
        "l'écran afficher un nombre comme sûr avec, juste à côté, la raison de "
        "ne pas s'y fier"
    )


def test_a_simple_contour_is_not_flagged_as_crossing_itself() -> None:
    """Le contre-exemple : sans lui, un détecteur qui dit « oui » à tout passe.

    **Pourquoi ce test nomme désormais la réserve au lieu d'exiger une liste
    vide.** Il en exigeait une, ce qui lui faisait vérifier deux choses à la
    fois : qu'aucun croisement n'est signalé, et que l'incertitude reste sous
    le seuil. La seconde n'est pas son sujet, et elle a changé de réponse le
    jour où l'erreur de pointage d'une aire a cessé d'être approximée par le
    périmètre : sur ce L, un demi-point d'erreur sur un sommet déplace l'aire
    de 1,9 % — l'ancienne approximation annonçait 0,3 %. La mesure porte donc
    maintenant « incertitude_elevee », et c'est juste.
    """
    carre = [Point(0.0, 0.0), Point(50.0, 0.0), Point(50.0, 50.0), Point(0.0, 50.0)]
    assert (
        "contour_qui_se_recoupe"
        not in mesures_pdf.aire(carre, CENT_POINTS_POUR_CINQ_METRES).reserves
    )

    # Et une forme concave non plus : un L se trace sans se croiser.
    forme_en_l = [
        Point(0.0, 0.0),
        Point(60.0, 0.0),
        Point(60.0, 20.0),
        Point(20.0, 20.0),
        Point(20.0, 60.0),
        Point(0.0, 60.0),
    ]
    assert (
        "contour_qui_se_recoupe"
        not in mesures_pdf.aire(forme_en_l, CENT_POINTS_POUR_CINQ_METRES).reserves
    )


# ---------------------------------------------------------------------------
# L'incertitude — le vrai sujet
# ---------------------------------------------------------------------------


def test_the_uncertainty_is_given_in_the_same_unit_as_the_value() -> None:
    """Pas en pourcentage.

    Une incertitude relative obligerait le lecteur à faire une multiplication
    pour savoir si le nombre lui convient. Cette multiplication ne se fait pas,
    et la réserve passe inaperçue.
    """
    mesure = mesures_pdf.longueur([Point(0.0, 0.0), Point(0.0, 50.0)], CENT_POINTS_POUR_CINQ_METRES)
    assert mesure.incertitude > 0
    assert mesure.incertitude_relative > 0
    # Les deux disent la même chose, et doivent concorder.
    assert mesure.incertitude == pytest.approx(
        mesure.valeur * mesure.incertitude_relative, rel=Decimal("0.001")
    )


def test_a_finer_pointing_gives_a_proportionally_smaller_uncertainty() -> None:
    """Pointer deux fois plus fin, se tromper deux fois moins.

    C'est ce qui justifie la tuile agrandie : mesuré, un pixel de l'aperçu
    pleine page vaut 12 à 42 mm d'ouvrage, contre 0,6 à 6 mm sur une tuile.
    """
    grossier = Calibration(
        Point(0.0, 0.0),
        Point(100.0, 0.0),
        Decimal("5000"),
        "mm",
        resolution_du_pointage=1.0,
    )
    fin = Calibration(
        Point(0.0, 0.0),
        Point(100.0, 0.0),
        Decimal("5000"),
        "mm",
        resolution_du_pointage=0.5,
    )
    trace = [Point(0.0, 0.0), Point(0.0, 50.0)]

    large = mesures_pdf.longueur(trace, grossier).incertitude_relative
    serree = mesures_pdf.longueur(trace, fin).incertitude_relative

    assert float(serree) == pytest.approx(float(large) / 2, rel=0.01)


def test_calibrating_on_a_short_span_poisons_every_measurement() -> None:
    """**La propriété qui justifie tout le reste.**

    Deux calibrations également valides, l'une posée sur 20 points, l'autre
    sur 200. La seconde rend la même valeur avec une incertitude bien moindre —
    et c'est pourquoi l'écran doit conseiller de calibrer sur une cote longue.

    Sans ce test, rien n'empêcherait l'incertitude d'ignorer l'écart de
    calibration, et le conseil ne serait qu'une opinion dans une docstring.
    """
    courte = Calibration(
        Point(0.0, 0.0),
        Point(20.0, 0.0),
        Decimal("1000"),
        "mm",
        resolution_du_pointage=0.5,
    )
    longue = Calibration(
        Point(0.0, 0.0),
        Point(200.0, 0.0),
        Decimal("10000"),
        "mm",
        resolution_du_pointage=0.5,
    )
    trace = [Point(0.0, 0.0), Point(0.0, 50.0)]

    sur_courte = mesures_pdf.longueur(trace, courte)
    sur_longue = mesures_pdf.longueur(trace, longue)

    # Les deux calibrations décrivent la même échelle : même valeur.
    assert sur_courte.valeur == sur_longue.valeur
    # Mais pas la même confiance.
    assert sur_courte.incertitude_relative > sur_longue.incertitude_relative


def test_an_area_is_twice_as_uncertain_as_the_length_it_comes_from() -> None:
    """Une aire va comme le carré d'une longueur, son erreur relative double.

    L'oublier annoncerait une surface deux fois plus sûre qu'elle ne l'est — et
    une surface entre directement dans un métré.
    """
    cote = [Point(0.0, 0.0), Point(50.0, 0.0), Point(50.0, 50.0), Point(0.0, 50.0)]
    surface = mesures_pdf.aire(cote, CENT_POINTS_POUR_CINQ_METRES)
    # Le même périmètre, parcouru comme une ligne brisée fermée.
    ligne = mesures_pdf.longueur([*cote, cote[0]], CENT_POINTS_POUR_CINQ_METRES)

    assert float(surface.incertitude_relative) == pytest.approx(
        2 * float(ligne.incertitude_relative), rel=0.01
    )


def test_an_uncertain_measurement_stays_to_be_verified_and_names_why() -> None:
    """Au-delà du seuil, la mesure est conservée — jamais écartée — et réservée.

    L'écarter priverait le propriétaire d'un ordre de grandeur qui lui est
    peut-être utile ; la présenter comme sûre lui ferait signer un nombre que
    personne n'a vérifié.
    """
    au_jugé = Calibration(
        Point(0.0, 0.0),
        Point(30.0, 0.0),
        Decimal("1500"),
        "mm",
        resolution_du_pointage=2.0,
    )
    mesure = mesures_pdf.longueur([Point(0.0, 0.0), Point(0.0, 20.0)], au_jugé)

    assert mesure.valeur > 0, "la mesure est conservée"
    assert mesure.fiabilite == "a_confirmer"
    assert "incertitude_elevee" in mesure.reserves


def test_a_well_pointed_measurement_carries_no_reserve() -> None:
    """Le contre-exemple du précédent.

    Sans lui, un module qui réserverait TOUTE mesure passerait le test
    ci-dessus, et « à vérifier » perdrait son sens en s'appliquant à tout.
    """
    soigneux = Calibration(
        Point(0.0, 0.0),
        Point(1000.0, 0.0),
        Decimal("50000"),
        "mm",
        resolution_du_pointage=0.05,
    )
    mesure = mesures_pdf.longueur([Point(0.0, 0.0), Point(0.0, 500.0)], soigneux)

    assert mesure.fiabilite == "mesurable"
    assert mesure.reserves == ()


def test_an_undeclared_pointing_resolution_is_treated_pessimistically() -> None:
    """Qui ne dit pas à quelle finesse il a pointé obtient le doute, pas le crédit.

    Supposer un pointage fin présenterait comme sûre une mesure prise au jugé.
    Le défaut va donc dans l'autre sens, et la valeur par défaut vaut à peu
    près l'aperçu pleine page d'un A0.
    """
    sans_declaration = Calibration(Point(0.0, 0.0), Point(100.0, 0.0), Decimal("5000"), "mm")
    assert sans_declaration.resolution_du_pointage == mesures_pdf.RESOLUTION_PAR_DEFAUT_EN_POINTS

    mesure = mesures_pdf.longueur([Point(0.0, 0.0), Point(0.0, 30.0)], sans_declaration)
    assert mesure.fiabilite == "a_confirmer"
