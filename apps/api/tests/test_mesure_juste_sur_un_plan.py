"""Mesurer un plan dont on connaît les dimensions, et comparer.

**Ce que ce fichier éprouve, et qu'aucun autre n'éprouve.**

Les tests de `test_mesures_pdf.py` vérifient l'arithmétique sur des points
posés à la main : deux points distants de 100 unités font 100 unités. C'est
nécessaire et ce n'est pas suffisant — on peut passer tous ces tests en
inversant un axe, en divisant par la mauvaise dimension, ou en calibrant sur
une longueur qui n'est pas celle qu'on croit.

Ici, le point de départ n'est pas une liste de coordonnées : c'est **un dessin**
— `fabriquer_pdf_de_test.plan_de_batiment()` — dont la géométrie est posée en
points de papier et dont les dimensions réelles se déduisent d'une échelle
déclarée. Le chemin éprouvé est donc le chemin complet :

    géométrie du dessin → coordonnées d'écran (ce que l'écran envoie)
      → `vers_la_page` → calibration sur une COTE DU DESSIN
      → mesure → comparaison à une vérité qui ne vient pas de Metreo.

La cote de référence fait 5 000 mm, la pièce 6 000 × 4 000 mm, soit 24,00 m² —
trois nombres écrits dans la fabrique, jamais lus sur le dessin ni rendus par
le lecteur.

**Et ce que ces tests ne prouvent toujours pas** : que Metreo mesure juste sur
un plan d'exécution réel. Ils prouvent que la chaîne de calcul est juste sur une
géométrie connue. L'écart entre les deux est l'erreur de POINTAGE d'un humain
sur un vrai dessin, et elle se mesure sur de vraies cotes — voir
`docs/COTES_DE_REFERENCE.md`.
"""

from __future__ import annotations

import io
import math
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from metreo_api.services import mesures_pdf

pdfium = pytest.importorskip("pypdfium2", reason="l'extra `pdf` n'est pas installé")

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import fabriquer_pdf_de_test as fabrique  # noqa: E402

#: La résolution du pointage que l'écran déclare quand on pointe dans la loupe.
#:
#: Calculée comme l'écran la calcule — `resolution_de_la_loupe` ci-dessous —
#: et non posée à la main : une valeur inventée ici éprouverait une autre
#: chaîne que celle qui tourne.
TAILLE_DE_LA_LOUPE = 0.05
COTE_DE_LA_TUILE = 512


def resolution_de_la_loupe(largeur_pt: float, hauteur_pt: float) -> float:
    """Points de page par pixel de loupe — la règle du rendu, pas une supposition.

    `rendu_pdf.tuile` ajuste son facteur sur le **plus grand côté** de la zone
    demandée. La zone étant carrée en coordonnées normalisées, son plus grand
    côté en POINTS est celui du plus grand côté de la page. C'est pourquoi
    cette fonction prend les deux dimensions et non la seule largeur : la
    prendre sur la largeur donne un nombre trop petit sur une page en portrait,
    et annonce une mesure plus sûre qu'elle ne l'est.
    """
    return TAILLE_DE_LA_LOUPE * max(largeur_pt, hauteur_pt) / COTE_DE_LA_TUILE


def page_du_plan(octets: bytes) -> tuple[tuple[float, float, float, float], int]:
    """La boîte affichée et la rotation, lues par le même lecteur que l'API."""
    document = pdfium.PdfDocument(io.BytesIO(octets))
    page = document[0]
    largeur, hauteur = (float(valeur) for valeur in page.get_size())
    return (0.0, 0.0, largeur, hauteur), page.get_rotation()


def vers_l_ecran(
    point: tuple[float, float],
    boite: tuple[float, float, float, float],
    rotation: int,
) -> tuple[float, float]:
    """Du repère de la page à celui de l'écran — ce que fait le NAVIGATEUR.

    Écrit ici à l'endroit, et non importé de `lecture_pdf` : le test doit
    pouvoir échouer si les deux conversions cessent d'être inverses l'une de
    l'autre. Les importer toutes les deux rendrait l'aller-retour vrai par
    construction, et c'est précisément ce qu'on veut éprouver.
    """
    gauche, bas, droite, haut = boite
    a = (point[0] - gauche) / (droite - gauche)
    b = (point[1] - bas) / (haut - bas)
    return {
        0: (a, 1.0 - b),
        90: (b, a),
        180: (1.0 - a, b),
        270: (1.0 - b, 1.0 - a),
    }[rotation % 360]


