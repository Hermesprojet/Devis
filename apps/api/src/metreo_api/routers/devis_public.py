"""La page que le destinataire d'un devis ouvre, sans compte Metreo.

**Ce que le serveur reçoit, et par où.** Le secret du lien ne passe jamais par
un chemin ni par une chaîne de requête : il arrive dans le CORPS d'un `POST`,
une seule fois, et repart aussitôt sous la forme d'une session courte en
cookie `HttpOnly`. Un journal d'accès, un en-tête `Referer` ou l'historique du
navigateur ne peuvent donc pas le contenir.

**Ce qui n'est jamais rendu.** Aucun coût interne, sous aucune forme : le
partage d'un devis émis avec `include_internal_costs` est refusé en amont, et
la vue publique ne lit que l'instantané du document — jamais les tables
vivantes, jamais les réglages du jour.

**Ce que ces pages ne prétendent pas être.** Une acceptation enregistrée ici
est une réponse commerciale déclarative. Ce n'est pas une signature
électronique qualifiée, et aucune identité n'est vérifiée. L'écran le dit,
et ces routes ne prétendent rien de plus.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import IssuedQuote, QuoteShareLink, utcnow
from ..schemas import (
    PublicQuoteLine,
    PublicQuoteView,
    PublicReceipt,
    PublicResponseRequest,
    PublicSessionRequest,
)
from ..services import cycle_devis, partage
from ..services.document_storage import ContenuRefuse, StockageLocal
from ..transactions import RouteTransactionnelle

router = APIRouter(prefix="/public", tags=["devis-public"], route_class=RouteTransactionnelle)

#: Une politique qui n'autorise que ce que la page utilise réellement : rien.
#: Ces routes ne rendent que du JSON et un PDF ; aucune n'a besoin de script,
#: de style, d'image ni de cadre. `frame-ancestors 'none'` interdit en outre
#: l'intégration dans un site tiers, qui permettrait de faire cliquer un
#: visiteur sur « Accepter » sans qu'il voie ce qu'il accepte.
CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

#: Les en-têtes que TOUTE réponse publique porte, succès comme refus.
ENTETES = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": CSP,
}


def _durcir(reponse: Response) -> None:
    for nom, valeur in ENTETES.items():
        reponse.headers[nom] = valeur


def _refuser(exc: DomainError, code_http: int) -> HTTPException:
    return HTTPException(status_code=code_http, detail=exc.to_dict(), headers=dict(ENTETES))


def _jeton(request: Request) -> str:
    jeton = request.cookies.get(partage.COOKIE)
    if not jeton:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "no_public_session",
                "message": "Ouvrez le lien que l'entreprise vous a communiqué.",
            },
            headers=dict(ENTETES),
        )
    return jeton


def _ouvert(session: Session, request: Request) -> tuple[QuoteShareLink, IssuedQuote]:
    try:
        return partage.devis_de_session(session, jeton=_jeton(request))
    except DomainError as exc:
        raise _refuser(exc, status.HTTP_401_UNAUTHORIZED) from exc


@router.post(
    "/quote-sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Échanger le secret du lien contre une session courte",
)
def open_session(
    payload: PublicSessionRequest,
    reponse: Response,
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Le seul endroit où le secret existe côté serveur, et il n'y reste pas.

    Il sert à retrouver un lien par son empreinte, puis il est oublié : ni
    journalisé, ni renvoyé, ni stocké. Le cookie posé en retour est
    `HttpOnly` — le script de la page ne peut pas le lire — et `SameSite=Lax`,
    ce qui suffit à empêcher un site tiers de déclencher une acceptation à
    l'insu du visiteur.
    """
    try:
        _lien, jeton = partage.ouvrir_une_session(session, secret=payload.secret)
    except DomainError as exc:
        raise _refuser(exc, status.HTTP_404_NOT_FOUND) from exc

    vide = Response(status_code=status.HTTP_204_NO_CONTENT)
    vide.set_cookie(
        partage.COOKIE,
        jeton,
        max_age=partage.MINUTES_DE_SESSION * 60,
        httponly=True,
        samesite="lax",
        # `Secure` dès qu'on sert en HTTPS. En développement local, sur
        # `http://localhost`, un cookie `Secure` ne serait jamais posé et la
        # page ne fonctionnerait pas du tout.
        secure=settings.is_production,
        path="/api/v1/public",
    )
    _durcir(vide)
    del reponse
    return vide


