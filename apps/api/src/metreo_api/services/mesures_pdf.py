"""Mesurer un PDF : calibrer, puis mesurer — et dire ce que la mesure vaut.

Ce module est **pur** : aucune base, aucun fichier, aucun réseau. Il reçoit des
points et rend des nombres. C'est ce qui permet de l'éprouver par le calcul,
sur des cas dont on connaît la réponse d'avance.

**Un PDF n'a pas d'unité.** Ses coordonnées sont des points PostScript
(1/72 de pouce), qui décrivent la feuille et ne disent rien de l'ouvrage.
Passer de l'un à l'autre demande un nombre que le fichier ne porte pas :
l'échelle. Ici, elle ne se devine jamais — elle est **calibrée** par une
personne qui désigne deux points et dit quelle distance les sépare dans la
réalité.

Trois choses que ce module fait, et qu'il faut lire ensemble.

1. **Il convertit le repère.** Les points arrivent du navigateur dans le repère
   de l'écran — [0,1], origine en haut à gauche, rotation d'affichage déjà
   appliquée. Les mesures se font dans celui de la page. La conversion est
   l'inverse exact de `lecture_pdf.normaliser`, et un test fait l'aller-retour
   sur les quatre rotations.

2. **Il mesure.** Un segment par sa longueur, une surface fermée par la formule
   du lacet. Rien d'autre : un périmètre, un angle ou un volume demanderaient
   chacun leur propre décision, et aucune n'est prise ici.

3. **Il dit ce que la mesure vaut**, et c'est le plus important.

   Une calibration posée à la main porte une erreur de pointage. Celle-ci se
   propage, et elle se propage **d'autant plus que la calibration est courte** :
   calibrer sur 100 points pour mesurer 3 000 multiplie l'erreur par trente.
   Le calcul est écrit ci-dessous et il est rendu avec la mesure, dans la même
   unité qu'elle.

   Mesuré sur quatre plans réels : à la résolution de l'aperçu pleine page
   (2 000 pixels sur le grand côté), **un pixel vaut 12 à 42 millimètres
   d'ouvrage** selon le format et l'échelle. Pointer sur l'aperçu, c'est donc
   se tromper de plusieurs centimètres par clic. Sur une vue agrandie, le même
   pixel vaut 0,6 à 6 mm. Ce module ne peut pas imposer où l'on pointe — mais
   il reçoit la résolution du pointage, en tire l'incertitude, et refuse de
   présenter comme sûre une mesure qui ne l'est pas.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from metreo_domain.units import Dimension, Quantity, convert, get_unit

#: Les rotations d'affichage, et comment revenir de l'écran à la page.
#:
#: Exactement l'inverse de `lecture_pdf._VERS_L_ECRAN`, et c'est voulu : les
#: deux tables se lisent côte à côte, et un test fait l'aller-retour sur les
#: quatre valeurs. `a` court le long de la largeur de la page, `b` le long de
#: sa hauteur et **vers le haut**, puisque c'est le repère du PDF.
_DEPUIS_L_ECRAN: dict[int, object] = {
    0: lambda x, y: (x, 1.0 - y),
    90: lambda x, y: (y, x),
    180: lambda x, y: (1.0 - x, y),
    270: lambda x, y: (1.0 - y, 1.0 - x),
}

#: En deçà, une calibration ne détermine rien.
#:
#: Dix points PostScript valent 3,5 millimètres de papier. Deux clics posés à
#: cette distance l'un de l'autre donnent un facteur dont l'erreur relative
#: dépasse 10 % dès que le pointage vaut deux pixels — et ce facteur
#: multiplierait ensuite TOUTES les mesures de la page.
#:
#: Ce n'est pas un seuil de confort : en dessous, le nombre produit n'a pas de
#: sens, et le refuser vaut mieux que l'assortir d'une réserve que personne ne
#: lira.
CALIBRATION_MINIMALE_EN_POINTS = 10.0

#: Au-delà de cette incertitude RELATIVE, une mesure reste « à vérifier ».
#:
#: **Ce nombre n'est pas une tolérance métier**, et il ne prétend pas en être
#: une : le propriétaire n'a pas encore fixé la sienne, et un terrassement n'a
#: pas la même exigence qu'un châssis (voir `docs/COTES_DE_REFERENCE.md`).
#:
#: C'est un garde-fou de dernier recours : au-delà de 2 % d'incertitude
#: PROPAGÉE — c'est-à-dire calculée depuis la résolution de pointage, pas
#: supposée — la mesure est trop mal déterminée pour être proposée sans
#: réserve, quelle que soit la tolérance qu'on retiendra ensuite.
SEUIL_D_INCERTITUDE = Decimal("0.02")

#: La résolution de pointage retenue quand l'appelant ne la déclare pas.
#:
#: Volontairement PESSIMISTE : un point de papier par pixel, soit à peu près
#: l'aperçu pleine page d'un A0. Un appelant qui ne dit pas à quelle échelle il
#: a pointé obtient donc une incertitude large, et ses mesures partent « à
#: vérifier ». L'inverse — supposer un pointage fin — présenterait comme sûre
#: une mesure prise au jugé.
RESOLUTION_PAR_DEFAUT_EN_POINTS = 1.0

#: Le nombre de décimales conservées. Dix, comme `Amount` en base : au-delà,
#: la valeur ne survivrait pas à son écriture.
DECIMALES = Decimal("0.0000000001")

Fiabilite = Literal["mesurable", "a_confirmer"]


class MesureRefusee(Exception):
    """La mesure n'a pas pu être faite, et on dit pourquoi en un code stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Point:
    """Un point dans le repère de la PAGE, en points PostScript absolus.

    Absolus et non normalisés : une coordonnée normalisée divise x et y par des
    longueurs différentes, ce qui ne conserve ni les distances ni les angles.
    Mesurer dans ce repère rendrait une diagonale fausse sur toute page qui
    n'est pas carrée.
    """

    u: float
    v: float