def calibration_sur_la_cote(
    octets: bytes, *, distance_reelle: str = "5000", unite: str = "mm"
) -> mesures_pdf.Calibration:
    """Calibrer en pointant les DEUX EXTRÉMITÉS de la ligne de cote du dessin.

    C'est le geste du métreur, et c'est aussi ce qui rend ce test sévère : le
    facteur n'est pas donné, il est DÉDUIT de deux points pointés sur le
    dessin. Une erreur de repère le fausserait, et toutes les mesures qui
    suivent avec lui.
    """
    boite, rotation = page_du_plan(octets)
    largeur, hauteur = boite[2] - boite[0], boite[3] - boite[1]
    premier = mesures_pdf.vers_la_page(
        vers_l_ecran((fabrique.COTE_X0, fabrique.COTE_Y), boite, rotation), boite, rotation
    )
    second = mesures_pdf.vers_la_page(
        vers_l_ecran((fabrique.COTE_X1, fabrique.COTE_Y), boite, rotation), boite, rotation
    )
    return mesures_pdf.Calibration(
        premier=premier,
        second=second,
        distance_reelle=Decimal(distance_reelle),
        unite=unite,
        resolution_du_pointage=resolution_de_la_loupe(largeur, hauteur),
    )


def points_de_la_page(octets: bytes, sommets: list[tuple[float, float]]) -> list[mesures_pdf.Point]:
    """Des sommets du DESSIN, passés par l'écran puis ramenés dans la page."""
    boite, rotation = page_du_plan(octets)
    return [
        mesures_pdf.vers_la_page(vers_l_ecran(sommet, boite, rotation), boite, rotation)
        for sommet in sommets
    ]


#: Les quatre coins de la pièce, dans l'ordre du contour.
COINS_DE_LA_PIECE: list[tuple[float, float]] = [
    (fabrique.PIECE_X0, fabrique.PIECE_Y0),
    (fabrique.PIECE_X1, fabrique.PIECE_Y0),
    (fabrique.PIECE_X1, fabrique.PIECE_Y1),
    (fabrique.PIECE_X0, fabrique.PIECE_Y1),
]


# ---------------------------------------------------------------------------
# La longueur
# ---------------------------------------------------------------------------


def test_la_facade_mesuree_vaut_ce_que_le_dessin_porte() -> None:
    """6 000 mm attendus, et c'est la fabrique qui le dit, pas le lecteur."""
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)
    mur = points_de_la_page(octets, COINS_DE_LA_PIECE[:2])

    mesure = mesures_pdf.longueur(mur, calibration)

    attendu = Decimal(str(fabrique.LARGEUR_DE_LA_PIECE_EN_MM))
    assert mesure.unite == "mm"
    ecart = abs(mesure.valeur - attendu)
    assert ecart <= attendu / 1000, (
        f"la façade devrait mesurer {attendu} mm et Metreo rend {mesure.valeur} — "
        f"écart de {ecart} mm"
    )


def test_l_incertitude_annoncee_couvre_l_ecart_reellement_commis() -> None:
    """La propriété qui compte vraiment : le ± n'est pas décoratif.

    Une mesure juste assortie d'une incertitude trop petite est pire qu'une
    mesure fausse : elle se présente comme sûre. Ce test ne regarde donc pas
    seulement la valeur, il regarde si l'intervalle annoncé CONTIENT la vérité.
    """
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)
    mesure = mesures_pdf.longueur(points_de_la_page(octets, COINS_DE_LA_PIECE[:2]), calibration)

    attendu = Decimal(str(fabrique.LARGEUR_DE_LA_PIECE_EN_MM))
    assert abs(mesure.valeur - attendu) <= mesure.incertitude, (
        f"{mesure.valeur} ± {mesure.incertitude} n'encadre pas {attendu}"
    )


def test_une_mesure_prise_a_la_loupe_sur_ce_plan_est_annoncee_mesurable() -> None:
    """Pointée à la résolution de la loupe, la mesure ne porte aucune réserve."""
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)
    mesure = mesures_pdf.longueur(points_de_la_page(octets, COINS_DE_LA_PIECE[:2]), calibration)

    assert mesure.fiabilite == "mesurable"
    assert mesure.reserves == ()


