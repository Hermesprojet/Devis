"""Lire un DXF : son unité, ses calques, ses cotations — et ce qui empêche de mesurer.

Ce module LIT. Il ne mesure pas, il ne convertit pas, il n'écrit rien en base.
Sa sortie est un constat : voici ce que le fichier déclare, voici ce que j'ai
pu recalculer, et voici ce qui interdit d'en tirer une quantité. La décision
reste humaine, et la quantité ne naîtra qu'après elle.

**Tout est déterministe.** Aucun modèle de langage n'intervient, aucune valeur
n'est devinée. Quand une information manque, elle est rendue absente et
l'anomalie est nommée — jamais remplacée par une valeur plausible.

Trois règles viennent de plans d'exécution réels, et non de la documentation.
Elles sont écrites ici parce que les suivre de mémoire ne se vérifie pas.

1. **`actual_measurement` est inexploitable en pratique.** La documentation
   d'ezdxf avertit qu'il est « souvent absent ». La réalité est pire : sur deux
   plans d'exécution d'un même projet, 1 410 cotations sur 1 410 le portent —
   et il vaut **-1** pour toutes. Un contrôle de simple présence l'accepterait
   et produirait des quantités de -1 mm. La valeur n'est retenue que si elle
   est présente ET strictement positive ; sinon la mesure est recalculée.

2. **Une mesure recalculée n'est pas exacte au bit près.** Sur ces mêmes plans,
   357 cotations sur 740 rendent un nombre exactement entier, et les 740
   tombent à moins d'un demi-millimètre d'un entier. L'écart vient de la
   géométrie elle-même, pas du calcul. Comparer à une valeur humaine exige donc
   une tolérance, et l'arrondi ne se fait jamais en silence.

3. **Le texte d'une cotation peut mentir sur sa propre mesure.** Il est saisi
   par un dessinateur. Vide ou `<>`, la mesure s'affiche telle quelle ; toute
   autre valeur l'écrase à l'écran sans changer la géométrie. Les deux sont
   conservés, et la divergence est signalée — jamais arbitrée ici.

4. **La plupart des cotes ne sont pas à plat dans l'espace modèle.** Un
   dessinateur fait un bloc « façade », un bloc « porte », et les insère — une
   fois, dix fois, en grille, les uns dans les autres.
   `modelspace.query("DIMENSION")` ne voit que le premier niveau : sur un
   fichier dont les cotes vivent dans des blocs, il rend **zéro**, en silence.
   Le parcours des blocs est donc une condition pour lire un plan réel, pas un
   raffinement. Il porte ses propres pièges, détaillés devant
   `_instances_de_cotation` : une insertion à l'échelle 2 double la longueur
   mesurée, `document.blocks` contient `*Model_Space`, et une cotation de bloc
   porte le même handle à chacune de ses insertions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

#: Les unités de dessin, telles que `$INSUNITS` les code, et le code d'unité
#: de Metreo correspondant. La table est volontairement courte : une unité
#: qu'on ne sait pas nommer rend l'unité INCONNUE, ce qui interdit la mesure.
#:
#: `0` signifie « sans unité » et n'est pas un oubli du dessinateur : c'est une
#: valeur que le format autorise. Elle interdit toute mesure, parce qu'un 5 000
#: peut alors valoir 5 mètres comme 5 kilomètres.
UNITES_DXF: dict[int, str] = {
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
}

#: Jusqu'où descendre dans les blocs imbriqués.
#:
#: Un bloc peut contenir une insertion d'un autre bloc, qui peut en contenir
#: une autre. Sur un plan d'exécution, deux à trois niveaux suffisent — une
#: porte dans une cloison dans un étage. Huit laissent largement la marge.
#:
#: Ce plafond N'EST PAS la protection contre les cycles : un cycle est détecté
#: par le chemin lui-même (voir `_descendre`), et ezdxf le signale à l'audit
#: (code 104). Celui-ci borne une imbrication profonde mais licite, dont le
#: coût explose : dix blocs insérés dix fois chacun font dix milliards
#: d'instances au dixième niveau.
PLAFOND_IMBRICATION = 8

#: Au-delà, on arrête de récolter et on le DIT.
#:
#: Mesuré sur deux plans d'exécution réels : 1 410 cotations. Vingt mille
#: laissent quatorze fois la marge et bornent un fichier dont un bloc coté
#: serait inséré des milliers de fois — ce qui est licite, et ce qui ferait
#: autant de propositions à valider à la main.
PLAFOND_INSTANCES = 20_000

#: Deux facteurs d'échelle sont « le même » en deçà de cet écart relatif.
#:
#: Pas zéro : une matrice composée passe par des flottants, et un bloc à
#: l'échelle 1 inséré dans un bloc à l'échelle 1 peut rendre 0,9999999999998.
#: Refuser cela nommerait « non uniforme » une insertion qui ne l'est pas.
TOLERANCE_ECHELLE = 1e-9

#: Code d'audit d'ezdxf pour un cycle de référence de bloc.
#:
#: Il n'est pas réparable et il n'est pas ignorable : ezdxf le SIGNALE sans
#: casser le cycle, et ni `explode()` ni `virtual_entities()` ne portent de
#: garde de profondeur. Parcourir un tel fichier est une récursion infinie.
CODE_CYCLE_DE_BLOCS = 104

#: Au-delà, une coordonnée n'est plus un plan de bâtiment mais une erreur de
#: saisie ou un fichier forgé. `1e308` est un flottant parfaitement valide :
#: il passe l'audit, puis rend `inf` dans un calcul d'aire, puis `nan` dans un
#: total. Le garde-fou doit donc être AVANT le calcul.
#:
#: Dix millions d'unités de dessin : 10 km en millimètres, 100 km en
#: centimètres. Aucun plan d'exécution ne les dépasse.
ENVELOPPE_PLAUSIBLE = 1e7

#: En deçà, une cotation est CONSERVÉE avec le statut « à vérifier ». Observé
#: sur un plan réel : une cotation à 0,0 mm et une autre à 0,1 mm, résidus
#: d'édition probables — mais seulement probables.
#:
#: **Ce seuil ne décide rien et n'écarte rien.** Il n'est pas arrêté : la
#: valeur sera fixée après confrontation à de vrais exemples, par celui qui
#: connaît les ouvrages. D'ici là une cotation sous le seuil part en relecture
#: humaine au lieu de disparaître, parce que les deux erreurs ne coûtent pas la
#: même chose : une cote de 0,2 mm proposée à tort se refuse d'un clic, une
#: cote réelle silencieusement retirée du métré ne se retrouve pas.
#:
#: Une mesure NÉGATIVE n'est pas une petite mesure : voir plus bas, elle reste
#: inexploitable. Une longueur n'a pas de signe.
SEUIL_PETITE_MESURE = 0.5

Fiabilite = Literal["mesurable", "a_confirmer", "inexploitable"]


@dataclass(frozen=True)
class Anomalie:
    """Ce qui empêche ou fragilise une reprise, nommé et situé."""

    code: str
    message: str
    #: Le handle de l'entité, quand l'anomalie porte sur une entité précise.
    object_ref: str | None = None
    calque: str | None = None


@dataclass(frozen=True)
class Cadre:
    """Où se trouve un objet dans l'image du plan, en coordonnées [0,1].

    Mêmes conventions que la boîte englobante d'une citation : origine **en
    haut à gauche**, bornes dans [0,1]. L'axe vertical d'un DXF monte et celui
    d'une image descend : `y` est donc INVERSÉ au passage. Sans cette
    inversion, une cotation du bas du plan serait surlignée en haut.

    Le repère est l'étendue du dessin, la MÊME que celle qui a servi au rendu.
    C'est ce qui permet de surligner une cotation sur le SVG sans rien
    recalculer dans le navigateur — et c'est pourquoi les deux étapes doivent
    s'accorder : un test compare les deux cadres.
    """

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Cotation:
    """Une cotation lue, avec de quoi la retrouver dans le fichier.

    `valeur` est la mesure en unités DU DOCUMENT, avant toute conversion. La
    conversion vers l'unité d'une ligne de bordereau appartient au moteur de
    prix, pas à un lecteur de plan.
    """

    #: Handle de l'entité DXF : la seule désignation stable d'un objet.
    object_ref: str
    calque: str
    #: `lineaire`, `alignee`, `angulaire`, `diametre`, `rayon`, `ordonnee`…
    famille: str
    valeur: Decimal | None
    #: D'où vient `valeur` : la cote stockée par le logiciel, ou un recalcul.
    origine: Literal["cote_42", "recalcul", "aucune"]
    #: Le texte saisi par un humain, s'il écrase la mesure à l'écran.
    texte_impose: str | None
    fiabilite: Fiabilite
    anomalies: tuple[Anomalie, ...] = ()
    #: Où la retrouver dans l'image, quand `lire(situer=True)` l'a calculé.
    #: `None` ne veut pas dire « au milieu » : il veut dire « on ne sait pas »,
    #: et l'écran doit alors désigner la cotation par son calque et son handle.
    cadre: Cadre | None = None


@dataclass
class LecturePlan:
    """Le constat complet d'une lecture, refus compris."""

    #: Vrai quand le fichier ne doit PAS être parcouru.
    refuse: bool = False
    motif_du_refus: Anomalie | None = None

    #: Le code d'unité de Metreo, ou None quand le document ne le dit pas.
    unite_source: str | None = None
    #: La valeur brute de `$INSUNITS`, conservée telle quelle pour l'audit.
    insunits: int | None = None
    version_dxf: str | None = None

    #: Nom de calque → nombre d'entités portées, dans le modelspace.
    calques: dict[str, int] = field(default_factory=dict)
    entites: dict[str, int] = field(default_factory=dict)
    cotations: list[Cotation] = field(default_factory=list)
    anomalies: list[Anomalie] = field(default_factory=list)
    #: Les mises en page du fichier, utiles pour nommer la « feuille ».
    feuilles: list[str] = field(default_factory=list)

    #: L'étendue du dessin en unités du document : xmin, ymin, xmax, ymax.
    #: Renseignée seulement par `lire(situer=True)`. C'est le repère commun au
    #: rendu et aux cadres de cotation.
    etendue: tuple[float, float, float, float] | None = None

    @property
    def mesurable(self) -> bool:
        """Une quantité peut-elle être proposée depuis ce fichier ?

        Non si le fichier est refusé, non si son unité est inconnue. Dans les
        deux cas la visualisation et l'archivage restent possibles : ils ne
        demandent aucune unité.
        """
        return not self.refuse and self.unite_source is not None

    def cotations_mesurables(self) -> list[Cotation]:
        """Celles qui ne portent aucune réserve. Rien n'est proposé d'office."""
        return [c for c in self.cotations if c.fiabilite == "mesurable"]

    def cotations_a_verifier(self) -> list[Cotation]:
        """Celles qui portent une réserve nommée, et attendent un humain.

        Elles ne sont NI écartées ni reprises : l'écran doit les montrer avec
        leur anomalie, et l'utilisateur confirme ou corrige. Une cotation très
        petite arrive ici depuis que le seuil ne décide plus seul.
        """
        return [c for c in self.cotations if c.fiabilite == "a_confirmer"]