@dataclass(frozen=True)
class Calibration:
    """Ce qu'une personne a déclaré : deux points, et la distance qui les sépare.

    `distance_reelle` et `unite` sont **ce que la personne a saisi**, conservés
    tels quels. Le facteur n'est pas stocké : il se recalcule à l'identique
    depuis ces trois champs, et un facteur stocké finirait par diverger de ce
    dont il est issu.
    """

    premier: Point
    second: Point
    distance_reelle: Decimal
    #: Un code d'unité de LONGUEUR, résolu par `get_unit` — jamais une chaîne
    #: libre. Une unité inconnue est un refus, pas une valeur par défaut.
    unite: str
    #: La résolution du pointage, en points PostScript par pixel d'écran. C'est
    #: elle qui porte toute l'incertitude, et c'est à l'appelant de la dire.
    resolution_du_pointage: float = RESOLUTION_PAR_DEFAUT_EN_POINTS

    @property
    def ecart_en_points(self) -> float:
        """La distance pointée, dans le repère de la page."""
        return math.hypot(self.second.u - self.premier.u, self.second.v - self.premier.v)


@dataclass(frozen=True)
class Mesure:
    """Une mesure, son incertitude, et ce qui permet de la refaire.

    `incertitude` est dans la MÊME unité que `valeur` : une incertitude en
    pourcentage demanderait au lecteur de faire une multiplication pour savoir
    si le nombre lui convient, et cette multiplication ne se fait pas.
    """

    valeur: Decimal
    unite: str
    incertitude: Decimal
    incertitude_relative: Decimal
    fiabilite: Fiabilite
    #: Les réserves, chacune nommée. Vide quand la mesure ne porte rien.
    reserves: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Le repère
# ---------------------------------------------------------------------------


def vers_la_page(
    point_ecran: tuple[float, float],
    boite_affichee: tuple[float, float, float, float],
    rotation: int,
) -> Point:
    """De l'écran — [0,1], origine en haut à gauche — au repère de la page.

    L'inverse exact de `lecture_pdf.normaliser`, sans son rognage : un point
    hors de la page est ici une erreur de l'appelant, pas une donnée à
    conserver. Le rognage, là-bas, sert à situer un texte que le fichier
    contient ; ici, le point vient d'un clic, et un clic hors page ne mesure
    rien.
    """
    gauche, bas, droite, haut = boite_affichee
    largeur = droite - gauche
    hauteur = haut - bas
    if largeur <= 0 or hauteur <= 0:
        raise MesureRefusee(
            "page_sans_dimension",
            "La page ne déclare aucune dimension exploitable : rien ne peut y être situé.",
        )

    transformer = _DEPUIS_L_ECRAN.get(rotation % 360 if rotation else 0)
    if transformer is None:
        raise MesureRefusee(
            "rotation_inconnue",
            f"Une rotation de {rotation}° n'existe pas dans le format : seules "
            "0, 90, 180 et 270 sont possibles.",
        )

    x, y = point_ecran
    a, b = transformer(x, y)  # type: ignore[operator]
    return Point(u=gauche + a * largeur, v=bas + b * hauteur)