# ---------------------------------------------------------------------------
# La surface
# ---------------------------------------------------------------------------


def test_le_contour_de_la_piece_rend_sa_surface_en_metres_carres() -> None:
    """24,00 m² attendus — 6 000 × 4 000 mm, calculés par la fabrique."""
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)
    contour = points_de_la_page(octets, COINS_DE_LA_PIECE)

    mesure = mesures_pdf.aire(contour, calibration)

    attendu = Decimal(str(fabrique.SURFACE_DE_LA_PIECE_EN_M2))
    assert mesure.unite == "m2"
    assert abs(mesure.valeur - attendu) <= attendu / 500, (
        f"la pièce devrait faire {attendu} m² et Metreo rend {mesure.valeur}"
    )


def test_la_surface_annonce_une_incertitude_double_de_celle_d_une_longueur() -> None:
    """Une aire va comme le carré d'une longueur : son relatif est doublé.

    Éprouvé sur le MÊME plan et la MÊME calibration que la longueur ci-dessus,
    sans quoi le rapport entre les deux ne voudrait rien dire.
    """
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)

    longueur = mesures_pdf.longueur(points_de_la_page(octets, COINS_DE_LA_PIECE[:2]), calibration)
    surface = mesures_pdf.aire(points_de_la_page(octets, COINS_DE_LA_PIECE), calibration)

    # Pas exactement deux fois : le terme de tracé n'a pas la même longueur
    # caractéristique — un périmètre contre un segment. Le facteur, lui, est
    # strictement doublé, et il domine ici.
    assert surface.incertitude_relative > longueur.incertitude_relative
    assert surface.incertitude_relative < longueur.incertitude_relative * 3


def test_le_contour_parcouru_en_sens_horaire_rend_la_meme_aire() -> None:
    """Le sens du tracé n'est pas une information que l'utilisateur a voulu donner."""
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)

    direct = mesures_pdf.aire(points_de_la_page(octets, COINS_DE_LA_PIECE), calibration)
    inverse = mesures_pdf.aire(
        points_de_la_page(octets, list(reversed(COINS_DE_LA_PIECE))), calibration
    )

    assert direct.valeur == inverse.valeur


# ---------------------------------------------------------------------------
# Le cas PORTRAIT, que les quatre plans du propriétaire n'éprouvent pas
# ---------------------------------------------------------------------------


def test_le_meme_plan_en_portrait_rend_les_memes_longueurs() -> None:
    """La géométrie ne dépend pas de l'orientation de la feuille.

    Le même dessin sur une page plus haute que large : les points de papier
    sont les mêmes, donc les millimètres aussi. Un lecteur qui diviserait par
    la mauvaise dimension le ferait voir ici, et nulle part ailleurs — les
    quatre plans du propriétaire sont tous en paysage.
    """
    paysage = fabrique.plan_de_batiment()
    portrait = fabrique.plan_de_batiment_en_portrait()

    en_paysage = mesures_pdf.longueur(
        points_de_la_page(paysage, COINS_DE_LA_PIECE[:2]), calibration_sur_la_cote(paysage)
    )
    en_portrait = mesures_pdf.longueur(
        points_de_la_page(portrait, COINS_DE_LA_PIECE[:2]), calibration_sur_la_cote(portrait)
    )

    attendu = Decimal(str(fabrique.LARGEUR_DE_LA_PIECE_EN_MM))
    assert abs(en_paysage.valeur - attendu) <= attendu / 1000
    assert abs(en_portrait.valeur - attendu) <= attendu / 1000


def test_le_portrait_n_annonce_pas_une_mesure_plus_sure_que_le_paysage() -> None:
    """Le défaut mesuré, et le seul moyen de le voir.

    L'écran déclarait sa résolution depuis la LARGEUR de la page. Sur le même
    dessin en portrait, la largeur est plus petite — la résolution annoncée
    était donc 1,41 fois trop fine, et l'incertitude 1,41 fois trop petite.

    La page portant la même géométrie, à la même échelle, une mesure ne peut
    pas y être annoncée plus sûre. Ce test échoue si la règle de résolution
    redevient « la largeur ».
    """
    paysage = fabrique.plan_de_batiment()
    portrait = fabrique.plan_de_batiment_en_portrait()

    en_paysage = mesures_pdf.longueur(
        points_de_la_page(paysage, COINS_DE_LA_PIECE[:2]), calibration_sur_la_cote(paysage)
    )
    en_portrait = mesures_pdf.longueur(
        points_de_la_page(portrait, COINS_DE_LA_PIECE[:2]), calibration_sur_la_cote(portrait)
    )

    assert en_portrait.incertitude_relative == en_paysage.incertitude_relative, (
        "la même géométrie sur une feuille tournée ne peut pas être mieux connue : "
        f"paysage {en_paysage.incertitude_relative}, portrait "
        f"{en_portrait.incertitude_relative}"
    )


