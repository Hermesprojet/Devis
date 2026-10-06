"""Poser une échelle sur un PDF, puis en tirer des mesures qui se justifient.

Ce module est la couture entre `mesures_pdf` — qui calcule et ne sait rien de
la base — et les tables qui conservent les décisions humaines. Il n'ajoute
aucune règle de mesure : il décide **ce qui est consigné où**, et rien d'autre.

**Pourquoi une calibration vit en base et un constat de lecture non.** Le
constat se reconstruit depuis l'original immuable : le jeter ne perd rien. Une
calibration, elle, est une **décision** — quelqu'un a regardé un plan, désigné
deux points et déclaré une distance. Rien ne la reconstruit. Elle se conserve
donc au même titre qu'une `ValidationDecision`, et pour la même raison.

**Pourquoi une mesure est une citation et une proposition**, plutôt qu'une
table à elle. Parce que c'est exactement ce qu'elle est : une valeur extraite
d'un document, ancrée à un endroit, qui attend une décision humaine. Le socle
documentaire porte déjà les trois pièces — `SourceCitation`,
`ExtractionProposal`, `ValidationDecision` — et elles sont éprouvées. Lui en
ajouter une quatrième aurait dupliqué la validation humaine, qui est
précisément ce qu'il ne faut pas dupliquer.

**Ce que ce module refuse de faire.** Il ne devine aucune échelle. Un PDF sans
calibration ne rend aucune mesure, et le refus le dit — il ne retombe pas sur
un 1:100 implicite, ni sur l'échelle écrite au cartouche, qui ne vaut plus dès
qu'un export a coché « ajuster à la page ».
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExtractionProposal, PlanCalibration, SourceCitation, ValidationDecision
from . import audit, lecture_de_plan, mesures_pdf
from .document_storage import StockageLocal
from .mesures_pdf import Calibration, Point
from .tenant import owned_query

logger = logging.getLogger("metreo.api")

#: Le nom et la version du schéma de valeur d'une mesure de PDF.
#:
#: Distinct de `mesure_de_plan`, qui désigne une cotation LUE dans un DXF. Les
#: deux ne se valent pas : l'une descend du fichier, l'autre d'une calibration
#: humaine. Les confondre sous un seul nom rendrait impossible de dire, plus
#: tard, laquelle a été obtenue comment.
SCHEMA = "mesure_pdf"
SCHEMA_VERSION = "1"

PIPELINE_VERSION = "mesure-pdf-v1"

#: Aucun modèle de langage n'intervient. Les deux valeurs le DISENT.
PROMPT_VERSION = "none"
MODEL_VERSION = "aucun-modele"

#: L'extracteur d'une mesure de PDF : une personne, pas une bibliothèque.
#:
#: C'est la différence de fond avec une cotation DXF, dont l'extracteur est
#: `lecture_dxf@ezdxf-…`. Ici le fichier n'a rien fourni d'autre qu'une image ;
#: la valeur vient d'un pointage et d'une déclaration. L'écrire ainsi évite
#: qu'une relecture présente une mesure humaine comme une lecture machine.
EXTRACTEUR = "mesure_humaine@pdf"

#: La confiance attachée à une mesure, par classe de fiabilité.
#:
#: **Ces nombres ne sont pas des probabilités mesurées.** Ils ORDONNENT deux
#: classes nommées, parce que la base exige un nombre dans [0,1]. Ce qui porte
#: le sens est l'incertitude, qui est calculée et conservée dans `value`, dans
#: la même unité que la mesure.
CONFIANCE: dict[str, Decimal] = {
    "mesurable": Decimal("0.9"),
    "a_confirmer": Decimal("0.5"),
}

#: La confiance d'une CITATION de mesure, qui n'est pas celle de la mesure.
#:
#: Elle ne vaut PAS 1, à la différence d'une citation de cotation DXF. Un
#: handle DXF désigne un objet sans ambiguïté ; une mesure de PDF désigne un
#: endroit que quelqu'un a pointé à la souris, et ce pointage porte la même
#: incertitude que la mesure elle-même.
CONFIANCE_DE_LA_CITATION = Decimal("0.9")

TypeDeMesure = Literal["segment", "surface"]


class CalibrationRefusee(Exception):
    """La calibration ou la mesure n'a pas pu être posée, et on dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MesureEnregistree:
    """Ce qui a été écrit, pour que l'appelant puisse en rendre compte."""

    proposal_id: str
    citation_id: str
    valeur: Decimal
    unite: str
    incertitude: Decimal
    fiabilite: str
    reserves: tuple[str, ...]