#: Les familles de cotation, par les trois bits de poids faible de `dimtype`.
_FAMILLES: dict[int, str] = {
    0: "lineaire",
    1: "alignee",
    2: "angulaire",
    3: "diametre",
    4: "rayon",
    5: "angulaire_3_points",
    6: "ordonnee",
}

#: Du plus sûr au moins sûr. Une cotation qui porte DEUX réserves garde la
#: plus basse des deux, et ses deux anomalies : avant ce classement, la
#: première réserve rencontrée court-circuitait les contrôles suivants, et une
#: cote à la fois minuscule et surchargée d'un texte ne signalait que l'une.
_RANG_FIABILITE: dict[str, int] = {"inexploitable": 0, "a_confirmer": 1, "mesurable": 2}


def _degrader(actuelle: Fiabilite, proposee: Fiabilite) -> Fiabilite:
    """Rend la moins sûre des deux. Ne remonte jamais une fiabilité."""
    if _RANG_FIABILITE[proposee] < _RANG_FIABILITE[actuelle]:
        return proposee
    return actuelle


#: Une famille dont la mesure n'est PAS une longueur. Un angle en degrés et un
#: vecteur d'ordonnée ne se reprennent pas comme une longueur : les retenir
#: sans le dire conduirait à additionner des degrés à des millimètres.
_FAMILLES_NON_LINEAIRES = frozenset({"angulaire", "angulaire_3_points", "ordonnee"})


