"""L'état commercial d'un devis remis, déduit de son journal.

Trois choses distinctes, et c'est la séparation qui fait tenir le reste :

* le DEVIS et son PDF, immuables — rien ici ne les touche ;
* son ÉTAT commercial, qui évolue ;
* l'HISTORIQUE des événements, qui ne se réécrit jamais.

L'état n'est stocké nulle part. C'est une fonction pure du journal et de la
date du jour. Deux conséquences voulues : « Expiré » est exact à la seconde
sans aucune tâche planifiée, et aucun état enregistré ne peut diverger de
l'histoire qui le justifie.

L'échelle des états n'est pas un simple ordre chronologique. Une décision
finale — acceptée ou refusée — l'emporte sur tout le reste : une consultation
tardive ne fait jamais régresser un devis accepté. Et créer un lien n'est pas
transmettre : `link_created` ne monte pas d'un cran.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..models import DECISIONS, IssuedQuote, QuoteEvent, utcnow
from ..security.auth import TenantContext
from . import audit
from .locking import lock_owned

#: Les états que l'entreprise voit, et leur libellé.
LIBELLES: dict[str, str] = {
    "issued": "Émis",
    "transmitted": "Transmis",
    "viewed": "Consulté",
    "accepted": "Accepté",
    "declined": "Refusé",
    "expired": "Expiré",
}

#: L'échelle des états d'AVANCEMENT, du plus faible au plus fort. Les décisions
#: et l'expiration ne s'y trouvent pas : elles ne se comparent pas, elles
#: tranchent.
PROGRESSION = ("issued", "transmitted", "viewed")

#: Le genre d'événement qui fait monter d'un cran, et le cran atteint.
MONTEES: dict[str, str] = {"transmitted": "transmitted", "viewed": "viewed"}


class DecisionConflit(DomainError):
    """Une seconde réponse finale, opposée à celle qui est déjà enregistrée."""

    def __init__(self, existante: str, demandee: str, quand: datetime) -> None:
        super().__init__(
            f"Ce devis a déjà reçu une réponse « {LIBELLES[existante]} » "
            f"le {quand.strftime('%d/%m/%Y')}. Il ne peut pas être "
            f"« {LIBELLES[demandee]} » ensuite.",
            recorded=existante,
            requested=demandee,
        )
        self.code = "quote_already_answered"


class ReponseRefusee(DomainError):
    """Une réponse que l'état du devis n'autorise pas."""

    def __init__(self, code: str, message: str, **contexte: Any) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True, slots=True)
class Etat:
    """L'état commercial, et les dates qui l'expliquent."""

    code: str
    label: str
    decision: str | None
    transmitted_at: datetime | None
    viewed_at: datetime | None
    decided_at: datetime | None
    last_activity_at: datetime | None
    expired: bool


# ---------------------------------------------------------------------------
# Lire le journal
# ---------------------------------------------------------------------------


def annules(evenements: Iterable[QuoteEvent]) -> set[str]:
    """Les événements qu'une correction ultérieure a barrés.

    Barrés, et non effacés : l'original reste lisible dans la chronologie, avec
    le motif de sa correction en regard. C'est la seule façon de corriger une
    saisie interne sans réécrire l'histoire.
    """
    return {
        evenement.corrects_event_id
        for evenement in evenements
        if evenement.kind == "correction" and evenement.corrects_event_id
    }


def actifs(evenements: Sequence[QuoteEvent]) -> list[QuoteEvent]:
    """Ce qui compte encore : ni corrigé, ni lui-même une correction."""
    barres = annules(evenements)
    return [
        evenement
        for evenement in evenements
        if evenement.kind != "correction" and evenement.id not in barres
    ]


def decision_active(evenements: Sequence[QuoteEvent]) -> QuoteEvent | None:
    """La réponse finale qui vaut, s'il y en a une."""
    finales = [e for e in actifs(evenements) if e.kind in DECISIONS]
    if not finales:
        return None
    return sorted(finales, key=lambda e: (e.recorded_at, e.id))[0]


def etat(devis: IssuedQuote, evenements: Sequence[QuoteEvent], *, aujourdhui: date) -> Etat:
    """L'état commercial, déduit — jamais lu dans une colonne."""
    vivants = actifs(evenements)
    finale = decision_active(evenements)
    premiers: dict[str, datetime] = {}
    for evenement in sorted(vivants, key=lambda e: (e.effective_at, e.recorded_at, e.id)):
        premiers.setdefault(evenement.kind, evenement.effective_at)

    derniere = max((e.recorded_at for e in vivants), default=None)

    if finale is not None:
        code = finale.kind
    elif devis.valid_until < aujourdhui:
        code = "expired"
    else:
        atteints = [MONTEES[e.kind] for e in vivants if e.kind in MONTEES]
        code = max(atteints, key=PROGRESSION.index) if atteints else "issued"

    return Etat(
        code=code,
        label=LIBELLES[code],
        decision=finale.kind if finale is not None else None,
        transmitted_at=premiers.get("transmitted"),
        viewed_at=premiers.get("viewed"),
        decided_at=finale.effective_at if finale is not None else None,
        last_activity_at=derniere,
        #: Un devis accepté après sa date de validité reste « Accepté » ; le
        #: drapeau dit quand même que la date est passée, ce qui explique
        #: pourquoi plus aucune réponse n'est acceptée.
        expired=devis.valid_until < aujourdhui,
    )