# ---------------------------------------------------------------------------
# La page : ses dimensions et sa rotation, relues du constat
# ---------------------------------------------------------------------------


def _page_du_constat(
    stockage: StockageLocal, *, organization_id: str, revision_id: str, page: int
) -> tuple[tuple[float, float, float, float], int]:
    """La boîte affichée et la rotation d'une page, depuis l'artefact de lecture.

    **Pas en rouvrant le PDF.** Mesuré : charger une page coûte 512 à 3 795 ms,
    et une calibration en demanderait autant à chaque clic. Le constat porte
    déjà ces deux informations, il est déterministe, et il se reconstruit
    depuis l'original si on le perd.
    """
    constat = lecture_de_plan.lire_le_constat(
        stockage, organization_id=organization_id, revision_id=revision_id
    )
    if constat is None:
        raise CalibrationRefusee(
            "plan_non_analyse",
            "Ce plan n'a pas encore été analysé : ses pages ne sont pas connues.",
        )
    if constat.contenu.get("format") != "pdf":
        raise CalibrationRefusee(
            "pas_un_pdf",
            "La calibration ne s'applique qu'à un PDF. Un DXF porte son unité "
            "de dessin dans le fichier : ses cotations se lisent sans échelle.",
        )

    boites = constat.contenu.get("boites_des_pages") or []
    if page < 1 or page > len(boites):
        raise CalibrationRefusee(
            "page_inconnue",
            f"Le document porte {len(boites)} page(s) : la page {page} n'existe pas.",
        )
    gauche, bas, droite, haut = (float(valeur) for valeur in boites[page - 1])

    rotations = constat.contenu.get("rotations_des_pages") or []
    rotation = int(rotations[page - 1]) if page <= len(rotations) else 0
    return (gauche, bas, droite, haut), rotation


# ---------------------------------------------------------------------------
# Poser une calibration
# ---------------------------------------------------------------------------


