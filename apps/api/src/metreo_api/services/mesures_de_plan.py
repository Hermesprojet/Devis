"""Faire d'un constat de lecture des propositions de mesure, et rien de plus.

Ce service écrit en base ce que `lecture_dxf` a constaté : une **citation**
par cotation — feuille, calque, handle d'objet, et la position dans l'image —
et une **proposition** portant la mesure. Il n'approuve rien, ne convertit
rien, ne calcule aucune quantité de bordereau.

**Trois règles qui ne se négocient pas.**

1. *Conserver n'est pas proposer.* Une cotation `inexploitable` ne donne
   aucune proposition : il n'y a rien à proposer. Une cotation
   `a_confirmer` en donne une, avec sa réserve attachée et une confiance plus
   basse — c'est la différence entre « je ne sais pas » et « je sais mal ».
2. *Aucune conversion d'unité.* La mesure part dans l'unité du document. Le
   pourquoi est écrit là où on le lira : voir la docstring de
   `ExtractionProposal` dans `models.py`.
3. *Une relecture n'efface pas une décision humaine.* Relancer la lecture du
   même fichier ne recrée pas les propositions déjà posées. Elles portent
   peut-être une acceptation ou une correction, et une étape machine ne défait
   pas le travail d'une personne.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExtractionProposal, SourceCitation, ValidationDecision
from . import audit, lisible
from .lecture_dxf import Cotation, LecturePlan
from .tenant import owned_query

#: Le nom et la version du schéma de valeur. Changer la forme de `value`
#: oblige à monter la version : une proposition relue doit pouvoir dire sous
#: quelle forme elle a été écrite.
SCHEMA = "mesure_de_plan"
SCHEMA_VERSION = "1"

#: La version du pipeline, qui entre dans la clé d'idempotence d'une étape.
PIPELINE_VERSION = "lecture-de-plan-v1"

#: Aucun modèle de langage n'intervient. Les deux valeurs le DISENT, au lieu
#: de laisser un lecteur supposer qu'un modèle a parlé.
PROMPT_VERSION = "none"
MODEL_VERSION = "aucun-modele"

#: La confiance attachée à une mesure, par classe de fiabilité.
#:
#: **Ces deux nombres ne sont pas des probabilités mesurées.** Personne n'a
#: estimé qu'une cotation sans réserve est juste neuf fois sur dix. Ils
#: ORDONNENT deux classes nommées, parce que la base exige un nombre dans
#: [0,1] et qu'il faut bien en écrire un.
#:
#: Ce qui porte le sens est la classe et sa réserve, toutes deux conservées
#: dans `value`. Un écran qui n'afficherait que « 0,9 » mentirait par
#: omission : la réserve est le fait, le nombre n'en est que le rang.
#:
#: Pourquoi pas 1,0 pour une cotation sans réserve : parce que le lecteur ne
#: peut pas savoir si le DESSIN est juste. Il sait seulement que rien, dans le
#: fichier, ne contredit la mesure.
CONFIANCE: dict[str, Decimal] = {
    "mesurable": Decimal("0.9"),
    "a_confirmer": Decimal("0.5"),
}

#: La confiance d'une CITATION, qui n'est pas celle de la mesure.
#:
#: Elle vaut 1 parce qu'un handle DXF désigne un objet sans ambiguïté : il n'y
#: a pas de « peut-être cet objet ». C'est l'emplacement qui est certain, pas
#: la valeur qu'on en tire.
CONFIANCE_DE_LA_CITATION = Decimal("1")


class MesuresRefusees(Exception):
    """Aucune mesure ne peut être proposée, et on dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MesureEnregistree:
    """Ce qui a été écrit, pour que l'appelant puisse en rendre compte."""

    proposal_id: str
    citation_id: str
    object_ref: str


#: Les familles dont la valeur est un ANGLE, et non une longueur.
FAMILLES_ANGULAIRES: frozenset[str] = frozenset({"angulaire", "angulaire_3_points"})