# ---------------------------------------------------------------------------
# La calibration
# ---------------------------------------------------------------------------


def facteur(calibration: Calibration) -> Decimal:
    """Combien d'unités réelles vaut UN point PostScript.

    Lève `MesureRefusee` plutôt que de rendre un nombre absurde : une
    calibration de longueur nulle, ou de distance réelle nulle ou négative, ne
    détermine aucun facteur.
    """
    ecart = calibration.ecart_en_points
    if ecart < CALIBRATION_MINIMALE_EN_POINTS:
        raise MesureRefusee(
            "calibration_trop_courte",
            f"Les deux points de calibration sont distants de {ecart:.1f} "
            f"points, en deçà des {CALIBRATION_MINIMALE_EN_POINTS:.0f} "
            "nécessaires. Un si petit écart ne détermine pas d'échelle : il "
            "multiplierait l'erreur de pointage sur toutes les mesures de la "
            "page. Désignez deux points plus éloignés — une cote longue est "
            "le meilleur choix.",
        )
    if calibration.distance_reelle <= 0:
        raise MesureRefusee(
            "distance_non_positive",
            "La distance réelle saisie doit être strictement positive.",
        )

    unite = get_unit(calibration.unite)
    if unite.dimension is not Dimension.LENGTH:
        raise MesureRefusee(
            "unite_non_lineaire",
            f"« {calibration.unite} » n'est pas une unité de longueur : une "
            "calibration déclare une DISTANCE entre deux points.",
        )

    return (calibration.distance_reelle / Decimal(str(ecart))).quantize(
        DECIMALES, rounding=ROUND_HALF_UP
    )


def _sensibilite_d_une_longueur(points: list[Point]) -> float:
    """De combien la longueur totale bouge quand chaque sommet bouge de un.

    **Le défaut que cette fonction corrige.** Le terme de tracé valait
    `√2·ε / L` : la formule d'un segment à DEUX extrémités, appliquée telle
    quelle à une polyligne de vingt sommets. Un relevé de façade en vingt
    clics était donc annoncé aussi sûr qu'un segment droit de même longueur,
    alors qu'il porte vingt erreurs de pointage et non deux.

    **Le calcul, et pourquoi il n'est pas « √N ».** La longueur totale vaut
    `Σ |P(i+1) − P(i)|`. Sa dérivée par rapport à un sommet est la différence
    des deux vecteurs unitaires qui en partent — `û(i-1) − û(i)` — et des
    erreurs indépendantes s'ajoutent en quadrature. D'où

        σ(L) = ε · √( Σ |û(i-1) − û(i)|² )

    et c'est ce que rend cette fonction, sans le `ε`.

    Ce que cette forme dit, et qu'un `√N` ne dirait pas :

    - **deux points** : les deux gradients valent 1, la somme vaut 2, et on
      retrouve exactement `√2` — l'ancienne formule était juste dans ce cas,
      et elle le reste ;
    - **des points ALIGNÉS** : les deux vecteurs unitaires d'un sommet
      intérieur sont égaux, leur différence est nulle, et le sommet n'ajoute
      rien. C'est physiquement vrai : glisser un point le long d'une droite ne
      change pas la longueur ;
    - **un tracé anguleux** : à angle droit, chaque sommet intérieur apporte
      `√2`, et la somme croît bien comme le nombre de sommets.

    Un `√N` forfaitaire aurait puni un tracé lisse — celui qui suit une
    courbe, où les sommets se compensent presque — pour une erreur qu'il ne
    commet pas.
    """
    directions: list[tuple[float, float]] = []
    for courant, suivant in itertools.pairwise(points):
        dx, dy = suivant.u - courant.u, suivant.v - courant.v
        norme = math.hypot(dx, dy)
        # Deux points confondus ne définissent aucune direction. Les compter
        # comme un vecteur nul est le choix prudent : le sommet apporte alors
        # la sensibilité d'une extrémité, et non zéro.
        directions.append((dx / norme, dy / norme) if norme > 0 else (0.0, 0.0))

    somme = 0.0
    for index in range(len(points)):
        avant = directions[index - 1] if index > 0 else (0.0, 0.0)
        apres = directions[index] if index < len(directions) else (0.0, 0.0)
        somme += (avant[0] - apres[0]) ** 2 + (avant[1] - apres[1]) ** 2
    return math.sqrt(somme)


