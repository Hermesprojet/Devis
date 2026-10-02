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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
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


def _lire_une_cotation(entite: object) -> Cotation:
    dxf = entite.dxf  # type: ignore[attr-defined]
    object_ref = str(dxf.get("handle", "") or "")
    calque = str(dxf.get("layer", "") or "")
    famille = _FAMILLES.get(int(dxf.get("dimtype", 0)) & 7, "inconnue")
    anomalies: list[Anomalie] = []

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

    fiabilite: Fiabilite = "mesurable"

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


def lire(chemin: str | Path) -> LecturePlan:
    """Lit un DXF et rend son constat. Ne lève pas sur un fichier abîmé.

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
    from ezdxf import recover  # import local : la dépendance reste optionnelle

    constat = LecturePlan()
    chemin = Path(chemin)

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

    for cotation in modelspace.query("DIMENSION"):
        # Une contrainte dimensionnelle réutilise l'entité DIMENSION sans en
        # être une : ezdxf ne la prend pas en charge et le dit.
        if getattr(cotation, "is_dimensional_constraint", False):
            continue
        constat.cotations.append(_lire_une_cotation(cotation))

    return constat