def calibrer(
    session: Session,
    *,
    stockage: StockageLocal,
    organization_id: str,
    revision_id: str,
    actor_user_id: str,
    page: int,
    premier_ecran: tuple[float, float],
    second_ecran: tuple[float, float],
    distance_reelle: Decimal,
    unite: str,
    resolution_du_pointage: Decimal,
    motif: str,
    zone: tuple[float, float, float, float] | None = None,
) -> PlanCalibration:
    """Enregistre une échelle déclarée, après l'avoir vérifiée.

    Les points arrivent dans le repère de l'ÉCRAN, parce que c'est là qu'ils
    ont été désignés. Ils sont convertis ici, une fois, et conservés dans celui
    de la page : un repère d'écran dépend de la rotation d'affichage et
    normalise x et y par des longueurs différentes, où une diagonale serait
    fausse.

    Lève avant d'écrire quoi que ce soit. Une calibration trop courte, une
    distance nulle ou une unité qui n'est pas une longueur sont refusées par
    `mesures_pdf.facteur`, qui est aussi ce qui les refusera à la mesure — une
    seule règle, à un seul endroit.
    """
    boite, rotation = _page_du_constat(
        stockage,
        organization_id=organization_id,
        revision_id=revision_id,
        page=page,
    )

    premier = mesures_pdf.vers_la_page(premier_ecran, boite, rotation)
    second = mesures_pdf.vers_la_page(second_ecran, boite, rotation)

    candidate = Calibration(
        premier=premier,
        second=second,
        distance_reelle=distance_reelle,
        unite=unite,
        resolution_du_pointage=float(resolution_du_pointage),
    )
    # Pour ses refus : c'est la MÊME fonction qui validera les mesures.
    facteur = mesures_pdf.facteur(candidate)

    calibration = PlanCalibration(
        organization_id=organization_id,
        revision_id=revision_id,
        page=page,
        u0=Decimal(str(premier.u)),
        v0=Decimal(str(premier.v)),
        u1=Decimal(str(second.u)),
        v1=Decimal(str(second.v)),
        distance_reelle=distance_reelle,
        unite=unite,
        resolution_du_pointage=resolution_du_pointage,
        zone_x0=Decimal(str(zone[0])) if zone else None,
        zone_y0=Decimal(str(zone[1])) if zone else None,
        zone_x1=Decimal(str(zone[2])) if zone else None,
        zone_y1=Decimal(str(zone[3])) if zone else None,
        motif=motif,
        actor_user_id=actor_user_id,
    )
    session.add(calibration)
    session.flush()

    audit.record(
        session,
        organization_id=organization_id,
        action="document.plan_calibrated",
        object_type="document_revision",
        object_id=revision_id,
        summary="Échelle d'un plan PDF déclarée",
        # Le facteur et l'unité, pas le motif : le motif est du texte saisi par
        # un humain à propos d'un document client, et le journal n'en porte pas.
        payload={
            "page": page,
            "unite": unite,
            "facteur_par_point": str(facteur),
            "resolution_du_pointage": str(resolution_du_pointage),
            "zone": zone is not None,
        },
    )
    return calibration


def _en_calibration(ligne: PlanCalibration) -> Calibration:
    """La ligne de base vers l'objet de calcul."""
    return Calibration(
        premier=Point(u=float(ligne.u0), v=float(ligne.v0)),
        second=Point(u=float(ligne.u1), v=float(ligne.v1)),
        distance_reelle=ligne.distance_reelle,
        unite=ligne.unite,
        resolution_du_pointage=float(ligne.resolution_du_pointage),
    )


def _contient(ligne: PlanCalibration, points_ecran: list[tuple[float, float]]) -> bool:
    """La zone de cette calibration couvre-t-elle TOUS les points désignés ?

    Tous, et pas le premier : une mesure qui sort de la zone où l'échelle a été
    déclarée n'est pas à moitié juste. Une page porte souvent un plan au 1:50 et
    un détail au 1:20 ; une mesure à cheval sur les deux n'a pas de valeur.
    """
    if ligne.zone_x0 is None:
        return True
    x0, y0 = float(ligne.zone_x0), float(ligne.zone_y0 or 0)
    x1, y1 = float(ligne.zone_x1 or 1), float(ligne.zone_y1 or 1)
    return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points_ecran)


def calibration_applicable(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    page: int,
    points_ecran: list[tuple[float, float]],
) -> PlanCalibration | None:
    """La calibration qui s'applique à ces points, ou `None`.

    Une calibration de ZONE l'emporte sur une calibration de page : elle est
    plus précise, et c'est pour cela qu'on l'a posée. À égalité, la plus
    récente gagne — une nouvelle calibration sur le même périmètre corrige la
    précédente, elle ne la complète pas.
    """
    lignes = session.scalars(
        owned_query(PlanCalibration, organization_id)
        .where(
            PlanCalibration.revision_id == revision_id,
            PlanCalibration.page == page,
        )
        .order_by(PlanCalibration.created_at.desc())
    ).all()

    applicables = [ligne for ligne in lignes if _contient(ligne, points_ecran)]
    if not applicables:
        return None
    # Les zonées d'abord, puis l'ordre de récence déjà posé par la requête.
    applicables.sort(key=lambda ligne: ligne.zone_x0 is None)
    return applicables[0]


# ---------------------------------------------------------------------------
# Mesurer
# ---------------------------------------------------------------------------