# ---------------------------------------------------------------------------
# Le nombre de sommets
# ---------------------------------------------------------------------------


def _polyligne_en_zigzag(
    sommets: int, *, longueur_en_points: float = 200.0
) -> list[tuple[float, float]]:
    """Un tracé ANGULEUX de longueur totale constante, à `sommets` sommets.

    Longueur constante, et c'est tout l'objet : si l'incertitude de tracé ne
    dépendait que de la longueur, découper le même parcours en dix fois plus de
    segments ne changerait rien. Or chaque sommet ajoute son erreur de pointage,
    et un tracé anguleux ne les compense pas.
    """
    pas = longueur_en_points / max(sommets - 1, 1)
    base_x, base_y = fabrique.PIECE_X0, fabrique.PIECE_Y0 + 20.0
    # Le zigzag est tracé en diagonale pour que chaque segment ait la même
    # longueur que le pas, à la composante verticale près — négligeable ici.
    return [
        (base_x + index * pas, base_y + (4.0 if index % 2 else 0.0)) for index in range(sommets)
    ]


@pytest.mark.parametrize("sommets", [2, 5, 20, 50])
def test_l_incertitude_de_trace_croit_avec_le_nombre_de_sommets(sommets: int) -> None:
    """Dix sommets pointés valent dix erreurs, pas deux.

    Le défaut corrigé : `du_trace` valait `√2·ε / L`, c'est-à-dire la formule
    d'un segment à DEUX extrémités, quel que soit le nombre de points posés.
    Un relevé de façade en vingt clics était annoncé aussi sûr qu'un segment
    droit de même longueur.
    """
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)

    deux = mesures_pdf.longueur(points_de_la_page(octets, _polyligne_en_zigzag(2)), calibration)
    beaucoup = mesures_pdf.longueur(
        points_de_la_page(octets, _polyligne_en_zigzag(sommets)), calibration
    )

    if sommets == 2:
        assert beaucoup.incertitude_relative == deux.incertitude_relative
    else:
        assert beaucoup.incertitude_relative > deux.incertitude_relative, (
            f"{sommets} sommets annoncent la même incertitude que 2 : "
            f"{beaucoup.incertitude_relative}"
        )


def test_la_croissance_suit_la_racine_du_nombre_de_sommets() -> None:
    """Et elle la suit de la bonne façon : en quadrature, pas linéairement.

    Des erreurs indépendantes s'ajoutent en quadrature. Quadrupler le nombre de
    sommets doit donc à peu près doubler le terme de tracé — pas le
    quadrupler, ce qui serait la somme d'erreurs toutes dans le même sens, et
    pas le laisser inchangé, ce qui était le défaut.
    """
    octets = fabrique.plan_de_batiment()
    calibration = calibration_sur_la_cote(octets)
    epsilon = calibration.resolution_du_pointage

    def terme_de_trace(sommets: int) -> float:
        mesure = mesures_pdf.longueur(
            points_de_la_page(octets, _polyligne_en_zigzag(sommets)), calibration
        )
        du_facteur = math.sqrt(2) * epsilon / calibration.ecart_en_points
        relative = float(mesure.incertitude_relative)
        return math.sqrt(max(relative**2 - du_facteur**2, 0.0))

    rapport = terme_de_trace(17) / terme_de_trace(5)
    # √(17/5) ≈ 1,84. La tolérance tient à ce que le zigzag n'a pas exactement
    # la même longueur totale selon le découpage.
    assert 1.5 < rapport < 2.2, f"rapport observé {rapport:.2f}, attendu autour de 1,84"


