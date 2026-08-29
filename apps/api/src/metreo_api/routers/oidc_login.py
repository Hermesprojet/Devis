"""Le parcours de connexion : départ, retour, échange.

Trois requêtes, dans cet ordre :

1. ``GET  /auth/oidc/start``     — ouvre une transaction, redirige vers le
   fournisseur.
2. ``GET  /auth/oidc/callback``  — reçoit le code, vérifie tout, rend un code
   de connexion opaque à l'application.
3. ``POST /auth/oidc/exchange``  — échange ce code contre la session.

Le jeton n'apparaît qu'à la troisième, dans un corps de réponse. Il ne
transite jamais par une URL.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import ExternalIdentity, LoginTransaction, Membership, utcnow
from ..schemas import OidcExchangeRequest, OidcStartOut, TokenResponse
from ..security.auth import issue_token
from ..services import oidc

router = APIRouter(prefix="/auth/oidc", tags=["auth"])


def _require_oidc(settings: Settings) -> None:
    """Refus fermé : pas de configuration, pas de parcours.

    Un 404 plutôt qu'un 500 : hors du mode OIDC ces routes n'existent pas, et
    annoncer « mal configuré » à un visiteur anonyme renseigne inutilement.
    """
    if settings.auth_mode != "oidc" or not settings.oidc_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "oidc_disabled",
                "message": "La connexion par fournisseur d'identité n'est pas configurée.",
            },
        )


def _safe_return_to(valeur: str | None) -> str | None:
    """N'accepte qu'un chemin interne.

    Sans ce filtre, `?return_to=https://ailleurs.invalid` ferait de l'écran de
    connexion un tremplin de redirection ouverte : un lien d'apparence
    légitime, vers notre domaine, qui dépose l'utilisateur ailleurs.
    """
    if not valeur:
        return None
    decoupe = urlsplit(valeur)
    if decoupe.scheme or decoupe.netloc or not valeur.startswith("/"):
        return None
    return valeur


def _erreur(exc: oidc.OidcError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/start", response_model=OidcStartOut, summary="Commencer une connexion")
def start(
    return_to: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(session_scope),
) -> OidcStartOut:
    _require_oidc(settings)
    try:
        metadata = oidc.discover(settings)
        url = oidc.start(session, settings, metadata=metadata, return_to=_safe_return_to(return_to))
    except oidc.OidcError as exc:
        raise _erreur(exc) from exc
    return OidcStartOut(authorization_url=url)


@router.get("/callback", summary="Retour du fournisseur d'identité")
def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(session_scope),
) -> RedirectResponse:
    """Vérifie tout, puis renvoie le navigateur avec un code opaque.

    Aucune erreur n'est rendue en JSON ici : l'utilisateur est dans son
    navigateur, il doit revenir sur une page de l'application. Le motif voyage
    comme un code court, pas comme un message technique.
    """
    _require_oidc(settings)
    base = settings.oidc_redirect_uri

    def _retour(**parametres: str) -> RedirectResponse:
        separateur = "&" if "?" in base else "?"
        return RedirectResponse(f"{base}{separateur}{urlencode(parametres)}", status_code=303)

    if error:
        # Le fournisseur a refusé (consentement annulé, compte bloqué…).
        return _retour(login_error="provider_refused")
    if not code or not state:
        return _retour(login_error="invalid_request")

    try:
        transaction = oidc._consume_transaction(session, state)
        metadata = oidc.discover(settings)
        jetons = oidc._exchange(
            settings,
            metadata=metadata,
            code=code,
            verifier=transaction.code_verifier,
            redirect_uri=transaction.redirect_uri,
        )
        claims = oidc.verify_id_token(
            settings,
            metadata=metadata,
            id_token=jetons["id_token"],
            nonce=transaction.nonce,
        )
        utilisateur = oidc.resolve_user(session, claims)
        appartenances = oidc.active_memberships(session, utilisateur)
        if not appartenances:
            return _retour(login_error="no_membership")
    except oidc.OidcError as exc:
        return _retour(login_error=exc.code)

    liaison = session.scalars(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == claims.issuer,
            ExternalIdentity.subject == claims.subject,
        )
    ).one_or_none()
    if liaison is not None:
        liaison.last_login_at = utcnow()

    transaction.user_id = utilisateur.id
    # Le choix d'organisation reste explicite quand il y en a plusieurs :
    # l'échange le demandera. Ne pas en préaffecter une évite de faire
    # travailler quelqu'un dans la mauvaise sans qu'il l'ait voulu.
    if len(appartenances) == 1:
        transaction.organization_id = appartenances[0].organization_id
    transaction.login_code = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    transaction.login_code_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        seconds=settings.oidc_login_code_ttl_seconds
    )
    session.flush()

    parametres = {"login_code": transaction.login_code}
    if transaction.return_to:
        parametres["return_to"] = transaction.return_to
    return _retour(**parametres)


@router.post("/exchange", response_model=TokenResponse, summary="Échanger le code de connexion")
def exchange(
    payload: OidcExchangeRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(session_scope),
) -> TokenResponse:
    """Rend la session contre un code opaque, une seule fois."""
    _require_oidc(settings)

    transaction = session.scalars(
        select(LoginTransaction).where(LoginTransaction.login_code == payload.login_code)
    ).one_or_none()
    maintenant = datetime.now(UTC).replace(tzinfo=None)
    if (
        transaction is None
        or transaction.user_id is None
        or transaction.login_code_expires_at is None
        or transaction.login_code_expires_at < maintenant
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_login_code", "message": "Code de connexion invalide."},
        )

    appartenances = session.scalars(
        select(Membership).where(
            Membership.user_id == transaction.user_id, Membership.is_active.is_(True)
        )
    ).all()
    if not appartenances:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "no_membership", "message": "Aucune organisation active."},
        )

    choisie = payload.organization_id or transaction.organization_id
    if choisie is None:
        if len(appartenances) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "organization_required",
                    "message": "Cet utilisateur appartient à plusieurs organisations.",
                    "organization_ids": [m.organization_id for m in appartenances],
                },
            )
        choisie = appartenances[0].organization_id

    membre = next((m for m in appartenances if m.organization_id == choisie), None)
    if membre is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "no_membership", "message": "Aucun accès à cette organisation."},
        )

    # Le code ne sert qu'une fois. Effacé ici, et non « marqué utilisé » : rien
    # ne doit pouvoir le retrouver, même par erreur de requête.
    transaction.login_code = None
    transaction.login_code_expires_at = None
    session.flush()

    jeton, duree = issue_token(
        user_id=transaction.user_id, organization_id=choisie, settings=settings
    )
    return TokenResponse(
        access_token=jeton,
        expires_in=duree,
        organization_id=choisie,
        user_id=transaction.user_id,
        role=membre.role,
    )