def mesurer(
    session: Session,
    *,
    stockage: StockageLocal,
    organization_id: str,
    revision_id: str,
    page: int,
    type_de_mesure: TypeDeMesure,
    points_ecran: list[tuple[float, float]],
    libelle: str,
) -> MesureEnregistree:
    """Mesure un segment ou une surface, et l'écrit comme une proposition.

    `libelle` est ce que la personne a tapé pour désigner son ouvrage — « mur
    nord », « dalle du séjour ». Il est conservé dans la valeur : une liste de
    mesures sans libellé est une liste de nombres que personne ne sait relire.
    """
    if len(points_ecran) < 2:
        raise CalibrationRefusee("points_insuffisants", "Une mesure demande au moins deux points.")

    calibration = calibration_applicable(
        session,
        organization_id=organization_id,
        revision_id=revision_id,
        page=page,
        points_ecran=points_ecran,
    )
    if calibration is None:
        raise CalibrationRefusee(
            "sans_calibration",
            "Aucune échelle n'a été confirmée pour cette page, ou aucune ne "
            "couvre tous les points désignés. Un PDF ne porte pas d'unité : "
            "sans échelle déclarée, il n'y a rien à mesurer. Posez une "
            "calibration sur une cote connue — une cote longue donne le "
            "meilleur résultat.",
        )

    boite, rotation = _page_du_constat(
        stockage,
        organization_id=organization_id,
        revision_id=revision_id,
        page=page,
    )
    points = [mesures_pdf.vers_la_page(point, boite, rotation) for point in points_ecran]

    outil = mesures_pdf.longueur if type_de_mesure == "segment" else mesures_pdf.aire
    mesure = outil(points, _en_calibration(calibration))

    # La citation : la page, et la boîte englobante de ce qui a été désigné.
    # C'est ce qui permettra de retrouver la mesure sur l'aperçu — et c'est
    # l'ancrage que la migration e2f3a4b50607 a rendu possible.
    cadre = _boite_englobante(points_ecran)
    citation = SourceCitation(
        organization_id=organization_id,
        revision_id=revision_id,
        page=page,
        char_start=None,
        char_end=None,
        x0=Decimal(str(cadre[0])),
        y0=Decimal(str(cadre[1])),
        x1=Decimal(str(cadre[2])),
        y1=Decimal(str(cadre[3])),
        sheet=None,
        layer=None,
        object_id=None,
        extractor=EXTRACTEUR,
        confidence=CONFIANCE_DE_LA_CITATION,
    )
    session.add(citation)
    session.flush()

    proposition = ExtractionProposal(
        organization_id=organization_id,
        revision_id=revision_id,
        citation_id=citation.id,
        schema_name=SCHEMA,
        schema_version=SCHEMA_VERSION,
        value=_valeur_de(
            mesure,
            type_de_mesure=type_de_mesure,
            libelle=libelle,
            points_ecran=points_ecran,
            calibration=calibration,
        ),
        confidence=CONFIANCE[mesure.fiabilite],
        pipeline_version=PIPELINE_VERSION,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
        status="proposed",
    )
    session.add(proposition)
    session.flush()

    audit.record(
        session,
        organization_id=organization_id,
        action="document.plan_measured",
        object_type="document_revision",
        object_id=revision_id,
        summary="Mesure prise sur un plan PDF",
        # Ni le libellé ni la valeur : le premier est du texte saisi à propos
        # d'un document client, la seconde est une donnée de métré. Le journal
        # porte des COMPTEURS et des codes.
        payload={
            "page": page,
            "type": type_de_mesure,
            "points": len(points_ecran),
            "unite": mesure.unite,
            "fiabilite": mesure.fiabilite,
            "reserves": list(mesure.reserves),
            "calibration_id": calibration.id,
            "pipeline_version": PIPELINE_VERSION,
        },
    )

    return MesureEnregistree(
        proposal_id=proposition.id,
        citation_id=citation.id,
        valeur=mesure.valeur,
        unite=mesure.unite,
        incertitude=mesure.incertitude,
        fiabilite=mesure.fiabilite,
        reserves=mesure.reserves,
    )