def _sensibilite_d_une_aire(points: list[Point]) -> float:
    """De combien l'aire bouge quand chaque sommet bouge de un.

    Le pendant exact de `_sensibilite_d_une_longueur`, pour la formule du
    lacet. La dérivée de l'aire par rapport au sommet `i` ne dépend que de ses
    DEUX VOISINS :

        ∂A/∂P(i) = ½ · ( v(i+1) − v(i-1) , u(i-1) − u(i+1) )

    — c'est-à-dire la moitié de la diagonale qui les joint, tournée d'un quart
    de tour. Un sommet dont les voisins sont proches l'un de l'autre pèse donc
    peu sur l'aire, et un sommet qui sépare deux côtés longs pèse beaucoup.
    C'est ce que l'approximation par le périmètre ne savait pas dire.

    Rend `√( Σ |∂A/∂P(i)|² )`, en points de page au carré par point de page.
    """
    nombre = len(points)
    somme = 0.0
    for index in range(nombre):
        avant = points[index - 1]
        apres = points[(index + 1) % nombre]
        gradient_u = (apres.v - avant.v) / 2.0
        gradient_v = (avant.u - apres.u) / 2.0
        somme += gradient_u**2 + gradient_v**2
    return math.sqrt(somme)


def _incertitude_relative_du_facteur(calibration: Calibration) -> Decimal:
    """De combien le facteur peut être faux, en relatif.

    Deux points pointés, chacun à ±ε près : l'écart entre eux porte une erreur
    de √2·ε. Rapportée à l'écart lui-même, elle donne l'erreur relative du
    facteur — et c'est pourquoi une calibration courte empoisonne tout ce qui
    suit.
    """
    epsilon = max(calibration.resolution_du_pointage, 0.0)
    return Decimal(str(math.sqrt(2) * epsilon / calibration.ecart_en_points))


def _epsilon_du_trace(calibration: Calibration, declaree: float | None) -> float:
    """La finesse des clics du TRACÉ, qui n'est pas celle de la calibration.

    `None` veut dire « on ne sait pas », et le seul choix qui ne suppose rien
    est alors de reprendre celle de la calibration : c'est vrai dans l'écran
    livré, où les deux gestes se font dans la même loupe.

    Une valeur négative est ramenée à zéro, comme pour le facteur : une
    résolution négative n'a pas de sens, et la propager rendrait une
    incertitude négative — un nombre qui se lirait comme une précision.
    """
    if declaree is None:
        return max(calibration.resolution_du_pointage, 0.0)
    return max(declaree, 0.0)


# ---------------------------------------------------------------------------
# Les mesures
# ---------------------------------------------------------------------------


def _verdict(valeur: Decimal, relative: Decimal, reserves: list[str]) -> Mesure:
    """Assemble la mesure, son incertitude absolue et sa fiabilité.

    **Toute réserve dégrade**, et pas seulement celle d'incertitude. Une
    réserve existe précisément pour dire que quelque chose ne va pas : la
    poser tout en annonçant « mesurable » laisserait un écran afficher un
    nombre comme sûr avec, à côté, la raison de ne pas s'y fier. C'est le
    défaut qu'un contour qui se recoupe a révélé — il portait sa réserve et
    restait mesurable.
    """
    absolue = (valeur * relative).quantize(DECIMALES, rounding=ROUND_HALF_UP)
    if relative > SEUIL_D_INCERTITUDE:
        reserves.append("incertitude_elevee")
    fiabilite: Fiabilite = "a_confirmer" if reserves else "mesurable"
    return Mesure(
        valeur=valeur.quantize(DECIMALES, rounding=ROUND_HALF_UP),
        unite="",  # posée par l'appelant immédiat
        incertitude=absolue,
        incertitude_relative=relative.quantize(DECIMALES, rounding=ROUND_HALF_UP),
        fiabilite=fiabilite,
        reserves=tuple(reserves),
    )


