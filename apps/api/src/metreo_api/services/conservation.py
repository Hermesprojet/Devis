"""Détruire une organisation : la seule porte, et ce qui la garde fermée.

Ce module tient la politique de conservation décidée pour ce dépôt :

    Rien ne se détruit sans un écrit préalable qui dit ce qui va être détruit,
    et qui survit à la destruction.

**Trois verrous, et aucun n'est de confiance.**

1. *Une décision, pas un nombre.* La durée de conservation vient d'une
   `QuoteRetentionDecision` qui porte sa juridiction, sa source, la date où
   cette source a été consultée, sa date d'effet et son validateur. Un
   `Integer` nullable sur les réglages — ce qu'il y avait d'abord — n'oblige
   personne à dire d'où sort le chiffre. Le dépôt n'en sème aucune : sans
   décision, la destruction est refusée, et le refus conserve.

2. *Une autorisation bornée, vérifiée par la BASE.* Demander n'est pas
   autoriser. `demander()` inscrit et n'ouvre rien ; `autoriser()` ouvre une
   fenêtre de quelques minutes que le déclencheur compare à l'horloge du
   serveur. Une demande abandonnée, une fenêtre expirée, une purge terminée ou
   échouée : aucune ne permet de détruire quoi que ce soit. La première
   version ouvrait dès l'inscription, ce qui faisait d'une demande oubliée un
   droit permanent.

3. *Aucun texte libre durable.* Le motif est un code pris dans une liste
   fermée ; la référence est facultative et contrainte à une forme opaque. Un
   registre censé prouver une destruction sans conserver ce qu'elle a effacé
   ne peut pas offrir une zone où l'on écrit le nom d'un client.

**L'ordre, et pourquoi il n'y en a qu'un de sûr.** Deux ressources sans
transaction commune : des lignes en base, des fichiers sur un volume.

* fichiers d'abord, lignes ensuite — si la base échoue, il reste des lignes qui
  désignent des fichiers absents : un devis qui existe et ne se télécharge
  plus. Le pire des deux.
* lignes d'abord, fichiers ensuite — si le volume échoue, il reste des fichiers
  que plus rien ne désigne. Réparable, à une condition : que l'écrit dise
  lesquels.

C'est donc le second, et l'écrit est `OrganizationPurge.documents`. Une purge
interrompue se reprend par `reprendre()` sans rien redécouvrir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..models import (
    MINUTES_D_AUTORISATION,
    MOTIFS_DE_PURGE,
    IssuedQuote,
    Organization,
    OrganizationPurge,
    QuoteRetentionDecision,
    utcnow,
)
from ..services.document_storage import StockageLocal
from . import audit

#: Ce qu'une référence de dossier a le droit d'être.
#:
#: Pas de blanc, pas de ponctuation de phrase : « DOSSIER-2026-014 » passe,
#: « demande de Jean Dupont » ne passe pas. La contrainte n'est pas cosmétique
#: — c'est ce qui empêche la donnée personnelle de rentrer par la porte prévue
#: pour la faire sortir. Elle ne rend pas l'abus impossible, elle le rend
#: délibéré, ce qui est le maximum qu'un format puisse offrir.
REFERENCE_OPAQUE = re.compile(r"^[A-Za-z0-9._:/-]{1,120}$")


class PurgeRefusee(DomainError):
    """La destruction est refusée, et le message dit par quoi."""

    def __init__(self, code: str, message: str, **contexte: object) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True, slots=True)
class Document:
    """Un PDF à détruire, décrit par ce qui suffit à le retrouver et à le nommer.

    Aucun nom de client ni de chantier : le registre prouve ce qui a été
    détruit, il ne conserve pas ce que la destruction visait à effacer.
    """

    quote_id: str
    number: str
    storage_key: str
    sha256: str

    def en_json(self) -> dict[str, str]:
        return {
            "quote_id": self.quote_id,
            "number": self.number,
            "storage_key": self.storage_key,
            "sha256": self.sha256,
        }


# --------------------------------------------------------------------------
# La décision de conservation
# --------------------------------------------------------------------------


def decision_active(
    session: Session, organization_id: str, *, aujourdhui: date | None = None
) -> QuoteRetentionDecision | None:
    """La décision en vigueur, ou `None` si la question n'a pas été tranchée.

    « En vigueur » se lit sur `effective_from` : une décision datée de demain
    ne s'applique pas aujourd'hui, et une décision remplacée reste lisible
    derrière celle qui la remplace. La plus récente parmi celles déjà entrées
    en vigueur gagne.
    """
    aujourdhui = aujourdhui or utcnow().date()
    return session.scalars(
        select(QuoteRetentionDecision)
        .where(
            QuoteRetentionDecision.organization_id == organization_id,
            QuoteRetentionDecision.effective_from <= aujourdhui,
        )
        .order_by(
            QuoteRetentionDecision.effective_from.desc(),
            QuoteRetentionDecision.created_at.desc(),
        )
        .limit(1)
    ).first()


def decider(
    session: Session,
    *,
    organization_id: str,
    years: int,
    jurisdiction: str,
    source_label: str,
    source_checked_on: date,
    effective_from: date,
    validated_by: str | None = None,
    source_url: str | None = None,
    note: str | None = None,
) -> QuoteRetentionDecision:
    """Enregistre une décision de conservation. Jamais en place : une de plus.

    Aucun paramètre n'a de valeur par défaut parmi les cinq qui fondent la
    décision. C'est voulu : un défaut sur la juridiction, la source ou la date
    d'effet ferait passer une opinion pour une règle.
    """
    if not (0 <= years <= 100):
        raise PurgeRefusee(
            "retention_years_out_of_range",
            "une durée de conservation se compte entre 0 et 100 ans",
            years=years,
        )
    for nom, valeur in (
        ("jurisdiction", jurisdiction),
        ("source_label", source_label),
    ):
        if not (valeur or "").strip():
            raise PurgeRefusee(
                "retention_decision_incomplete",
                f"une décision de conservation sans {nom} n'est pas une décision : "
                "elle ne dit pas de quel droit elle relève ni d'où elle sort",
            )

    decision = QuoteRetentionDecision(
        organization_id=organization_id,
        years=years,
        jurisdiction=jurisdiction.strip()[:10],
        source_label=source_label.strip()[:300],
        source_url=(source_url or None),
        source_checked_on=source_checked_on,
        effective_from=effective_from,
        validated_by=validated_by,
        note=note,
    )
    session.add(decision)
    audit.record(
        session,
        organization_id=organization_id,
        action="organization.retention_decided",
        object_type="quote_retention_decision",
        object_id=decision.id,
        summary=f"conservation des devis émis fixée à {years} an(s)",
        actor_user_id=validated_by,
        payload={
            "years": years,
            "jurisdiction": decision.jurisdiction,
            "effective_from": effective_from.isoformat(),
            "source_checked_on": source_checked_on.isoformat(),
        },
    )
    session.flush()
    return decision


# --------------------------------------------------------------------------
# Le seuil
# --------------------------------------------------------------------------


def echeance(emis_le: datetime, annees: int) -> date:
    """Le jour où la conservation d'un devis cesse d'être exigée.

    Des années CALENDAIRES, et non un nombre de jours moyen. Une première
    version comptait en tranches de 365,25 jours : le seuil tombait alors un
    jour à côté de la date anniversaire, et « sept ans » ne voulait plus dire
    ce que tout le monde entend par sept ans. Un seuil réglementaire qui dérive
    d'un jour est un seuil faux.

    Le 29 février se replie sur le 28 : c'est la convention la plus courante,
    et la seule qui ne fasse pas disparaître une date un an sur quatre.
    """
    jour = emis_le.date()
    try:
        return jour.replace(year=jour.year + annees)
    except ValueError:
        return jour.replace(year=jour.year + annees, day=28)


def devis_retenus(
    session: Session, organization_id: str, *, annees: int, aujourdhui: date
) -> list[IssuedQuote]:
    """Les devis encore dans leur durée de conservation. Vide autorise la purge."""
    devis = session.scalars(
        select(IssuedQuote).where(IssuedQuote.organization_id == organization_id)
    ).all()
    return [d for d in devis if aujourdhui < echeance(d.issued_at, annees)]


def documents_a_detruire(session: Session, organization_id: str) -> list[Document]:
    devis = session.scalars(
        select(IssuedQuote)
        .where(IssuedQuote.organization_id == organization_id)
        .order_by(IssuedQuote.number)
    ).all()
    return [
        Document(
            quote_id=d.id,
            number=d.number,
            storage_key=d.pdf_storage_key,
            sha256=d.pdf_sha256,
        )
        for d in devis
    ]


# --------------------------------------------------------------------------
# La purge : demander, autoriser, exécuter, refermer
# --------------------------------------------------------------------------


def demander(
    session: Session,
    *,
    organization_id: str,
    reason_code: str,
    reference: str | None = None,
    requested_by: str | None = None,
    aujourdhui: date | None = None,
    sans_retention: bool = False,
) -> OrganizationPurge:
    """Inscrit la destruction. **N'autorise rien** : il faut `autoriser()` ensuite.

    La séparation est le correctif de fond. Une demande qui autoriserait
    d'elle-même ferait de toute demande oubliée un droit permanent de détruire
    les devis de cette organisation.

    Cinq refus, dans cet ordre, et aucun n'écrit quoi que ce soit :
    l'organisation n'existe pas ; le motif n'est pas dans la liste fermée ; la
    référence n'est pas opaque ; la conservation n'a pas été décidée ; elle
    n'est pas échue.

    `sans_retention` saute le QUATRIÈME et le CINQUIÈME refus, et eux seuls. Un
    seul appelant l'emploie : `seed --reset`, qui ne détruit que les
    organisations semées par ce module, retrouvées par leur nom exact. Les
    trois premiers refus continuent de s'appliquer — même le jeu de
    démonstration ne se détruit pas sans écrit.
    """
    aujourdhui = aujourdhui or utcnow().date()

    organisation = session.get(Organization, organization_id)
    if organisation is None:
        raise PurgeRefusee(
            "organization_unknown",
            "organisation introuvable : rien à détruire",
            organization_id=organization_id,
        )

    if reason_code not in MOTIFS_DE_PURGE:
        raise PurgeRefusee(
            "purge_reason_unknown",
            f"motif de purge inconnu : « {reason_code} ». "
            f"Motifs admis : {', '.join(MOTIFS_DE_PURGE)}",
            reason_code=reason_code,
        )

    if reference is not None and not REFERENCE_OPAQUE.match(reference):
        raise PurgeRefusee(
            "purge_reference_not_opaque",
            "une référence de dossier ne porte ni blanc ni ponctuation de phrase : "
            "elle désigne un dossier ailleurs, elle ne le raconte pas ici",
            reference_length=len(reference),
        )

    decision: QuoteRetentionDecision | None = None
    if sans_retention:
        annees = 0
    else:
        decision = decision_active(session, organization_id, aujourdhui=aujourdhui)
        if decision is None:
            raise PurgeRefusee(
                "quote_retention_undecided",
                "aucune décision de conservation n'est en vigueur pour cette "
                "organisation. Une durée seule ne suffit pas : il faut sa "
                "juridiction, sa source, la date où cette source a été consultée, "
                "sa date d'effet et son validateur. Tant qu'elle manque, la "
                "destruction est refusée plutôt qu'appliquée sur une durée supposée",
                organization_id=organization_id,
            )
        annees = decision.years
        retenus = devis_retenus(session, organization_id, annees=annees, aujourdhui=aujourdhui)
        if retenus:
            raise PurgeRefusee(
                "quote_retention_not_elapsed",
                f"{len(retenus)} devis émis sont encore dans leur durée de "
                f"conservation de {annees} an(s)",
                organization_id=organization_id,
                retention_years=annees,
                retained=[d.number for d in retenus],
            )

    documents = documents_a_detruire(session, organization_id)
    purge = OrganizationPurge(
        organization_id=organization_id,
        status="requested",
        requested_by=requested_by,
        reason_code=reason_code,
        reference=reference,
        retention_years_applied=annees,
        retention_decision_id=decision.id if decision is not None else None,
        quote_count=len(documents),
        documents=[d.en_json() for d in documents],
        files_deleted=0,
        files_failed=[],
    )
    session.add(purge)

    # Le journal d'audit de l'organisation va disparaître avec elle. On y écrit
    # quand même : tant que la purge n'est pas exécutée, cette ligne est la
    # trace visible d'une demande, et une demande abandonnée doit se voir.
    audit.record(
        session,
        organization_id=organization_id,
        action="organization.purge_requested",
        object_type="organization_purge",
        object_id=purge.id,
        summary=f"destruction demandée : {len(documents)} devis émis",
        actor_user_id=requested_by,
        payload={
            "reason_code": reason_code,
            "retention_years": annees,
            "quote_count": len(documents),
        },
    )
    session.flush()
    return purge


def autoriser(
    session: Session, purge: OrganizationPurge, *, minutes: int | None = None
) -> OrganizationPurge:
    """Ouvre la fenêtre d'exécution. C'est ELLE que la base vérifie.

    Un geste distinct de la demande, et volontairement bref : la fenêtre doit
    couvrir une destruction, pas survivre à l'oubli de celui qui l'a ouverte.
    Passé `authorized_until`, le déclencheur referme sans que personne ait à y
    penser — et il compare à SA propre horloge, pas à celle de l'appelant.
    """
    if purge.status != "requested":
        raise PurgeRefusee(
            "purge_not_pending",
            f"cette purge est en état « {purge.status} » : elle ne peut plus être autorisée",
            purge_id=purge.id,
        )
    ouverture = utcnow()
    purge.authorized_at = ouverture
    purge.authorized_until = ouverture + timedelta(minutes=minutes or MINUTES_D_AUTORISATION)
    purge.status = "executing"
    session.flush()
    return purge


def executer(session: Session, purge: OrganizationPurge) -> OrganizationPurge:
    """Détruit les lignes. Les fichiers viennent après, et séparément.

    Cette fonction s'arrête à `rows_deleted` À DESSEIN : le volume ne participe
    pas à la transaction, et prétendre le contraire produirait le seul état
    vraiment mauvais — une base qui se croit propre et un volume qui ne l'est
    pas, sans écrit pour les réconcilier.
    """
    if purge.status != "executing":
        raise PurgeRefusee(
            "purge_not_authorized",
            f"cette purge est en état « {purge.status} » : sans fenêtre d'exécution "
            "ouverte, la base refusera de détruire quoi que ce soit",
            purge_id=purge.id,
        )

    # Les devis d'abord : `organization_id` retient en RESTRICT, l'organisation
    # ne partirait pas avant eux. Le déclencheur les laisse passer PARCE QUE la
    # fenêtre de cette purge est ouverte et non expirée — la base le vérifie
    # elle-même, ligne par ligne.
    for devis in session.scalars(
        select(IssuedQuote).where(IssuedQuote.organization_id == purge.organization_id)
    ).all():
        session.delete(devis)
    session.flush()

    organisation = session.get(Organization, purge.organization_id)
    if organisation is not None:
        session.delete(organisation)
    session.flush()

    purge.status = "rows_deleted"
    session.flush()
    return purge


def retirer_les_fichiers(
    session: Session, purge: OrganizationPurge, stockage: StockageLocal
) -> OrganizationPurge:
    """Retire du volume ce que le registre a nommé, et referme la purge.

    Idempotente : un fichier déjà absent compte comme retiré. C'est ce qui rend
    `reprendre()` sûr après n'importe quelle interruption.
    """
    if purge.status not in {"rows_deleted", "failed"}:
        raise PurgeRefusee(
            "purge_rows_not_deleted",
            f"cette purge est en état « {purge.status} » : ses lignes ne sont pas encore détruites",
            purge_id=purge.id,
        )

    retires = 0
    echecs: list[dict[str, str]] = []
    for entree in purge.documents:
        cle = str(entree.get("storage_key", ""))
        try:
            stockage.supprimer(cle)
            retires += 1
        except OSError as erreur:  # pragma: no cover - dépend du volume
            echecs.append({"storage_key": cle, "erreur": str(erreur)})

    purge.files_deleted = retires
    purge.files_failed = echecs
    purge.status = "completed" if not echecs else "failed"
    purge.completed_at = utcnow()
    # La fenêtre se referme AVEC la purge : laisser `authorized_until` dans le
    # futur sur une purge terminée n'autoriserait plus rien — le statut suffit —
    # mais laisserait croire à une autorisation vivante en lisant la ligne.
    purge.authorized_until = utcnow()
    session.flush()
    return purge


def reprendre(
    session: Session, purge: OrganizationPurge, stockage: StockageLocal
) -> OrganizationPurge:
    """Achève une purge interrompue, depuis l'état où elle s'est arrêtée.

    Une purge restée en `requested` ou dont la fenêtre a expiré n'est PAS
    reprise ici : il faut la ré-autoriser explicitement. Reprendre en
    rouvrant silencieusement une fenêtre expirée annulerait la borne.
    """
    if purge.status == "completed":
        return purge
    if purge.status == "requested":
        raise PurgeRefusee(
            "purge_not_authorized",
            "cette purge n'a jamais été autorisée : une demande ne se reprend pas, elle s'autorise",
            purge_id=purge.id,
        )
    if purge.status == "executing":
        expiree = purge.authorized_until is None or purge.authorized_until <= utcnow()
        if expiree:
            raise PurgeRefusee(
                "purge_authorization_expired",
                "la fenêtre d'exécution de cette purge est expirée ; "
                "ré-autorisez-la avant de reprendre",
                purge_id=purge.id,
            )
        executer(session, purge)
    return retirer_les_fichiers(session, purge, stockage)


def orphelins(session: Session, stockage: StockageLocal) -> list[str]:
    """Les fichiers qu'une purge a nommés et que le volume porte encore.

    Sert au contrôle, pas au fonctionnement : une purge terminée doit rendre
    une liste vide, et c'est vérifiable sans relire le code qui l'a produite.
    """
    restants: list[str] = []
    for purge in session.scalars(select(OrganizationPurge)).all():
        for entree in purge.documents:
            cle = str(entree.get("storage_key", ""))
            if cle and stockage.chemin(cle).exists():
                restants.append(cle)
    return restants
