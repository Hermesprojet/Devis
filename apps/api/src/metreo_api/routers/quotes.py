"""Le cycle commercial d'un devis remis, côté entreprise.

Créer et révoquer le lien de consultation, enregistrer une transmission ou une
réponse obtenue hors ligne, corriger une saisie, et lire la chronologie — plus
la vue inter-chantiers de tous les devis.

Rien ici ne touche au devis ni à son PDF : ils sont immuables. Tout ce qui
évolue s'écrit dans `quote_events`, que la base refuse de modifier.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import IssuedQuote, QuoteEvent, QuoteShareLink, User, utcnow
from ..schemas import (
    IssuedQuoteDetail,
    IssuedQuoteOut,
    Page,
    QuoteBoardPage,
    QuoteBoardRow,
    QuoteCorrectionCreate,
    QuoteEventCreate,
    QuoteEventOut,
    QuoteStateOut,
    ShareLinkCreated,
    ShareLinkOut,
    ShareLinkRequest,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import cycle_devis, partage
from ..services.tenant import get_owned, owned_query
from ..transactions import RouteTransactionnelle

router = APIRouter(tags=["devis"], route_class=RouteTransactionnelle)

#: Les libellés de la chronologie. Le même vocabulaire que l'écran.
LIBELLES_EVENEMENTS = {
    "link_created": "Lien de consultation créé",
    "link_revoked": "Lien révoqué",
    "transmitted": "Transmis au client",
    "viewed": "Consulté par le client",
    "accepted": "Accepté par le client",
    "declined": "Refusé par le client",
    "correction": "Correction",
}


def _refus(exc: DomainError, defaut: int = status.HTTP_409_CONFLICT) -> HTTPException:
    codes = {
        "internal_costs_not_shareable": status.HTTP_409_CONFLICT,
        "quote_expired": status.HTTP_409_CONFLICT,
        "quote_already_answered": status.HTTP_409_CONFLICT,
        "link_not_found": status.HTTP_404_NOT_FOUND,
        "event_not_found": status.HTTP_404_NOT_FOUND,
        "already_corrected": status.HTTP_409_CONFLICT,
        "correction_of_correction": status.HTTP_409_CONFLICT,
        "unknown_decision": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        status_code=codes.get(getattr(exc, "code", ""), defaut), detail=exc.to_dict()
    )


def _etat_rendu(etat: cycle_devis.Etat) -> QuoteStateOut:
    return QuoteStateOut(
        code=etat.code,
        label=etat.label,
        decision=etat.decision,
        transmitted_at=etat.transmitted_at,
        viewed_at=etat.viewed_at,
        decided_at=etat.decided_at,
        last_activity_at=etat.last_activity_at,
        expired=etat.expired,
    )


def _evenements_rendus(evenements: list[QuoteEvent]) -> list[QuoteEventOut]:
    barres = cycle_devis.annules(evenements)
    motifs = {
        e.corrects_event_id: e.correction_reason
        for e in evenements
        if e.kind == "correction" and e.corrects_event_id
    }
    return [
        QuoteEventOut(
            id=e.id,
            kind=e.kind,
            kind_label=LIBELLES_EVENEMENTS.get(e.kind, e.kind),
            channel=e.channel,
            actor_email=e.actor_email,
            respondent_name=e.respondent_name,
            respondent_email=e.respondent_email,
            comment=e.comment,
            effective_at=e.effective_at,
            recorded_at=e.recorded_at,
            corrected=e.id in barres,
            correction_reason=motifs.get(e.id) or e.correction_reason,
            corrects_event_id=e.corrects_event_id,
        )
        for e in evenements
    ]


def _lien_rendu(lien: QuoteShareLink, *, maintenant: datetime) -> ShareLinkOut:
    return ShareLinkOut(
        id=lien.id,
        created_at=lien.created_at,
        expires_at=lien.expires_at,
        revoked_at=lien.revoked_at,
        active=lien.revoked_at is None and lien.expires_at > maintenant,
    )


def _total_ttc(devis: IssuedQuote) -> tuple[str, str]:
    totaux = (devis.document_snapshot or {}).get("totals") or {}
    return str(totaux.get("total_ttc") or "0"), str(totaux.get("currency") or "EUR")


# ---------------------------------------------------------------------------
# La vue inter-chantiers
# ---------------------------------------------------------------------------


@router.get("/quotes", response_model=QuoteBoardPage, summary="Tous les devis émis")
def list_quotes(
    q: str | None = Query(default=None, description="Numéro, client ou chantier"),
    state: str | None = Query(default=None, description="Filtrer sur l'état commercial"),
    issued_from: date | None = Query(default=None),
    issued_to: date | None = Query(default=None),
    expiring_within_days: int | None = Query(default=None, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> QuoteBoardPage:
    """Le suivi commercial, tous chantiers confondus.

    L'état est CALCULÉ, donc il ne se filtre pas en SQL : les devis retenus par
    les autres critères sont chargés, leur état déduit, puis la sélection sur
    l'état s'applique. C'est le prix d'un état qui ne peut pas mentir, et il se
    paie sur des volumes qui restent ceux d'une entreprise de construction.
    """
    requete = owned_query(IssuedQuote, context.organization_id)
    if issued_from:
        requete = requete.where(
            IssuedQuote.issued_at >= datetime.combine(issued_from, datetime.min.time())
        )
    if issued_to:
        requete = requete.where(
            IssuedQuote.issued_at <= datetime.combine(issued_to, datetime.max.time())
        )

    devis = list(session.scalars(requete.order_by(IssuedQuote.issued_at.desc())).all())
    if q:
        # Numéro, client et chantier se cherchent ENSEMBLE, et en Python : le
        # nom du client et la référence du chantier vivent dans l'instantané,
        # pas dans une colonne — c'est ce qui les rend stables quand la fiche
        # ou le chantier changent. Un `LIKE` SQL n'atteindrait que le numéro et
        # laisserait croire que la recherche porte sur les trois.
        motif = q.strip().lower()
        devis = [
            d
            for d in devis
            if motif in d.number.lower()
            or motif in str(d.client_snapshot.get("name", "")).lower()
            or motif in str(d.project_snapshot.get("reference", "")).lower()
            or motif in str(d.project_snapshot.get("name", "")).lower()
        ]

    aujourdhui = utcnow().date()
    lignes: list[QuoteBoardRow] = []
    for un_devis in devis:
        etat = cycle_devis.etat(
            un_devis, cycle_devis.journal(session, un_devis), aujourdhui=aujourdhui
        )
        if state and etat.code != state:
            continue
        if expiring_within_days is not None:
            reste = (un_devis.valid_until - aujourdhui).days
            if reste < 0 or reste > expiring_within_days or etat.decision is not None:
                continue
        ttc, devise = _total_ttc(un_devis)
        lignes.append(
            QuoteBoardRow(
                id=un_devis.id,
                number=un_devis.number,
                client_name=str(un_devis.client_snapshot.get("name", "")),
                project_id=un_devis.project_id,
                project_reference=str(un_devis.project_snapshot.get("reference", "")),
                project_name=str(un_devis.project_snapshot.get("name", "")),
                total_ttc=ttc,
                currency=devise,
                issued_at=un_devis.issued_at,
                valid_until=un_devis.valid_until,
                state=_etat_rendu(etat),
                has_active_link=partage.lien_actif(
                    partage.liens_du_devis(session, un_devis), maintenant=utcnow()
                )
                is not None,
            )
        )

    total = len(lignes)
    return QuoteBoardPage(
        items=lignes[offset : offset + limit],
        page=Page(total=total, limit=limit, offset=offset),
    )


# ---------------------------------------------------------------------------
# La fiche d'un devis
# ---------------------------------------------------------------------------


def _devis(session: Session, context: TenantContext, quote_id: str) -> IssuedQuote:
    return get_owned(session, IssuedQuote, context.organization_id, quote_id, label="Devis émis")


@router.get(
    "/issued-quotes/{quote_id}",
    response_model=IssuedQuoteDetail,
    summary="Fiche d'un devis remis",
)
def read_quote(
    quote_id: str,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> IssuedQuoteDetail:
    devis = _devis(session, context, quote_id)
    evenements = cycle_devis.journal(session, devis)
    maintenant = utcnow()
    ttc, devise = _total_ttc(devis)
    emetteur = session.get(User, devis.issued_by) if devis.issued_by else None
    return IssuedQuoteDetail(
        quote=IssuedQuoteOut(
            id=devis.id,
            number=devis.number,
            project_id=devis.project_id,
            estimate_id=devis.estimate_id,
            estimate_version_id=devis.estimate_version_id,
            client_id=devis.client_id,
            client_name=str(devis.client_snapshot.get("name", "")),
            issued_at=devis.issued_at,
            valid_until=devis.valid_until,
            terms=devis.terms,
            include_internal_costs=devis.include_internal_costs,
            pdf_sha256=devis.pdf_sha256,
            pdf_byte_size=devis.pdf_byte_size,
            version_number=int(devis.project_snapshot.get("version_number") or 0),
            issued_by_email=emetteur.email if emetteur else None,
        ),
        state=_etat_rendu(cycle_devis.etat(devis, evenements, aujourdhui=maintenant.date())),
        events=_evenements_rendus(evenements),
        links=[
            _lien_rendu(lien, maintenant=maintenant)
            for lien in partage.liens_du_devis(session, devis)
        ],
        project_reference=str(devis.project_snapshot.get("reference", "")),
        project_name=str(devis.project_snapshot.get("name", "")),
        client_snapshot=devis.client_snapshot,
        total_ttc=ttc,
        currency=devise,
    )


# ---------------------------------------------------------------------------
# Le lien de consultation
# ---------------------------------------------------------------------------


@router.post(
    "/issued-quotes/{quote_id}/share-links",
    response_model=ShareLinkCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un lien de consultation",
)
def create_share_link(
    quote_id: str,
    payload: ShareLinkRequest,
    context: TenantContext = Depends(require(Permission.EXPORT_CLIENT)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> ShareLinkCreated:
    """Rend le secret UNE fois. La base n'en garde que l'empreinte.

    `export:client` et non `estimate:write` : ce geste met le DOCUMENT entre
    les mains du client, exactement comme le téléchargement du PDF. Un rôle
    qui n'a pas le droit d'exporter un devis n'a pas celui de le publier.
    """
    devis = _devis(session, context, quote_id)
    try:
        cree = partage.creer(session, context=context, devis=devis, jours=payload.days)
    except DomainError as exc:
        raise _refus(exc) from exc
    #: Le secret voyage dans le FRAGMENT : le navigateur ne l'envoie jamais au
    #: serveur, il n'apparaît donc ni dans un journal d'accès, ni dans un
    #: en-tête `Referer`.
    return ShareLinkCreated(
        link=_lien_rendu(cree.lien, maintenant=utcnow()),
        url=f"{settings.public_base_url}/devis#{cree.secret}",
    )


@router.delete(
    "/issued-quotes/{quote_id}/share-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Révoquer un lien de consultation",
)
def revoke_share_link(
    quote_id: str,
    link_id: str,
    context: TenantContext = Depends(require(Permission.EXPORT_CLIENT)),
    session: Session = Depends(session_scope),
) -> None:
    devis = _devis(session, context, quote_id)
    try:
        partage.revoquer(session, context=context, devis=devis, lien_id=link_id)
    except DomainError as exc:
        raise _refus(exc) from exc


# ---------------------------------------------------------------------------
# Ce que l'entreprise enregistre elle-même
# ---------------------------------------------------------------------------


@router.post(
    "/issued-quotes/{quote_id}/events",
    response_model=IssuedQuoteDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer une transmission ou une réponse hors ligne",
)
def record_event(
    quote_id: str,
    payload: QuoteEventCreate,
    context: TenantContext = Depends(require(Permission.ESTIMATE_WRITE)),
    session: Session = Depends(session_scope),
) -> IssuedQuoteDetail:
    """Le parcours sans portail : on envoie soi-même, on note la réponse.

    Tant qu'aucun domaine n'héberge la page publique, c'est le chemin
    principal — et il le restera pour les réponses obtenues au téléphone ou en
    rendez-vous, qui n'ont jamais transité par un lien.
    """
    devis = cycle_devis.verrouiller(session, context.organization_id, quote_id)
    try:
        if payload.kind == "transmitted":
            cycle_devis.inscrire(
                session,
                devis=devis,
                kind="transmitted",
                channel=payload.channel,
                actor_user_id=context.user.id,
                actor_email=context.user.email,
                comment=payload.comment,
                effective_at=payload.effective_at,
            )
        else:
            if not (payload.comment or "").strip():
                # Une décision hors ligne n'a aucune trace ailleurs : la note
                # est ce qui permettra, dans six mois, de dire d'où elle vient.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "offline_note_required",
                        "message": (
                            "Une réponse enregistrée hors ligne demande une note ou "
                            "une référence : courriel reçu, appel du 12/03, "
                            "compte rendu de réunion."
                        ),
                    },
                )
            cycle_devis.repondre(
                session,
                devis=devis,
                decision=payload.kind,
                channel=payload.channel,
                respondent_name=payload.respondent_name,
                respondent_email=payload.respondent_email,
                comment=payload.comment,
                effective_at=payload.effective_at,
                actor_user_id=context.user.id,
                actor_email=context.user.email,
            )
    except DomainError as exc:
        raise _refus(exc) from exc
    return read_quote(quote_id, context=context, session=session)


@router.post(
    "/issued-quotes/{quote_id}/events/{event_id}/correction",
    response_model=IssuedQuoteDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Corriger un événement par un événement compensatoire",
)
def correct_event(
    quote_id: str,
    event_id: str,
    payload: QuoteCorrectionCreate,
    context: TenantContext = Depends(require(Permission.ESTIMATE_WRITE)),
    session: Session = Depends(session_scope),
) -> IssuedQuoteDetail:
    """Barre un événement sans l'effacer, motif obligatoire.

    La base refuse `UPDATE` et `DELETE` sur le journal. Corriger, ici, c'est
    ajouter la ligne qui dit que la précédente était fausse — et pourquoi.
    """
    devis = _devis(session, context, quote_id)
    try:
        cycle_devis.corriger(
            session,
            context=context,
            devis=devis,
            evenement_id=event_id,
            motif=payload.reason,
            commentaire=payload.comment,
        )
    except DomainError as exc:
        raise _refus(exc) from exc
    return read_quote(quote_id, context=context, session=session)