def journal(session: Session, devis: IssuedQuote) -> list[QuoteEvent]:
    return list(
        session.scalars(
            select(QuoteEvent)
            .where(QuoteEvent.issued_quote_id == devis.id)
            .order_by(QuoteEvent.recorded_at, QuoteEvent.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# Écrire le journal
# ---------------------------------------------------------------------------


def inscrire(
    session: Session,
    *,
    devis: IssuedQuote,
    kind: str,
    channel: str | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    respondent_name: str | None = None,
    respondent_email: str | None = None,
    comment: str | None = None,
    effective_at: datetime | None = None,
    corrects_event_id: str | None = None,
    correction_reason: str | None = None,
    request_id: str | None = None,
) -> QuoteEvent:
    """Ajoute une ligne au journal. Rien n'y est jamais modifié ni retiré."""
    maintenant = utcnow()
    evenement = QuoteEvent(
        organization_id=devis.organization_id,
        issued_quote_id=devis.id,
        #: L'empreinte du document AU MOMENT de l'événement, recopiée dans la
        #: ligne : elle dit de quel PDF il est question sans dépendre d'une
        #: jointure qui pourrait, un jour, désigner autre chose.
        pdf_sha256=devis.pdf_sha256,
        kind=kind,
        channel=channel,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        respondent_name=respondent_name,
        respondent_email=respondent_email,
        comment=comment,
        effective_at=effective_at or maintenant,
        recorded_at=maintenant,
        corrects_event_id=corrects_event_id,
        correction_reason=correction_reason,
        request_id=request_id,
    )
    session.add(evenement)
    session.flush()
    return evenement


def verrouiller(session: Session, organization_id: str, quote_id: str) -> IssuedQuote:
    """Le devis, tenu jusqu'à la fin de la transaction.

    Deux réponses finales opposées lancées en même temps se sérialisent ici :
    la seconde n'entre qu'une fois la première validée, relit le journal, et se
    heurte à la décision qui s'y trouve désormais.
    """
    return lock_owned(session, IssuedQuote, organization_id, quote_id, label="Devis émis")


def repondre(
    session: Session,
    *,
    devis: IssuedQuote,
    decision: str,
    channel: str,
    respondent_name: str | None,
    respondent_email: str | None,
    comment: str | None,
    effective_at: datetime | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    aujourdhui: date | None = None,
    request_id: str | None = None,
) -> tuple[QuoteEvent, bool]:
    """Enregistre la réponse du client. Rend (événement, vraiment_créé).

    Idempotente : rejouer exactement la même décision rend l'événement déjà
    enregistré, sans en écrire un second. Une décision OPPOSÉE lève
    `DecisionConflit` — l'appelant en fait un 409.
    """
    if decision not in DECISIONS:
        raise ReponseRefusee("unknown_decision", f"Réponse inconnue : {decision}.")

    evenements = journal(session, devis)
    deja = decision_active(evenements)
    if deja is not None:
        if deja.kind == decision:
            return deja, False
        raise DecisionConflit(deja.kind, decision, deja.effective_at)

    jour = aujourdhui or utcnow().date()
    if decision == "accepted" and devis.valid_until < jour:
        raise ReponseRefusee(
            "quote_expired",
            f"Ce devis n'était valable que jusqu'au "
            f"{devis.valid_until.strftime('%d/%m/%Y')}. Demandez une nouvelle offre.",
            valid_until=devis.valid_until.isoformat(),
        )

    evenement = inscrire(
        session,
        devis=devis,
        kind=decision,
        channel=channel,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        respondent_name=respondent_name,
        respondent_email=respondent_email,
        comment=comment,
        effective_at=effective_at,
        request_id=request_id,
    )
    audit.record(
        session,
        organization_id=devis.organization_id,
        action=f"quote.{decision}",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Devis {devis.number} : {LIBELLES[decision].lower()}",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        payload={
            "number": devis.number,
            "channel": channel,
            "respondent_name": respondent_name,
            "pdf_sha256": devis.pdf_sha256,
        },
    )
    return evenement, True


def corriger(
    session: Session,
    *,
    context: TenantContext,
    devis: IssuedQuote,
    evenement_id: str,
    motif: str,
    commentaire: str | None = None,
) -> QuoteEvent:
    """Barre un événement par un événement compensatoire, motif obligatoire.

    Ni modification ni suppression : la base les refuse, et c'est voulu. Une
    saisie interne fautive — « transmis par courriel » alors que c'était par
    téléphone — se corrige en le DISANT, pas en effaçant la trace.
    """
    original = session.get(QuoteEvent, evenement_id)
    if (
        original is None
        or original.issued_quote_id != devis.id
        or original.organization_id != context.organization_id
    ):
        raise ReponseRefusee("event_not_found", "Cet événement n'appartient pas à ce devis.")
    if original.kind == "correction":
        raise ReponseRefusee(
            "correction_of_correction",
            "Une correction ne se corrige pas : ajoutez l'événement qui manque.",
        )
    deja = session.scalars(
        select(QuoteEvent).where(QuoteEvent.corrects_event_id == original.id)
    ).first()
    if deja is not None:
        raise ReponseRefusee("already_corrected", "Cet événement a déjà été corrigé.")

    correction = inscrire(
        session,
        devis=devis,
        kind="correction",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        comment=commentaire,
        corrects_event_id=original.id,
        correction_reason=motif,
    )
    audit.record(
        session,
        organization_id=devis.organization_id,
        action="quote.event_corrected",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Devis {devis.number} : événement corrigé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"corrected_event": original.id, "kind": original.kind, "reason": motif},
    )
    return correction