def _boite_englobante(
    points: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """La boîte des points désignés, élargie si elle est plate.

    Un segment parfaitement horizontal a une hauteur nulle, et une boîte plate
    est refusée par `ck_source_citation_bbox` — à juste titre, puisqu'elle ne
    désigne aucune surface d'écran. Un millième de page suffit à la rendre
    valide sans déplacer quoi que ce soit : c'est deux pixels sur un aperçu de
    deux mille.
    """
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    epaisseur = 0.001
    if x1 - x0 < epaisseur:
        x0, x1 = max(0.0, x0 - epaisseur), min(1.0, x1 + epaisseur)
    if y1 - y0 < epaisseur:
        y0, y1 = max(0.0, y0 - epaisseur), min(1.0, y1 + epaisseur)
    return x0, y0, x1, y1


def _valeur_de(
    mesure: mesures_pdf.Mesure,
    *,
    type_de_mesure: TypeDeMesure,
    libelle: str,
    points_ecran: list[tuple[float, float]],
    calibration: PlanCalibration,
) -> dict[str, Any]:
    """Ce qui est conservé d'une mesure, et pourquoi chaque champ y est.

    Les points sont conservés : sans eux, une mesure ne se redessine pas sur
    l'aperçu, et « retrouver la mesure sur le plan » redevient impossible.

    L'incertitude est conservée dans la même unité que la valeur, et avec elle
    la calibration dont elle descend. C'est ce qui permet, six mois plus tard,
    de répondre à « d'où vient ce 12,4 m² ? » autrement que par « de l'écran ».
    """
    return {
        "type": type_de_mesure,
        "libelle": libelle,
        # En CHAÎNE : les contrats refusent les flottants, et `Amount` quantise
        # à dix décimales. Le nombre écrit doit être celui du calcul.
        "valeur": str(mesure.valeur),
        "unite": mesure.unite,
        "incertitude": str(mesure.incertitude),
        "incertitude_relative": str(mesure.incertitude_relative),
        "fiabilite": mesure.fiabilite,
        "reserves": list(mesure.reserves),
        "points_ecran": [[x, y] for x, y in points_ecran],
        "calibration": {
            "id": calibration.id,
            "unite": calibration.unite,
            "distance_reelle": str(calibration.distance_reelle),
            "resolution_du_pointage": str(calibration.resolution_du_pointage),
            "motif": calibration.motif,
            "zonee": calibration.zone_x0 is not None,
        },
    }


# ---------------------------------------------------------------------------
# Relire
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MesureALire:
    """Une mesure telle que l'écran la montre, décision humaine comprise."""

    proposal_id: str
    citation_id: str
    page: int
    type: str
    libelle: str
    valeur: str
    unite: str
    incertitude: str
    incertitude_relative: str
    fiabilite: str
    reserves: tuple[str, ...]
    points_ecran: tuple[tuple[float, float], ...]
    cadre: tuple[str, str, str, str] | None
    calibration: dict[str, Any]
    decision: str | None
    valeur_corrigee: str | None


def lister(session: Session, *, organization_id: str, revision_id: str) -> list[MesureALire]:
    """Les mesures d'une révision, avec la dernière décision de chacune.

    La décision n'est pas lue sur la proposition : la proposition machine n'est
    jamais réécrite. L'état d'une mesure se DÉDUIT de son journal de décisions,
    dont la dernière l'emporte — c'est ce qui garantit qu'une acceptation ne
    peut pas être effacée par une nouvelle mesure.
    """
    rangees = session.execute(
        owned_query(ExtractionProposal, organization_id)
        .join(
            SourceCitation,
            (SourceCitation.id == ExtractionProposal.citation_id)
            & (SourceCitation.organization_id == ExtractionProposal.organization_id),
        )
        .where(
            ExtractionProposal.revision_id == revision_id,
            ExtractionProposal.schema_name == SCHEMA,
        )
        .with_only_columns(ExtractionProposal, SourceCitation)
        .order_by(ExtractionProposal.created_at)
    ).all()

    identifiants = [proposition.id for proposition, _ in rangees]
    dernieres: dict[str, ValidationDecision] = {}
    if identifiants:
        for decision in session.scalars(
            select(ValidationDecision)
            .where(
                ValidationDecision.organization_id == organization_id,
                ValidationDecision.proposal_id.in_(identifiants),
            )
            .order_by(ValidationDecision.created_at)
        ).all():
            dernieres[decision.proposal_id] = decision

    mesures: list[MesureALire] = []
    for proposition, citation in rangees:
        valeur = dict(proposition.value)
        derniere = dernieres.get(proposition.id)
        apres = derniere.after_value if derniere is not None else None
        cadre = None
        if citation.x0 is not None:
            cadre = (str(citation.x0), str(citation.y0), str(citation.x1), str(citation.y1))
        mesures.append(
            MesureALire(
                proposal_id=proposition.id,
                citation_id=citation.id,
                page=int(citation.page or 1),
                type=str(valeur.get("type", "")),
                libelle=str(valeur.get("libelle", "")),
                valeur=str(valeur.get("valeur", "")),
                unite=str(valeur.get("unite", "")),
                incertitude=str(valeur.get("incertitude", "")),
                incertitude_relative=str(valeur.get("incertitude_relative", "")),
                fiabilite=str(valeur.get("fiabilite", "")),
                reserves=tuple(str(r) for r in valeur.get("reserves", [])),
                points_ecran=tuple(
                    (float(point[0]), float(point[1]))
                    for point in valeur.get("points_ecran", [])
                    if isinstance(point, list) and len(point) == 2
                ),
                cadre=cadre,
                calibration=dict(valeur.get("calibration") or {}),
                decision=derniere.decision if derniere is not None else None,
                valeur_corrigee=(str(apres.get("valeur")) if isinstance(apres, dict) else None),
            )
        )
    return mesures


def lister_les_calibrations(
    session: Session, *, organization_id: str, revision_id: str
) -> list[PlanCalibration]:
    """Les échelles déclarées sur cette révision, la plus récente d'abord."""
    return list(
        session.scalars(
            owned_query(PlanCalibration, organization_id)
            .where(PlanCalibration.revision_id == revision_id)
            .order_by(PlanCalibration.created_at.desc())
        ).all()
    )


def facteur_lisible(calibration: PlanCalibration) -> str:
    """« 50 mm par point », pour l'écran et pour le journal.

    Rendu comme une chaîne : c'est une information à LIRE, pas à recalculer.
    Un écran qui en referait l'arithmétique obtiendrait un second facteur, et
    les deux finiraient par diverger.
    """
    facteur = mesures_pdf.facteur(_en_calibration(calibration))
    return f"{_sans_zeros_inutiles(facteur)} {calibration.unite} par point"


def _sans_zeros_inutiles(valeur: Decimal) -> str:
    """« 50 » plutôt que « 50.0000000000 », et « 33.333333 » reste entier.

    Le facteur est quantisé à dix décimales pour l'arithmétique — c'est ce que
    `Amount` conserve — mais ces dix décimales sont du bruit sur un écran. Un
    utilisateur qui lit « 50.0000000000 mm par point » apprend la même chose
    que devant « 50 mm », et doute un peu plus.

    `normalize()` seul ne suffit pas : sur 50, il rend `5E+1`, qui est pire.
    """
    reduite = valeur.normalize()
    _, _, exposant = reduite.as_tuple()
    if isinstance(exposant, int) and exposant > 0:
        reduite = reduite.quantize(Decimal(1))
    return f"{reduite}"


def en_json(calibration: PlanCalibration) -> str:
    """La calibration, pour un artefact ou une trace. Sans le motif."""
    return json.dumps(
        {
            "id": calibration.id,
            "page": calibration.page,
            "unite": calibration.unite,
            "distance_reelle": str(calibration.distance_reelle),
            "resolution_du_pointage": str(calibration.resolution_du_pointage),
        },
        ensure_ascii=False,
    )