def _unite_de(insunits: int | None) -> tuple[str | None, Anomalie | None]:
    if insunits is None:
        return None, Anomalie(
            "unite_absente",
            "Le document ne déclare pas d'unité ($INSUNITS absent). "
            "Aucune mesure ne peut en être tirée sans confirmation humaine.",
        )
    if insunits == 0:
        return None, Anomalie(
            "unite_sans_valeur",
            "Le document se déclare « sans unité » ($INSUNITS = 0). "
            "Un 5000 peut alors valoir 5 mètres comme 5 kilomètres.",
        )
    code = UNITES_DXF.get(insunits)
    if code is None:
        return None, Anomalie(
            "unite_non_prise_en_charge",
            f"L'unité de dessin $INSUNITS = {insunits} n'est pas prise en charge.",
        )
    return code, None


def _texte_impose(brut: str | None) -> str | None:
    """Le texte saisi, s'il écrase la mesure.

    Vide ou `<>` : la mesure s'affiche telle quelle, rien n'est imposé. Un
    seul espace : le texte est supprimé à l'écran, ce qui ne change pas non
    plus la mesure.
    """
    if brut is None:
        return None
    nettoye = brut.strip()
    if nettoye in ("", "<>"):
        return None
    return brut


def _normaliser(
    boite: tuple[float, float, float, float],
    etendue: tuple[float, float, float, float],
) -> Cadre | None:
    """Rapporte une boîte à l'étendue du dessin, ou rend `None`.

    Rend `None` dans trois cas, et aucun n'est une erreur de fichier :
    une étendue plate (tout le dessin sur une ligne), une boîte dégénérée
    (une cotation sans épaisseur), ou une boîte qui sort du cadre. Dans les
    trois, mieux vaut ne pas situer que situer faux : la contrainte de base
    exige `x0 < x1` et `y0 < y1` strictement, et un surlignage de largeur
    nulle ne se verrait pas davantage qu'une absence.
    """
    x_min, y_min, x_max, y_max = etendue
    largeur = x_max - x_min
    hauteur = y_max - y_min
    if largeur <= 0 or hauteur <= 0:
        return None

    bx0, by0, bx1, by1 = boite
    x0 = (bx0 - x_min) / largeur
    x1 = (bx1 - x_min) / largeur
    # L'axe vertical s'inverse : voir la docstring de `Cadre`.
    y0 = 1.0 - (by1 - y_min) / hauteur
    y1 = 1.0 - (by0 - y_min) / hauteur
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        return None
    return Cadre(x0=x0, y0=y0, x1=x1, y1=y1)