def _lignes(devis: IssuedQuote) -> list[PublicQuoteLine]:
    lignes = (devis.document_snapshot or {}).get("lines") or []
    return [
        PublicQuoteLine(
            position=str(ligne.get("position", "")),
            designation=str(ligne.get("designation", "")),
            unit=str(ligne.get("unit", "")),
            quantity=str(ligne.get("quantity", "")),
            unit_price_ht=str(ligne.get("unit_price_ht", "")),
            total_ht=str(ligne.get("selling_price_ht", "")),
        )
        for ligne in lignes
    ]


def _adresse(bloc: dict[str, Any]) -> list[str]:
    lignes = []
    for cle in ("billing_address",):
        valeur = (bloc.get(cle) or "").strip()
        if valeur:
            lignes.append(valeur)
    ville = " ".join(
        part
        for part in ((bloc.get("postal_code") or "").strip(), (bloc.get("city") or "").strip())
        if part
    )
    if ville:
        lignes.append(ville)
    return lignes


@router.get("/quote", response_model=PublicQuoteView, summary="Le devis, côté client")
def read_public_quote(
    request: Request,
    session: Session = Depends(session_scope),
) -> Response:
    """Tout vient de l'INSTANTANÉ. Rien n'est relu dans les tables vivantes.

    C'est ce qui rend la page identique à ce que le PDF imprime, et immunisée
    contre une modification ultérieure de la fiche client ou des réglages.

    L'ouverture de la page inscrit une consultation, une seule fois : rafraîchir
    ne remplit pas la chronologie, et une consultation ne fait jamais régresser
    un devis déjà accepté ou refusé.
    """
    _lien, devis = _ouvert(session, request)
    aujourdhui = utcnow().date()
    evenements = cycle_devis.journal(session, devis)
    if not any(e.kind == "viewed" for e in evenements):
        cycle_devis.inscrire(session, devis=devis, kind="viewed", channel="public_link")
        evenements = cycle_devis.journal(session, devis)

    etat = cycle_devis.etat(devis, evenements, aujourdhui=aujourdhui)
    organisation = devis.organization_snapshot or {}
    totaux = (devis.document_snapshot or {}).get("totals") or {}

    peut, motif = _peut_repondre(etat, devis, aujourdhui)
    vue = PublicQuoteView(
        number=devis.number,
        issued_at=devis.issued_at,
        valid_until=devis.valid_until,
        organization_name=str(organisation.get("name", "")),
        organization_legal_name=organisation.get("legal_name"),
        organization_company_number=organisation.get("company_number"),
        client_name=str((devis.client_snapshot or {}).get("name", "")),
        client_address_lines=_adresse(devis.client_snapshot or {}),
        project_reference=str((devis.project_snapshot or {}).get("reference", "")),
        project_name=str((devis.project_snapshot or {}).get("name", "")),
        lines=_lignes(devis),
        total_ht=str(totaux.get("total_ht") or "0"),
        taxes=list(totaux.get("taxes") or []),
        total_ttc=str(totaux.get("total_ttc") or "0"),
        currency=str(totaux.get("currency") or "EUR"),
        terms=devis.terms,
        pdf_sha256=devis.pdf_sha256,
        pdf_byte_size=devis.pdf_byte_size,
        state=_etat_public(etat),
        can_respond=peut,
        cannot_respond_reason=motif,
    )
    reponse = Response(
        content=vue.model_dump_json(), media_type="application/json", status_code=200
    )
    _durcir(reponse)
    return reponse


def _etat_public(etat: cycle_devis.Etat) -> Any:
    from ..schemas import QuoteStateOut

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