def longueur(
    points: list[Point],
    calibration: Calibration,
    *,
    reserves: tuple[str, ...] = (),
    resolution_du_trace: float | None = None,
) -> Mesure:
    """La longueur d'une ligne brisée, dans l'unité de la calibration.

    L'unité n'est pas convertie : la personne a calibré en millimètres, la
    longueur sort en millimètres. Convertir ici imposerait un choix que
    personne n'a fait, et ferait perdre le lien direct entre ce qui a été saisi
    et ce qui est rendu.

    **`resolution_du_trace` ne touche QUE le terme de tracé**, et c'est tout
    l'intérêt de l'avoir en paramètre plutôt que de remplacer la résolution de
    la calibration. Les deux termes ne décrivent pas le même geste : le facteur
    porte l'erreur des deux clics de la CALIBRATION, posés une fois pour toute
    la page ; le tracé porte celle des clics qu'on vient de poser. Remplacer la
    résolution dans la calibration ferait varier les deux ensemble, et
    annoncerait une échelle plus sûre qu'elle n'est dès qu'on mesure en
    agrandissant davantage.

    Omis, c'est la résolution de la calibration qui sert — le comportement
    d'avant, et le seul qui ne suppose rien quand on ne sait pas.
    """
    if len(points) < 2:
        raise MesureRefusee(
            "segment_incomplet",
            "Une longueur demande au moins deux points.",
        )

    k = facteur(calibration)
    total_en_points = sum(
        math.hypot(suivant.u - courant.u, suivant.v - courant.v)
        for courant, suivant in itertools.pairwise(points)
    )
    if total_en_points <= 0:
        raise MesureRefusee(
            "longueur_nulle",
            "Les points désignés sont confondus : il n'y a aucune longueur à mesurer.",
        )

    valeur = k * Decimal(str(total_en_points))

    # La propagation : l'erreur du facteur ET celle du pointage de la mesure
    # elle-même, qui sont indépendantes et s'ajoutent donc en quadrature.
    #
    # La sensibilité du tracé est CALCULÉE sur les points posés, et non
    # supposée égale à celle d'un segment droit : voir
    # `_sensibilite_d_une_longueur`.
    du_facteur = _incertitude_relative_du_facteur(calibration)
    du_trace = Decimal(
        str(
            _sensibilite_d_une_longueur(points)
            * _epsilon_du_trace(calibration, resolution_du_trace)
            / total_en_points
        )
    )
    relative = Decimal(str(math.sqrt(float(du_facteur) ** 2 + float(du_trace) ** 2)))

    mesure = _verdict(valeur, relative, list(reserves))
    return Mesure(
        valeur=mesure.valeur,
        unite=get_unit(calibration.unite).code,
        incertitude=mesure.incertitude,
        incertitude_relative=mesure.incertitude_relative,
        fiabilite=mesure.fiabilite,
        reserves=mesure.reserves,
    )


#: L'unité dans laquelle toute surface est rendue.
#:
#: **Et pourquoi une conversion ici, alors qu'une longueur n'en subit aucune.**
#: Le dépôt ne connaît pas de millimètre carré : `units.py` déclare `m2`, `cm2`
#: et `ha`, et rien d'autre. Une calibration en millimètres n'a donc pas
#: d'unité de surface correspondante, et il faut bien en choisir une.
#:
#: Le mètre carré est l'unité canonique de la dimension AREA, et c'est celle
#: dans laquelle un métré de bâtiment se lit. La conversion passe par
#: `convert()`, jamais par un facteur réécrit ici.
UNITE_DE_SURFACE = "m2"