# ---------------------------------------------------------------------------
# La résolution déclarée
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facteur", [1, 2, 10])
def test_pointer_plus_finement_resserre_l_incertitude_proportionnellement(facteur: int) -> None:
    """L'incertitude est linéaire en la résolution de pointage, et rien d'autre.

    Ce qui garantit qu'elle est bien CALCULÉE depuis la résolution déclarée, et
    non lue dans une table de seuils.
    """
    octets = fabrique.plan_de_batiment()
    base = calibration_sur_la_cote(octets)
    fine = mesures_pdf.Calibration(
        premier=base.premier,
        second=base.second,
        distance_reelle=base.distance_reelle,
        unite=base.unite,
        resolution_du_pointage=base.resolution_du_pointage / facteur,
    )

    mur = points_de_la_page(octets, COINS_DE_LA_PIECE[:2])
    grossiere = mesures_pdf.longueur(mur, base)
    precise = mesures_pdf.longueur(mur, fine)

    rapport = float(grossiere.incertitude_relative) / float(precise.incertitude_relative)
    assert rapport == pytest.approx(facteur, rel=1e-6)


# ---------------------------------------------------------------------------
# Plusieurs pointages indépendants contre la même référence connue
# ---------------------------------------------------------------------------
#
# **Ce que les tests ci-dessus ne pouvaient pas dire.** Ils pointent les sommets
# du dessin à la coordonnée EXACTE, c'est-à-dire là où aucun humain ne clique.
# Ils prouvent donc que la chaîne de calcul est juste, et rien sur ce que le ±
# annoncé vaut face à la dispersion réelle d'un geste répété.
#
# Le propriétaire a demandé exactement cela : « Compare plusieurs pointages
# indépendants aux références connues. » Ce qui suit le fait, sans introduire
# de paramètre inventé.
#
# Le seul modèle d'erreur utilisé est celui que le navigateur IMPOSE : un clic
# ne rend pas une position continue, il rend un PIXEL. La coordonnée enregistrée
# est donc celle du centre du pixel visé, sur une grille dont le pas vaut ε —
# la résolution du pointage — et dont l'ORIGINE change à chaque placement de la
# loupe. Deux pointages successifs de la même cote, la loupe recentrée entre les
# deux, tombent sur deux grilles décalées : c'est cela qui les rend
# indépendants, et c'est une propriété du logiciel, pas une hypothèse sur la
# main de l'utilisateur.


def _sur_la_grille(valeur: float, origine: float, pas: float) -> float:
    """La coordonnée que le navigateur rend : le centre du pixel visé."""
    return origine + round((valeur - origine) / pas) * pas


def _pointage_independant(
    octets: bytes, decalage: float
) -> tuple[mesures_pdf.Calibration, list[mesures_pdf.Point]]:
    """Un pointage complet — calibration et mur — sur une grille décalée.

    `decalage` est la position de l'origine de la grille dans un pixel, en
    fraction de pixel. Il tient le rôle du recentrage de la loupe entre deux
    pointages : rien d'autre ne change.
    """
    boite, rotation = page_du_plan(octets)
    largeur, hauteur = boite[2] - boite[0], boite[3] - boite[1]
    epsilon = resolution_de_la_loupe(largeur, hauteur)
    origine = decalage * epsilon

    def pointe(sommet: tuple[float, float]) -> mesures_pdf.Point:
        grille = (
            _sur_la_grille(sommet[0], origine, epsilon),
            _sur_la_grille(sommet[1], origine, epsilon),
        )
        return mesures_pdf.vers_la_page(vers_l_ecran(grille, boite, rotation), boite, rotation)

    calibration = mesures_pdf.Calibration(
        premier=pointe((fabrique.COTE_X0, fabrique.COTE_Y)),
        second=pointe((fabrique.COTE_X1, fabrique.COTE_Y)),
        distance_reelle=Decimal("5000"),
        unite="mm",
        resolution_du_pointage=epsilon,
    )
    return calibration, [pointe(sommet) for sommet in COINS_DE_LA_PIECE[:2]]


#: Douze placements de loupe, répartis dans le pixel sans jamais retomber sur
#: le même décalage. Posés en dur : un tirage au hasard rendrait l'échec de ce
#: test non reproductible, et un test qui ne se rejoue pas ne prouve rien.
DECALAGES_DE_LA_LOUPE: tuple[float, ...] = (
    0.00,
    0.37,
    0.08,
    0.71,
    0.29,
    0.94,
    0.13,
    0.56,
    0.82,
    0.21,
    0.65,
    0.48,
)