def unite_de_la_cote(
    famille: str, unite_du_document: str | None, origine: str | None
) -> str | None:
    """L'unité dans laquelle une cotation est réellement exprimée.

    **Le défaut que cette fonction ferme, trouvé sur un plan réel.** Chaque
    cotation recevait l'unité de longueur du document. Une cotation angulaire
    de 82° s'affichait donc « 1.431 cm ».

    **Et l'unité d'un angle dépend de son ORIGINE**, mesuré sur ce même plan :
    la cote stockée par le logiciel de dessin (groupe 42) est en radians, comme
    le veut le format — 1,431163 ; le recalcul de la bibliothèque DXF rend des
    degrés — 81,9996. Une valeur angulaire porte donc `rad` ou `deg` selon
    d'où elle vient, et jamais une unité de longueur.

    Appliquée aussi à la LECTURE, pour que les propositions déjà écrites avec
    la mauvaise unité se lisent juste.
    """
    if famille in FAMILLES_ANGULAIRES:
        return lisible.UNITE_D_ANGLE if origine == "cote_42" else lisible.UNITE_DE_DEGRE
    return unite_du_document


@dataclass(frozen=True)
class MesureALire:
    """Une mesure telle que l'écran la montre, décision humaine comprise."""

    proposal_id: str
    citation_id: str
    valeur_document: str
    unite_document: str | None
    famille: str
    fiabilite: str
    origine_de_la_mesure: str
    texte_impose: str | None
    reserves: tuple[dict[str, str], ...]
    confiance: str
    calque: str | None
    feuille: str | None
    object_ref: str | None
    #: La position dans l'image, quand elle est connue.
    cadre: tuple[str, str, str, str] | None
    #: La dernière décision humaine, ou `None` si personne ne s'est prononcé.
    decision: str | None
    #: La valeur retenue par l'humain, s'il a corrigé.
    valeur_corrigee: str | None


def _valeur_de(cotation: Cotation, constat: LecturePlan) -> dict[str, object]:
    """La forme documentée dans la docstring d'`ExtractionProposal`."""
    return {
        "famille": cotation.famille,
        # En CHAÎNE : les contrats refusent les flottants, et `Amount`
        # quantise à dix décimales. Le nombre écrit doit être celui du
        # fichier, pas son arrondi de transport.
        "valeur_document": str(cotation.valeur),
        "unite_document": unite_de_la_cote(
            cotation.famille, constat.unite_source, cotation.origine
        ),
        "insunits": constat.insunits,
        "origine_de_la_mesure": cotation.origine,
        "texte_impose": cotation.texte_impose,
        "fiabilite": cotation.fiabilite,
        "reserves": [
            {"code": anomalie.code, "message": anomalie.message} for anomalie in cotation.anomalies
        ],
    }