def aire(
    points: list[Point],
    calibration: Calibration,
    *,
    reserves: tuple[str, ...] = (),
    resolution_du_trace: float | None = None,
) -> Mesure:
    """L'aire d'un contour fermé, en mètres carrés.

    La formule du lacet, dont le résultat est pris en valeur absolue : un
    contour tracé dans le sens horaire rend une aire négative, et le sens du
    tracé n'est pas une information que l'utilisateur a voulu donner.

    Le contour est fermé implicitement — le dernier point rejoint le premier.
    Un contour qui se recoupe lui-même rend une aire que la formule calcule
    sans broncher et qui ne veut rien dire ; c'est signalé, pas corrigé, parce
    que décider ce que l'utilisateur voulait dessiner n'appartient pas à ce
    module.
    """
    if len(points) < 3:
        raise MesureRefusee(
            "contour_incomplet",
            "Une surface demande au moins trois points.",
        )

    # Appelé pour ses REFUS, et non pour sa valeur : c'est lui qui rejette une
    # calibration trop courte, une distance négative ou une unité qui n'est pas
    # une longueur. L'aire n'utilise pas ce facteur-ci — voir plus bas pourquoi.
    facteur(calibration)

    # Formule du lacet, sur les coordonnées de la page.
    double_aire = 0.0
    perimetre = 0.0
    for courant, suivant in zip(points, [*points[1:], points[0]], strict=False):
        double_aire += courant.u * suivant.v - suivant.u * courant.v
        perimetre += math.hypot(suivant.u - courant.u, suivant.v - courant.v)
    aire_en_points = abs(double_aire) / 2.0

    if aire_en_points <= 0:
        raise MesureRefusee(
            "surface_nulle",
            "Les points désignés sont alignés ou confondus : ils n'enferment aucune surface.",
        )

    toutes_les_reserves = list(reserves)
    if _se_recoupe(points):
        toutes_les_reserves.append("contour_qui_se_recoupe")

    # Deux termes, et ils ne se doublent pas de la même façon.
    #
    # **Le facteur, lui, est bien doublé** : l'aire vaut `k² × A`, donc une
    # erreur relative de 1 % sur `k` en fait 2 % sur l'aire. L'oublier
    # annoncerait une surface deux fois plus sûre qu'elle ne l'est, et une
    # surface entre directement dans un métré.
    #
    # **Le tracé, non.** L'erreur de pointage se propage par la dérivée de la
    # formule du lacet, sommet par sommet — `_sensibilite_d_une_aire` — et non
    # par le périmètre. L'approximation par le périmètre valait pour un carré
    # et se trompait sur tout le reste : un contour très allongé, ou un contour
    # à sommets rapprochés, n'ont pas la même sensibilité qu'un carré de même
    # périmètre.
    du_facteur = _incertitude_relative_du_facteur(calibration)
    du_trace = Decimal(
        str(
            _sensibilite_d_une_aire(points)
            * _epsilon_du_trace(calibration, resolution_du_trace)
            / aire_en_points
        )
    )
    relative = Decimal(str(math.hypot(2 * float(du_facteur), float(du_trace))))

    # L'aire se calcule à partir d'un facteur exprimé en MÈTRES par point, et
    # non du facteur dans l'unité de la calibration élevé au carré.
    #
    # La différence n'est pas cosmétique : la conversion de l'unité saisie vers
    # le mètre est faite par `convert()`, donc par la seule table d'unités du
    # dépôt, sur une LONGUEUR — une dimension que cette table connaît. Élever
    # ensuite au carré est de l'arithmétique, pas une conversion. Écrire
    # l'inverse obligerait à convertir des millimètres carrés, que `units.py`
    # ne déclare pas, et donc à recopier ici un facteur qui vit ailleurs.
    distance_en_metres = convert(
        Quantity.of(calibration.distance_reelle, calibration.unite), "m"
    ).quantity.value
    facteur_en_metres = distance_en_metres / Decimal(str(calibration.ecart_en_points))
    en_metres_carres = (facteur_en_metres * facteur_en_metres) * Decimal(str(aire_en_points))
    mesure = _verdict(en_metres_carres, relative, toutes_les_reserves)
    return Mesure(
        valeur=mesure.valeur,
        unite=UNITE_DE_SURFACE,
        incertitude=mesure.incertitude,
        incertitude_relative=mesure.incertitude_relative,
        fiabilite=mesure.fiabilite,
        reserves=mesure.reserves,
    )


def _se_recoupe(points: list[Point]) -> bool:
    """Le contour se croise-t-il lui-même ?

    Contrôle en O(n²), et c'est assez : un contour tracé à la main par un
    humain compte quelques dizaines de sommets, pas quelques milliers.
    """
    nombre = len(points)
    cotes = [(points[i], points[(i + 1) % nombre]) for i in range(nombre)]
    for i in range(nombre):
        for j in range(i + 1, nombre):
            # Les côtés adjacents partagent un sommet : ils se « touchent »
            # toujours, et ce n'est pas un croisement.
            if j == i + 1 or (i == 0 and j == nombre - 1):
                continue
            if _se_croisent(cotes[i], cotes[j]):
                return True
    return False


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b.u - a.u) * (c.v - a.v) - (b.v - a.v) * (c.u - a.u)


def _se_croisent(premier: tuple[Point, Point], second: tuple[Point, Point]) -> bool:
    """Deux segments se croisent-ils ? Cas dégénérés exclus volontairement.

    Le test ne traite que le croisement FRANC : deux segments colinéaires qui
    se chevauchent ne sont pas signalés. C'est une limite assumée — le signal
    sert à avertir d'un tracé manifestement croisé, pas à valider une topologie.
    """
    a, b = premier
    c, d = second
    d1 = _orientation(a, b, c)
    d2 = _orientation(a, b, d)
    d3 = _orientation(c, d, a)
    d4 = _orientation(c, d, b)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))