def _peut_repondre(
    etat: cycle_devis.Etat, devis: IssuedQuote, aujourdhui: Any
) -> tuple[bool, str | None]:
    if etat.decision is not None:
        return False, (
            f"Vous avez déjà répondu à ce devis le "
            f"{etat.decided_at.strftime('%d/%m/%Y') if etat.decided_at else ''}."
        )
    if devis.valid_until < aujourdhui:
        # Consultable, mais plus acceptable : le document reste lisible tant
        # que le lien vit, et c'est la validité qui ferme la réponse.
        return False, (
            f"Ce devis n'était valable que jusqu'au "
            f"{devis.valid_until.strftime('%d/%m/%Y')}. Demandez une offre à jour."
        )
    return True, None


@router.get(
    "/quote/document.pdf",
    summary="Le PDF du devis, tel qu'il a été émis",
    response_class=Response,
)
def download_public_pdf(
    request: Request,
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Les octets du volume, jamais un document recomposé.

    C'est le même fichier que l'entreprise télécharge, au bit près : le
    destinataire et l'émetteur regardent le même document, et son empreinte le
    prouve des deux côtés.
    """
    _lien, devis = _ouvert(session, request)
    stockage = StockageLocal(settings.storage_root)
    try:
        octets = stockage.chemin(devis.pdf_storage_key).read_bytes()
    except (OSError, ContenuRefuse) as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "document_missing",
                "message": "Ce document n'est plus disponible. Contactez l'entreprise.",
            },
            headers=dict(ENTETES),
        ) from exc

    nom = "".join(c if c.isalnum() or c in "-_." else "-" for c in f"devis-{devis.number}")
    reponse = Response(
        content=octets,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nom}.pdf"',
            "Content-Length": str(len(octets)),
            "X-Quote-Sha256": devis.pdf_sha256,
        },
    )
    _durcir(reponse)
    return reponse


@router.post(
    "/quote/response",
    response_model=PublicReceipt,
    summary="Accepter ou refuser le devis",
)
def respond(
    payload: PublicResponseRequest,
    request: Request,
    session: Session = Depends(session_scope),
) -> Response:
    """La réponse du client. Rejouée à l'identique, elle rend le même reçu.

    L'identité est DÉCLARATIVE : on demande un nom et une adresse pour pouvoir
    dire de qui vient la réponse, on ne les vérifie pas, et l'écran l'annonce.
    Ce n'est pas une signature électronique qualifiée.
    """
    _lien, devis_lu = _ouvert(session, request)
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "confirmation_required",
                "message": "Confirmez explicitement votre réponse avant de l'envoyer.",
            },
            headers=dict(ENTETES),
        )
    if payload.decision == "accepted" and not (payload.respondent_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "respondent_required",
                "message": "Indiquez votre nom pour accepter ce devis.",
            },
            headers=dict(ENTETES),
        )

    #: Verrouillé AVANT de relire le journal : deux réponses opposées lancées
    #: en même temps se sérialisent ici, et la seconde voit la première.
    devis = cycle_devis.verrouiller(session, devis_lu.organization_id, devis_lu.id)
    try:
        evenement, cree = cycle_devis.repondre(
            session,
            devis=devis,
            decision=payload.decision,
            channel="public_link",
            respondent_name=(payload.respondent_name or "").strip() or None,
            respondent_email=payload.respondent_email,
            comment=(payload.comment or "").strip() or None,
        )
    except DomainError as exc:
        code = getattr(exc, "code", "")
        raise _refuser(
            exc,
            status.HTTP_409_CONFLICT
            if code in {"quote_already_answered", "quote_expired"}
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc

    recu = PublicReceipt(
        number=devis.number,
        decision=evenement.kind,
        decision_label=cycle_devis.LIBELLES[evenement.kind],
        decided_at=evenement.effective_at,
        respondent_name=evenement.respondent_name,
        pdf_sha256=devis.pdf_sha256,
        created=cree,
    )
    reponse = Response(
        content=recu.model_dump_json(), media_type="application/json", status_code=200
    )
    _durcir(reponse)
    return reponse