def _douze_longueurs(octets: bytes) -> list[mesures_pdf.Mesure]:
    return [
        mesures_pdf.longueur(mur, calibration)
        for calibration, mur in (
            _pointage_independant(octets, decalage) for decalage in DECALAGES_DE_LA_LOUPE
        )
    ]


def test_douze_pointages_independants_encadrent_tous_la_cote_connue() -> None:
    """La propriété de couverture, éprouvée douze fois sur la même référence.

    6 000 mm est écrit dans la fabrique. Chacun des douze pointages est fait
    sur une grille de pixels différente, donc commet une erreur différente.
    **Aucun ne doit rater la vérité de plus de deux fois son propre ±.**

    Deux fois, et non une : le ± rendu est une incertitude TYPE (§ 2.0 de
    `docs/PRECISION_DES_MESURES.md`), pas un intervalle garanti. Un intervalle
    de couverture s'obtient en le multipliant par un facteur d'élargissement,
    et c'est k = 2 qui est vérifié ici.
    """
    octets = fabrique.plan_de_batiment()
    attendu = Decimal(str(fabrique.LARGEUR_DE_LA_PIECE_EN_MM))

    manques: list[str] = []
    for decalage, mesure in zip(DECALAGES_DE_LA_LOUPE, _douze_longueurs(octets), strict=True):
        if abs(mesure.valeur - attendu) > 2 * mesure.incertitude:
            manques.append(
                f"décalage {decalage} : {mesure.valeur} ± {mesure.incertitude} "
                f"rate {attendu} de plus de 2 σ"
            )

    assert manques == [], "\n".join(manques)


def test_la_dispersion_observee_ne_depasse_pas_l_incertitude_annoncee() -> None:
    """Le ± annoncé est-il assez large ? Oui, et de combien.

    Un ± plus petit que la dispersion réelle serait un mensonge. Ce test
    compare l'écart-type OBSERVÉ sur les douze pointages à l'incertitude
    ANNONCÉE, et exige que le second majore le premier.

    Il exige aussi que le rapport reste sous 10 : un ± cent fois trop large
    serait honnête mais inutilisable, et passerait la première moitié du test.
    Le rapport réellement constaté est écrit dans le document de précision.
    """
    octets = fabrique.plan_de_batiment()
    mesures = _douze_longueurs(octets)

    valeurs = [float(mesure.valeur) for mesure in mesures]
    moyenne = sum(valeurs) / len(valeurs)
    observee = math.sqrt(sum((valeur - moyenne) ** 2 for valeur in valeurs) / (len(valeurs) - 1))
    annoncee = sum(float(mesure.incertitude) for mesure in mesures) / len(mesures)

    assert observee <= annoncee, (
        f"dispersion observée {observee:.4f} mm > incertitude annoncée {annoncee:.4f} mm : "
        "le ± affiché est trop étroit"
    )
    assert annoncee <= 10 * max(observee, 1e-9), (
        f"incertitude annoncée {annoncee:.4f} mm pour une dispersion de {observee:.4f} mm : "
        "le ± est large au point de ne plus rien dire"
    )


def test_le_biais_moyen_des_douze_pointages_reste_sous_l_incertitude() -> None:
    """Un biais ne s'annule pas en répétant : il faut le regarder à part.

    L'hypothèse **H7** du document de précision dit que l'erreur de pointage
    est supposée centrée. Ce test la met à l'épreuve sur le seul mécanisme
    d'erreur que le logiciel impose : la quantification en pixels. La moyenne
    des douze écarts doit rester petite devant le ± d'un seul pointage, sinon
    la somme en quadrature n'a pas de sens.
    """
    octets = fabrique.plan_de_batiment()
    attendu = float(fabrique.LARGEUR_DE_LA_PIECE_EN_MM)
    mesures = _douze_longueurs(octets)

    biais = sum(float(mesure.valeur) - attendu for mesure in mesures) / len(mesures)
    annoncee = sum(float(mesure.incertitude) for mesure in mesures) / len(mesures)

    assert abs(biais) <= annoncee, (
        f"biais moyen de {biais:.4f} mm pour un ± annoncé de {annoncee:.4f} mm : "
        "l'erreur de pointage n'est pas centrée, et la quadrature la sous-estime"
    )