def enregistrer(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    constat: LecturePlan,
    extracteur: str,
) -> list[MesureEnregistree]:
    """Écrit une citation et une proposition par cotation reprenable.

    Lève `MesuresRefusees` quand le constat interdit toute mesure — fichier
    refusé, ou unité inconnue. Ce n'est pas un échec technique : c'est le
    constat lui-même, et il doit remonter tel quel à l'utilisateur, qui peut
    encore vouloir VOIR son plan.
    """
    if constat.refuse:
        motif = constat.motif_du_refus
        raise MesuresRefusees(
            motif.code if motif is not None else "fichier_refuse",
            motif.message if motif is not None else "Le fichier a été refusé à la lecture.",
        )
    if constat.unite_source is None:
        raise MesuresRefusees(
            "unite_inconnue",
            "Le document ne déclare pas d'unité exploitable : aucune mesure "
            "ne peut en être tirée sans confirmation humaine. Le plan reste "
            "consultable.",
        )

    # Ce qui a DÉJÀ été proposé pour cette révision par le même extracteur,
    # désigné par le handle de son objet. Une relecture ne les recrée pas :
    # elles portent peut-être une décision humaine.
    deja: set[str] = {
        handle
        for handle in session.scalars(
            owned_query(SourceCitation, organization_id)
            .where(
                SourceCitation.revision_id == revision_id,
                SourceCitation.extractor == extracteur,
                SourceCitation.object_id.is_not(None),
            )
            .with_only_columns(SourceCitation.object_id)
        ).all()
        if handle is not None
    }

    feuille = constat.feuilles[0] if constat.feuilles else None
    ecrites: list[MesureEnregistree] = []

    for cotation in constat.cotations:
        if cotation.fiabilite == "inexploitable" or cotation.valeur is None:
            continue
        # Un handle vide ne désigne rien : la base le refuserait, et elle a
        # raison. Une cotation sans handle n'est pas reprenable, parce qu'on
        # ne saurait pas la retrouver pour la vérifier.
        if not cotation.object_ref.strip():
            continue
        if cotation.object_ref in deja:
            continue

        citation = SourceCitation(
            organization_id=organization_id,
            revision_id=revision_id,
            page=None,
            char_start=None,
            char_end=None,
            x0=Decimal(str(cotation.cadre.x0)) if cotation.cadre else None,
            y0=Decimal(str(cotation.cadre.y0)) if cotation.cadre else None,
            x1=Decimal(str(cotation.cadre.x1)) if cotation.cadre else None,
            y1=Decimal(str(cotation.cadre.y1)) if cotation.cadre else None,
            sheet=feuille,
            layer=cotation.calque or None,
            object_id=cotation.object_ref,
            extractor=extracteur,
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
            value=_valeur_de(cotation, constat),
            confidence=CONFIANCE[cotation.fiabilite],
            pipeline_version=PIPELINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
            status="proposed",
        )
        session.add(proposition)
        session.flush()
        ecrites.append(
            MesureEnregistree(
                proposal_id=proposition.id,
                citation_id=citation.id,
                object_ref=cotation.object_ref,
            )
        )
        deja.add(cotation.object_ref)

    audit.record(
        session,
        organization_id=organization_id,
        action="document.plan_measured",
        object_type="document_revision",
        object_id=revision_id,
        summary="Mesures proposées depuis un plan",
        # Des COMPTEURS et des codes, jamais un nom de calque ni une valeur :
        # le journal ne porte pas de contenu de document.
        payload={
            "cotations_lues": len(constat.cotations),
            "propositions_ecrites": len(ecrites),
            "deja_proposees": len(deja) - len(ecrites),
            "unite_document": constat.unite_source,
            "pipeline_version": PIPELINE_VERSION,
        },
    )
    return ecrites


def lister(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
) -> list[MesureALire]:
    """Les mesures d'une révision, avec la dernière décision de chacune.

    La décision n'est pas lue sur la proposition : la proposition machine
    n'est jamais réécrite, et deux tests du dépôt le vérifient. L'état d'une
    mesure se DÉDUIT donc de son journal de décisions, dont la dernière
    l'emporte. C'est plus coûteux à lire, et c'est ce qui garantit qu'une
    acceptation ne peut pas être effacée par une relecture.
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
        .order_by(SourceCitation.object_id)
    ).all()

    identifiants = [proposition.id for proposition, _ in rangees]
    # La dernière décision de chaque proposition : la boucle écrase, donc la
    # plus récente reste. Un `ORDER BY created_at` croissant suffit, et évite
    # une sous-requête que SQLite et PostgreSQL optimisent différemment.
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
        # Nom distinct de la variable de boucle ci-dessus : le même nom
        # porterait deux types, et mypy refuse — à juste titre.
        derniere = dernieres.get(proposition.id)
        apres = derniere.after_value if derniere is not None else None
        cadre = None
        if citation.x0 is not None and citation.y0 is not None:
            cadre = (str(citation.x0), str(citation.y0), str(citation.x1), str(citation.y1))
        reserves = tuple(
            {"code": str(r.get("code", "")), "message": str(r.get("message", ""))}
            for r in valeur.get("reserves", [])
        )
        mesures.append(
            MesureALire(
                proposal_id=proposition.id,
                citation_id=citation.id,
                valeur_document=str(valeur.get("valeur_document", "")),
                unite_document=unite_de_la_cote(
                    str(valeur.get("famille", "")),
                    valeur.get("unite_document"),
                    valeur.get("origine_de_la_mesure"),
                ),
                famille=str(valeur.get("famille", "")),
                fiabilite=str(valeur.get("fiabilite", "")),
                origine_de_la_mesure=str(valeur.get("origine_de_la_mesure", "")),
                texte_impose=valeur.get("texte_impose"),
                reserves=reserves,
                confiance=str(proposition.confidence),
                calque=citation.layer,
                feuille=citation.sheet,
                object_ref=citation.object_id,
                cadre=cadre,
                decision=derniere.decision if derniere is not None else None,
                valeur_corrigee=(
                    str(apres.get("valeur_document")) if isinstance(apres, dict) else None
                ),
            )
        )
    return mesures