def _lire_une_cotation(
    entite: object,
    *,
    object_ref: str | None = None,
    reserves_du_contexte: tuple[Anomalie, ...] = (),
    fiabilite_maximale: Fiabilite = "mesurable",
) -> Cotation:
    """Lit UNE cotation. `entite` peut être une copie transformée.

    `object_ref` permet à l'appelant d'imposer une désignation : une cotation
    qui vit dans un bloc inséré trois fois porte TROIS fois le même handle, et
    trois mesures qui se désignent pareil ne sont pas trois mesures — la base
    déduplique sur ce champ, et deux des trois disparaîtraient. L'appelant
    passe donc le chemin d'insertion. Par défaut, le handle de l'entité, ce
    qui laisse une cotation de premier niveau exactement comme avant.

    `reserves_du_contexte` sont les réserves qui portent sur l'INSERTION et
    non sur la cotation — une échelle de bloc non uniforme, par exemple. Elles
    sont attachées à la mesure parce que c'est là qu'un humain les lira.

    `fiabilite_maximale` plafonne la fiabilité : une réserve de contexte doit
    pouvoir dégrader une cotation que le fichier, lui, ne contredit pas.
    """
    dxf = entite.dxf  # type: ignore[attr-defined]
    if object_ref is None:
        object_ref = str(dxf.get("handle", "") or "")
    calque = str(dxf.get("layer", "") or "")
    famille = _FAMILLES.get(int(dxf.get("dimtype", 0)) & 7, "inconnue")
    anomalies: list[Anomalie] = list(reserves_du_contexte)

    texte = _texte_impose(dxf.get("text", None))

    valeur: Decimal | None = None
    origine: Literal["cote_42", "recalcul", "aucune"] = "aucune"

    # La cote 42, et la raison de ne pas s'y fier : voir l'en-tête du module.
    cote = dxf.get("actual_measurement", None)
    if cote is not None and isinstance(cote, (int, float)) and cote > 0:
        valeur = Decimal(str(cote))
        origine = "cote_42"
    else:
        if cote is not None:
            anomalies.append(
                Anomalie(
                    "cote_stockee_sentinelle",
                    f"La cote stockée par le logiciel vaut {cote}, ce qui n'est pas "
                    "une mesure. Elle est ignorée et la mesure est recalculée.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
        try:
            recalcul = entite.get_measurement()  # type: ignore[attr-defined]
        except Exception as erreur:  # ezdxf lève TypeError sur un dimtype inconnu
            anomalies.append(
                Anomalie(
                    "mesure_incalculable",
                    f"La mesure n'a pas pu être recalculée : {type(erreur).__name__}.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
            recalcul = None
        if isinstance(recalcul, (int, float)):
            valeur = Decimal(str(recalcul))
            origine = "recalcul"
        elif recalcul is not None:
            # Une ordonnée rend un vecteur, pas un nombre. Ce n'est pas une
            # erreur du fichier : c'est une famille qu'on ne reprend pas.
            anomalies.append(
                Anomalie(
                    "mesure_non_scalaire",
                    "Cette famille de cotation ne rend pas une longueur mais "
                    f"un {type(recalcul).__name__}.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )

    fiabilite: Fiabilite = fiabilite_maximale

    if valeur is None:
        fiabilite = "inexploitable"
    else:
        if not valeur.is_finite():
            anomalies.append(
                Anomalie(
                    "mesure_non_finie",
                    "La mesure n'est pas un nombre fini.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
            fiabilite = _degrader(fiabilite, "inexploitable")
        elif abs(valeur) > Decimal(str(ENVELOPPE_PLAUSIBLE)):
            anomalies.append(
                Anomalie(
                    "mesure_hors_enveloppe",
                    f"La mesure {valeur} dépasse l'enveloppe plausible "
                    f"de {ENVELOPPE_PLAUSIBLE:.0f} unités de dessin.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
            fiabilite = _degrader(fiabilite, "inexploitable")
        elif valeur < 0:
            # Une longueur négative n'est pas une petite longueur : c'est une
            # géométrie que le lecteur n'a pas comprise. La conserver « à
            # vérifier » proposerait une quantité de signe faux à l'écran.
            #
            # Vérifié dans ezdxf 1.4.4 : aucun des sept outils de mesure ne
            # rend de valeur signée — les longueurs passent par `.magnitude`
            # et `angle_between` ramène l'angle dans [0, 360). Cette branche
            # garde donc contre un changement en amont, et non contre un
            # fichier. Elle est éprouvée en remplaçant l'outil de mesure
            # d'ezdxf, parce qu'aucun DXF ne la déclenche aujourd'hui.
            anomalies.append(
                Anomalie(
                    "mesure_negative",
                    f"La mesure {valeur} est négative, ce qu'aucune longueur "
                    "ne peut être. La géométrie n'a pas été interprétée.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
            fiabilite = _degrader(fiabilite, "inexploitable")
        elif valeur < Decimal(str(SEUIL_PETITE_MESURE)):
            anomalies.append(
                Anomalie(
                    "mesure_tres_petite",
                    f"La mesure {valeur} est très petite pour un ouvrage "
                    f"(seuil provisoire : {SEUIL_PETITE_MESURE} unité de "
                    "dessin). Elle est conservée et demande une vérification "
                    "humaine ; le seuil reste à confirmer sur de vrais plans.",
                    object_ref=object_ref or None,
                    calque=calque or None,
                )
            )
            fiabilite = _degrader(fiabilite, "a_confirmer")

    if valeur is not None and fiabilite != "inexploitable" and famille in _FAMILLES_NON_LINEAIRES:
        anomalies.append(
            Anomalie(
                "famille_non_lineaire",
                f"La famille « {famille} » ne mesure pas une longueur. "
                "Sa reprise demande une décision explicite.",
                object_ref=object_ref or None,
                calque=calque or None,
            )
        )
        fiabilite = _degrader(fiabilite, "a_confirmer")

    if valeur is not None and fiabilite != "inexploitable" and texte is not None:
        anomalies.append(
            Anomalie(
                "texte_impose",
                f"Le plan affiche « {texte.strip()} » à la place de la mesure. "
                "Les deux sont conservés ; l'écart est à trancher par un humain.",
                object_ref=object_ref or None,
                calque=calque or None,
            )
        )
        fiabilite = _degrader(fiabilite, "a_confirmer")

    return Cotation(
        object_ref=object_ref,
        calque=calque,
        famille=famille,
        valeur=valeur,
        origine=origine,
        texte_impose=texte,
        fiabilite=fiabilite,
        anomalies=tuple(anomalies),
    )


# ---------------------------------------------------------------------------
# Les cotations contenues dans des blocs
# ---------------------------------------------------------------------------
#
# **Le défaut que cette section corrige.** `modelspace.query("DIMENSION")` ne
# voit que le premier niveau. Vérifié par exécution : un fichier dont les trois
# cotations vivent dans des blocs rend `0 cotation`. Pas une erreur, pas une
# anomalie — zéro, en silence. Un plan réel qui range ses cotes dans des blocs
# de façade aurait été déclaré « lu, aucune mesure », et le propriétaire aurait
# conclu que Metreo ne sait pas lire son plan.
#
# **Et le défaut que cette section doit ÉVITER de créer**, qui est le
# symétrique : `document.blocks` contient `*Model_Space`. Parcourir tous les
# blocs du document compterait donc chaque cotation de premier niveau DEUX
# fois, et compterait une fois chaque cotation d'un bloc jamais inséré — un
# fantôme — tout en ne comptant qu'une fois celle d'un bloc inséré trois fois.
# On ne parcourt donc JAMAIS `document.blocks` : on part de l'espace modèle et
# on descend par les insertions. Un test le vérifie en comptant les instances.
#
# Trois choses sont apprises par exécution sur ezdxf 1.4.4, et aucune ne se
# devine en lisant la documentation :
#
# 1. une copie transformée rend la BONNE mesure. `entite.copy()` puis
#    `.transform(matrice)` sous une insertion à l'échelle 2 rend 10 000 pour
#    une cotation de 5 000. La mesure de l'original, elle, reste 5 000 : la
#    lire sans transformer donnerait une quantité deux fois trop petite ;
# 2. la copie n'a PAS de handle (`copie.dxf.handle` vaut `None`). Le handle se
#    lit donc sur l'original, et c'est pour cela que ce code compose les
#    matrices lui-même au lieu d'utiliser `virtual_entities()`, qui rend des
#    entités justes mais anonymes — donc impossibles à citer ;
# 3. `matrix44()` prend déjà en compte le `base_point` du bloc. Le soustraire
#    à la main décalerait tout d'une fois le point de base.


def _facteurs_d_echelle(matrice: object) -> tuple[float, float]:
    """Combien la matrice étire en x et en y. Toujours positif.

    Une insertion miroir (`xscale` négatif) a une magnitude de 1 : elle
    retourne la géométrie sans changer aucune longueur, et c'est correct —
    une façade symétrique s'insère ainsi.
    """
    x = matrice.transform_direction((1.0, 0.0, 0.0)).magnitude  # type: ignore[attr-defined]
    y = matrice.transform_direction((0.0, 1.0, 0.0)).magnitude  # type: ignore[attr-defined]
    return float(x), float(y)


def _reserve_d_echelle(matrice: object, chemin_de_blocs: tuple[str, ...]) -> Anomalie | None:
    """L'insertion étire-t-elle différemment les deux axes ?

    Si oui, la longueur d'une cotation **dépend de sa direction**, et ezdxf
    rendra bien un nombre : celui de la direction où elle est dessinée. Ce
    nombre n'est pas faux, il est seulement indécidable à distance — une cote
    à 45° sous une échelle 2/3 ne vaut ni le double ni le triple.
    """
    x, y = _facteurs_d_echelle(matrice)
    if x <= 0.0 or y <= 0.0:
        # Ceci borne la matrice COMPOSÉE, et non un attribut de fichier.
        # Vérifié à l'exécution : ezdxf 1.4.4 ramène un facteur nul à 1 — à
        # l'affectation, à l'écriture et à la relecture — et même `1e-180` est
        # ramené à 1. Aucun DXF passé par ezdxf ne porte donc d'insertion
        # dégénérée. Ce qui reste atteignable est le sous-débordement d'un
        # produit de facteurs extrêmes au fil d'une imbrication, et c'est ce
        # qu'un test éprouve en appelant cette fonction directement.
        return Anomalie(
            "echelle_de_bloc_degeneree",
            "Le bloc est inséré avec un facteur d'échelle nul : sa géométrie "
            f"est aplatie, et aucune longueur n'en sort. Chemin : {' > '.join(chemin_de_blocs)}.",
        )
    if abs(x - y) > TOLERANCE_ECHELLE * max(x, y):
        return Anomalie(
            "echelle_de_bloc_non_uniforme",
            f"Le bloc est inséré en étirant x de {x:.6g} et y de {y:.6g}. La "
            "longueur d'une cotation dépend alors de sa direction : la mesure "
            "est conservée et demande une vérification humaine. Chemin : "
            f"{' > '.join(chemin_de_blocs)}.",
        )
    return None


@dataclass(frozen=True)
class _Instance:
    """Une cotation trouvée, et de quoi la ramener en espace modèle.

    `matrice` vaut `None` pour une cotation du premier niveau : il n'y a rien
    à transformer, et une matrice identité coûterait une copie de l'entité
    pour rien sur les 1 410 cotations d'un plan réel.
    """

    entite: object
    object_ref: str
    matrice: object | None
    reserves: tuple[Anomalie, ...]


def _instances_de_cotation(document: object, anomalies: list[Anomalie]) -> list[_Instance]:
    """Toutes les cotations de l'espace modèle, blocs imbriqués compris.

    Une par INSTANCE, non par définition : un bloc coté inséré trois fois rend
    trois cotations, à trois endroits, chacune citable séparément.
    """
    trouvees: list[_Instance] = []
    atteint_le_plafond = False
    imbrication_trop_profonde: set[str] = set()
    cycles: set[str] = set()

    def _descendre(
        conteneur: object,
        *,
        matrice: object | None,
        chemin_de_handles: tuple[str, ...],
        chemin_de_blocs: tuple[str, ...],
        reserves: tuple[Anomalie, ...],
    ) -> None:
        nonlocal atteint_le_plafond
        for entite in conteneur:  # type: ignore[attr-defined]
            if len(trouvees) >= PLAFOND_INSTANCES:
                atteint_le_plafond = True
                return
            type_dxf = entite.dxftype()

            if type_dxf == "DIMENSION":
                # Une contrainte dimensionnelle réutilise l'entité DIMENSION
                # sans en être une : ezdxf ne la prend pas en charge et le dit.
                if getattr(entite, "is_dimensional_constraint", False):
                    continue
                handle = str(entite.dxf.get("handle", "") or "")
                trouvees.append(
                    _Instance(
                        entite=entite,
                        # Le chemin d'insertion PUIS le handle. Au premier
                        # niveau, le chemin est vide et la désignation reste
                        # le handle seul — les citations déjà écrites en base
                        # continuent donc de désigner la même cotation.
                        object_ref="/".join((*chemin_de_handles, handle)),
                        matrice=matrice,
                        reserves=reserves,
                    )
                )
                continue

            if type_dxf != "INSERT":
                continue

            nom = str(entite.dxf.get("name", "") or "")
            if not nom:
                continue
            if nom in chemin_de_blocs:
                # Un cycle. ezdxf le signale à l'audit (code 104) et le
                # fichier est alors refusé en amont ; cette garde est la
                # seconde ligne, pour un cycle qu'un audit tolérant laisserait
                # passer. Sans elle, le parcours est une récursion infinie.
                cycles.add(nom)
                continue
            if len(chemin_de_blocs) >= PLAFOND_IMBRICATION:
                imbrication_trop_profonde.add(nom)
                continue

            bloc = document.blocks.get(nom)  # type: ignore[attr-defined]
            if bloc is None:
                # Une insertion qui nomme un bloc absent : le fichier est
                # incohérent sur ce point, et le reste est lisible.
                continue

            # Une insertion en grille (MINSERT) vaut `row_count × column_count`
            # exemplaires. `virtual_entities()` n'en rend qu'UN — vérifié : une
            # grille 3 × 2 rend une seule DIMENSION — là où `multi_insert()`
            # rend bien les six, chacune à sa place. S'en remettre à la
            # première aurait compté une cote sur six.
            exemplaires = [entite]
            if getattr(entite, "mcount", 1) > 1:
                try:
                    exemplaires = list(entite.multi_insert())
                except Exception:  # pragma: no cover - ezdxf reste en amont
                    exemplaires = [entite]

            handle_de_l_insertion = str(entite.dxf.get("handle", "") or "")
            for rang, exemplaire in enumerate(exemplaires):
                propre = exemplaire.matrix44()
                composee = propre if matrice is None else matrice @ propre
                # Le rang n'apparaît que pour une grille : une insertion
                # ordinaire garde son handle nu, et les citations déjà écrites
                # restent valides.
                designation = handle_de_l_insertion
                if len(exemplaires) > 1:
                    designation = f"{handle_de_l_insertion}#{rang}"

                reserve = _reserve_d_echelle(composee, (*chemin_de_blocs, nom))
                _descendre(
                    bloc,
                    matrice=composee,
                    chemin_de_handles=(*chemin_de_handles, designation),
                    chemin_de_blocs=(*chemin_de_blocs, nom),
                    reserves=reserves if reserve is None else (*reserves, reserve),
                )

    _descendre(
        document.modelspace(),  # type: ignore[attr-defined]
        matrice=None,
        chemin_de_handles=(),
        chemin_de_blocs=(),
        reserves=(),
    )

    if atteint_le_plafond:
        anomalies.append(
            Anomalie(
                "trop_de_cotations",
                f"La récolte s'est arrêtée à {PLAFOND_INSTANCES} cotations. Le "
                "plan en porte davantage : ce qui suit n'a pas été lu, et "
                "aucune lecture ne doit être présentée comme complète.",
            )
        )
    if imbrication_trop_profonde:
        anomalies.append(
            Anomalie(
                "imbrication_trop_profonde",
                f"La descente s'est arrêtée à {PLAFOND_IMBRICATION} niveaux de "
                "blocs. Les cotations plus profondes n'ont pas été lues : "
                f"{', '.join(sorted(imbrication_trop_profonde))}.",
            )
        )
    if cycles:
        anomalies.append(
            Anomalie(
                "cycle_de_blocs_evite",
                "Un bloc s'insère dans lui-même, directement ou par un autre. "
                "La descente s'est arrêtée là plutôt que de boucler, et les "
                f"cotations de ces blocs n'ont pas été lues : {', '.join(sorted(cycles))}.",
            )
        )
    return trouvees


def lire(chemin: str | Path, *, situer: bool = False) -> LecturePlan:
    """Lit un DXF et rend son constat. Ne lève pas sur un fichier abîmé.

    `situer=True` calcule en plus l'étendue du dessin et la position de chaque
    cotation dans cette étendue. Ce n'est pas le défaut parce que cela COÛTE :
    mesuré sur un plan réel de 7,4 Mo, l'étendue demande 1,37 s — les 663
    boîtes de cotation suivantes, elles, ne coûtent que 0,01 s grâce au cache.
    Un appel qui ne veut que l'unité et le nombre de cotations n'a pas à payer
    cette seconde et demie.

    L'ordre des opérations porte la sécurité, et il n'est pas interchangeable :

    1. lecture tolérante, en mode STRICT sur le décodage — `recover` avale
       sinon les erreurs de décodage en silence, et sa documentation prévient
       qu'un fichier ainsi chargé perd de l'information ;
    2. audit, AVANT tout parcours — un cycle de référence de bloc fait du
       parcours une récursion infinie, et ezdxf le signale sans le casser ;
    3. lecture de l'unité — sans elle, aucune mesure n'est permise, mais la
       visualisation reste possible ;
    4. seulement ensuite, les entités.
    """
    # Imports locaux : la dépendance « plans » reste optionnelle.
    from ezdxf import bbox, recover

    constat = LecturePlan()
    chemin = Path(chemin)

    # Un DXF se termine par le groupe `0 / EOF`. Sans lui, le fichier est
    # incomplet — et c'est le seul moment où on peut encore le dire : `recover`
    # ouvre un fichier coupé en plein en-tête SANS lever, sans erreur d'audit
    # et sans correctif signalé, en rendant un document vide. La lecture
    # répondait alors « 0 cotation », exactement comme pour un plan correct qui
    # n'est pas coté — l'utilisateur déposait un fichier abîmé et recevait un
    # silence. Mesuré : les deux plans réels, `mur_simple.dxf` et
    # `sans_unites.dxf` portent ce marqueur ; `tronque.dxf` ne l'a pas.
    #
    # Le contrôle porte sur la FIN du fichier, pas sur son contenu : un plan
    # valide et vide reste accepté, simplement non mesurable. C'est la
    # frontière que fixe `test_a_file_that_parses_but_says_nothing_...`.
    try:
        with chemin.open("rb") as fichier:
            tete = fichier.read(64)
            fichier.seek(0, 2)
            taille = fichier.tell()
            fichier.seek(max(0, taille - 64))
            queue = fichier.read(64)
    except OSError as erreur:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "fichier_illisible",
            f"Le fichier n'a pas pu être ouvert : {type(erreur).__name__}.",
        )
        return constat

    # Le contrôle ne vaut que pour un fichier qui COMMENCE comme un DXF. Un
    # fichier qui n'en est pas un du tout doit être refusé pour cette
    # raison-là, par `recover`, et non pour une troncature qu'il n'a pas.
    debut = [ligne.strip() for ligne in tete.decode("latin-1").splitlines() if ligne.strip()][:2]
    ressemble_a_un_dxf = debut[:2] == ["0", "SECTION"]

    if ressemble_a_un_dxf and not queue.decode("latin-1").strip().endswith("EOF"):
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "fichier_tronque",
            "Le fichier ne se termine pas par le marqueur de fin d'un DXF : il "
            "est incomplet. Le lire rendrait un plan vide, impossible à "
            "distinguer d'un plan correct sans cotation — il est donc refusé, "
            "et le fichier déposé reste téléchargeable.",
        )
        return constat

    try:
        document, auditeur = recover.readfile(str(chemin), errors="strict")
    except UnicodeDecodeError:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "fichier_illisible",
            "Le fichier contient des données binaires que le format ne permet "
            "pas de décoder. Il est refusé plutôt que lu partiellement.",
        )
        return constat
    except Exception as erreur:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "fichier_invalide",
            f"Le fichier n'a pas pu être ouvert comme un DXF : {type(erreur).__name__}.",
        )
        return constat

    codes = {erreur.code for erreur in auditeur.errors}
    if CODE_CYCLE_DE_BLOCS in codes:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "cycle_de_blocs",
            "Le fichier contient un cycle de référence de bloc. Le parcourir "
            "serait une récursion sans fin : il est refusé.",
        )
        return constat

    for constat_audit in auditeur.errors:
        constat.anomalies.append(
            Anomalie(
                "audit",
                f"Audit du fichier, code {constat_audit.code} : {constat_audit.message}",
            )
        )

    entete = document.header
    version = entete.get("$ACADVER", None)
    constat.version_dxf = str(version) if version is not None else None
    insunits = entete.get("$INSUNITS", None)
    constat.insunits = int(insunits) if isinstance(insunits, int) else None
    constat.unite_source, anomalie_unite = _unite_de(constat.insunits)
    if anomalie_unite is not None:
        constat.anomalies.append(anomalie_unite)

    constat.feuilles = list(document.layout_names())

    modelspace = document.modelspace()
    par_type: Counter[str] = Counter()
    par_calque: Counter[str] = Counter()
    for entite in modelspace:
        par_type[entite.dxftype()] += 1
        par_calque[str(entite.dxf.get("layer", "") or "")] += 1
    constat.entites = dict(par_type)
    constat.calques = dict(par_calque)

    # Le cadre est calculé AVANT de parcourir les cotations, et le cache est
    # partagé : c'est lui qui rend les boîtes suivantes quasi gratuites.
    cache = None
    if situer:
        cache = bbox.Cache()
        etendue = bbox.extents(modelspace, cache=cache)
        if etendue.has_data:
            constat.etendue = (
                float(etendue.extmin.x),
                float(etendue.extmin.y),
                float(etendue.extmax.x),
                float(etendue.extmax.y),
            )
        else:
            constat.anomalies.append(
                Anomalie(
                    "dessin_sans_etendue",
                    "Le dessin ne porte aucune géométrie situable : les "
                    "cotations ne peuvent pas être montrées sur l'image.",
                )
            )

    # Les cotations des PRÉSENTATIONS ne sont pas lues, et il faut le dire.
    #
    # Une présentation annote une feuille à imprimer : ses cotes redoublent
    # souvent celles de l'espace modèle, à une échelle de feuille, et les
    # reprendre produirait deux mesures pour un seul ouvrage. On les COMPTE
    # pour pouvoir annoncer ce qui n'a pas été lu — ce qui vaut mieux qu'un
    # silence que l'utilisateur lirait comme « il n'y en a pas ».
    en_presentation = 0
    for feuille in document.layouts:
        if feuille.name == "Model":
            continue
        en_presentation += len(feuille.query("DIMENSION"))
    if en_presentation:
        constat.anomalies.append(
            Anomalie(
                "cotations_en_presentation",
                f"{en_presentation} cotation(s) vivent dans une présentation et "
                "ne sont pas lues : une présentation annote une feuille, et ses "
                "cotes redoublent souvent celles du dessin. Les reprendre "
                "donnerait deux mesures pour un seul ouvrage.",
            )
        )

    for instance in _instances_de_cotation(document, constat.anomalies):
        # Une cotation d'un bloc doit être TRANSFORMÉE avant d'être mesurée.
        # Vérifié : sous une insertion à l'échelle 2, la mesure de l'original
        # reste 5 000 là où celle de la copie transformée vaut 10 000. Lire
        # l'original donnerait une quantité deux fois trop petite, sans que
        # rien ne le signale.
        if instance.matrice is None:
            a_mesurer = instance.entite
        else:
            a_mesurer = instance.entite.copy()  # type: ignore[attr-defined]
            a_mesurer.transform(instance.matrice)  # type: ignore[attr-defined]

        reserve_non_uniforme = any(
            reserve.code == "echelle_de_bloc_non_uniforme" for reserve in instance.reserves
        )
        lue = _lire_une_cotation(
            a_mesurer,
            object_ref=instance.object_ref,
            reserves_du_contexte=instance.reserves,
            # Une échelle non uniforme ne rend pas la mesure fausse : elle la
            # rend indécidable à distance. Elle est donc conservée « à
            # vérifier », jamais écartée ni présentée comme sûre.
            fiabilite_maximale="a_confirmer" if reserve_non_uniforme else "mesurable",
        )
        # Le calque se lit sur l'ORIGINAL : une copie transformée le garde,
        # mais lire l'original évite d'en dépendre.
        calque_source = str(instance.entite.dxf.get("layer", "") or "")  # type: ignore[attr-defined]
        if calque_source and calque_source != lue.calque:
            lue = replace(lue, calque=calque_source)

        if cache is not None and constat.etendue is not None:
            # La boîte est celle de l'entité transformée : c'est la seule qui
            # soit dans le repère du dessin, donc dans celui du rendu. Le cache
            # ne sert que les entités de premier niveau — une copie est neuve à
            # chaque fois, et rien ne peut être réutilisé pour elle.
            # `a_mesurer` est typée `object` : ce module n'importe ezdxf qu'à
            # l'appel, parce que l'extra « plans » est optionnel, et il ne peut
            # donc pas nommer `DXFEntity` dans une annotation.
            boite = bbox.extents([a_mesurer], cache=cache)  # type: ignore[list-item]
            if boite.has_data:
                cadre = _normaliser(
                    (
                        float(boite.extmin.x),
                        float(boite.extmin.y),
                        float(boite.extmax.x),
                        float(boite.extmax.y),
                    ),
                    constat.etendue,
                )
                if cadre is not None:
                    lue = replace(lue, cadre=cadre)
        constat.cotations.append(lue)

    return constat
