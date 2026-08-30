"""Connexion par un fournisseur d'identité externe (OpenID Connect).

Ce module fournit le seul parcours de connexion utilisable hors développement.
`dev-login` émet des jetons sans mot de passe et reste refusé en préproduction
et en production ; sans ce qui suit, l'application y était déployable mais
personne ne pouvait s'y connecter.

Ce qui est délibéré ici
-----------------------

**Rien n'est deviné.** Les points d'entrée (autorisation, jeton, JWKS) sont lus
dans le document de découverte du fournisseur. Aucun chemin n'est codé en dur,
donc n'importe quel fournisseur conforme convient.

**Aucune cryptographie n'est réécrite.** La vérification de signature est celle
de PyJWT, avec les clés publiques que `PyJWKClient` récupère et met en cache.
On ne manipule ni RSA ni condensats à la main. PKCE se réduit à un SHA-256
d'une valeur aléatoire — `hashlib`, pas une implémentation maison.

**La transaction vit en base, pas en mémoire.** Un `state` gardé dans un
dictionnaire de processus casse dès la deuxième instance d'API : l'utilisateur
part depuis l'une et revient sur l'autre, qui ne connaît pas sa demande. Une
table, en revanche, est partagée — et permet de refuser le rejeu par une
contrainte d'unicité plutôt que par une convention.

**Le jeton final ne passe jamais par l'URL.** Le fournisseur renvoie le
navigateur sur l'application avec un code opaque, court et à usage unique, que
le client échange contre sa session. Un JWT dans une URL se retrouve dans
l'historique, les journaux du proxy et l'en-tête `Referer`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ExternalIdentity, LoginTransaction, Membership, User, utcnow


class OidcError(Exception):
    """Un parcours de connexion qui ne peut pas aboutir.

    Porte un code stable pour le client et un message affichable. Le détail
    technique reste dans les journaux : dire à un visiteur *pourquoi* sa
    connexion échoue en dit trop à qui essaie des adresses au hasard.
    """

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Ce que la découverte nous apprend du fournisseur."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True, slots=True)
class ExternalClaims:
    """L'identité telle que le fournisseur l'affirme, une fois vérifiée."""

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    """Vérificateur PKCE : 43 à 128 caractères d'alphabet URL (RFC 7636)."""
    return _b64url(secrets.token_bytes(48))


def challenge_for(verifier: str) -> str:
    """Défi PKCE en S256. La méthode `plain` n'est pas proposée : elle
    n'apporte rien qu'un attaquant capable de lire l'URL ne contourne."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def discover(settings: Settings, *, client: httpx.Client | None = None) -> ProviderMetadata:
    """Lit le document de découverte et refuse un émetteur qui ne se confirme pas.

    Le champ `issuer` du document doit valoir exactement l'émetteur configuré.
    Sans ce contrôle, un document servi depuis une autre origine pourrait
    rediriger le parcours vers les points d'entrée d'un tiers.
    """
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    fermer = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        reponse = http.get(url)
        reponse.raise_for_status()
        document = reponse.json()
    except (httpx.HTTPError, ValueError) as erreur:
        raise OidcError(
            "provider_unavailable",
            "Le fournisseur d'identité est injoignable.",
            status_code=503,
        ) from erreur
    finally:
        if fermer:
            http.close()

    declare = str(document.get("issuer", ""))
    if declare.rstrip("/") != settings.oidc_issuer.rstrip("/"):
        raise OidcError(
            "issuer_mismatch",
            "Le fournisseur d'identité ne se présente pas sous l'émetteur attendu.",
            status_code=502,
        )
    try:
        return ProviderMetadata(
            issuer=declare,
            authorization_endpoint=str(document["authorization_endpoint"]),
            token_endpoint=str(document["token_endpoint"]),
            jwks_uri=str(document["jwks_uri"]),
        )
    except KeyError as erreur:
        raise OidcError(
            "provider_incomplete",
            "Le fournisseur d'identité ne publie pas les points d'entrée requis.",
            status_code=502,
        ) from erreur


def start(
    session: Session,
    settings: Settings,
    *,
    metadata: ProviderMetadata,
    return_to: str | None = None,
) -> str:
    """Ouvre une transaction de connexion et rend l'URL du fournisseur."""
    transaction = LoginTransaction(
        state=_b64url(secrets.token_bytes(32)),
        nonce=_b64url(secrets.token_bytes(32)),
        code_verifier=new_verifier(),
        redirect_uri=settings.oidc_redirect_uri,
        return_to=return_to,
        expires_at=datetime.now(UTC).replace(tzinfo=None)
        + timedelta(seconds=settings.oidc_transaction_ttl_seconds),
    )
    session.add(transaction)
    session.flush()

    parametres = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": transaction.state,
        "nonce": transaction.nonce,
        "code_challenge": challenge_for(transaction.code_verifier),
        "code_challenge_method": "S256",
    }
    separateur = "&" if "?" in metadata.authorization_endpoint else "?"
    return f"{metadata.authorization_endpoint}{separateur}{urlencode(parametres)}"


def _consume_transaction(session: Session, state: str) -> LoginTransaction:
    """Retire la transaction correspondante, ou refuse.

    La consommation est unique : la ligne est marquée dès qu'elle sert. Un
    `state` rejoué ne retrouve donc rien, et le rejeu est refusé par l'état
    persistant plutôt que par une fenêtre de temps.
    """
    transaction = session.scalars(
        select(LoginTransaction).where(LoginTransaction.state == state)
    ).one_or_none()
    if transaction is None or transaction.consumed_at is not None:
        raise OidcError("invalid_state", "Demande de connexion inconnue ou déjà utilisée.")
    if transaction.expires_at < datetime.now(UTC).replace(tzinfo=None):
        raise OidcError("expired_state", "Demande de connexion expirée.")
    transaction.consumed_at = utcnow()
    session.flush()
    return transaction


def _exchange(
    settings: Settings,
    *,
    metadata: ProviderMetadata,
    code: str,
    verifier: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    fermer = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        reponse = http.post(
            metadata.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as erreur:
        raise OidcError(
            "provider_unavailable",
            "Le fournisseur d'identité est injoignable.",
            status_code=503,
        ) from erreur
    finally:
        if fermer:
            http.close()

    if reponse.status_code >= 400:
        raise OidcError("code_rejected", "Le code d'autorisation a été refusé.")
    try:
        charge = reponse.json()
    except ValueError as erreur:
        raise OidcError("provider_incomplete", "Réponse illisible du fournisseur.") from erreur
    if "id_token" not in charge:
        raise OidcError(
            "provider_incomplete", "Le fournisseur n'a pas renvoyé de jeton d'identité."
        )
    return charge


def verify_id_token(
    settings: Settings,
    *,
    metadata: ProviderMetadata,
    id_token: str,
    nonce: str,
    jwk_client: PyJWKClient | None = None,
) -> ExternalClaims:
    """Vérifie signature, émetteur, audience, expiration et `nonce`.

    Tout est délégué à PyJWT sauf le `nonce`, qui n'est pas une revendication
    standardisée que la bibliothèque contrôle : c'est à l'appelant de le
    comparer à celui qu'il a émis. L'oublier laisserait rejouer un jeton
    d'identité obtenu ailleurs.
    """
    client = jwk_client or PyJWKClient(metadata.jwks_uri, cache_keys=True)
    try:
        cle = client.get_signing_key_from_jwt(id_token)
        revendications = jwt.decode(
            id_token,
            cle.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=settings.oidc_client_id,
            issuer=metadata.issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as erreur:
        raise OidcError("token_expired", "Le jeton d'identité a expiré.") from erreur
    except jwt.InvalidAudienceError as erreur:
        raise OidcError(
            "invalid_audience", "Ce jeton d'identité vise une autre application."
        ) from erreur
    except jwt.InvalidIssuerError as erreur:
        raise OidcError(
            "invalid_issuer", "Ce jeton d'identité vient d'un autre émetteur."
        ) from erreur
    except jwt.InvalidTokenError as erreur:
        raise OidcError("invalid_token", "Jeton d'identité invalide.") from erreur
    except jwt.PyJWKClientError as erreur:
        # Clé de signature introuvable : le fournisseur a fait tourner ses
        # clés, ou le jeton vient d'ailleurs. Dans les deux cas on refuse,
        # sans distinguer — la différence n'aide que celui qui essaie.
        raise OidcError("invalid_token", "Jeton d'identité invalide.") from erreur

    if revendications.get("nonce") != nonce:
        raise OidcError("invalid_nonce", "Jeton d'identité rejoué ou détourné.")

    courriel = revendications.get("email")
    return ExternalClaims(
        issuer=str(revendications["iss"]),
        subject=str(revendications["sub"]),
        email=str(courriel).lower() if courriel else None,
        email_verified=bool(revendications.get("email_verified")),
        full_name=revendications.get("name") or revendications.get("preferred_username"),
    )


def resolve_user(session: Session, claims: ExternalClaims) -> User:
    """Trouve l'utilisateur derrière une identité externe vérifiée.

    Deux chemins, dans cet ordre :

    1. **Le couple `(issuer, subject)`** — immuable, décidé par le fournisseur.
       C'est la seule identification qui vaille une fois la liaison faite.
    2. **L'adresse électronique, une seule fois**, pour créer cette liaison.
       Elle n'est acceptée que si le fournisseur la déclare vérifiée et si un
       administrateur a précréé le compte. Une adresse est réattribuable et
       usurpable ; s'y fier durablement permettrait de reprendre un compte en
       obtenant l'adresse chez le fournisseur.

    Aucune inscription automatique : un visiteur inconnu est refusé, même avec
    un jeton parfaitement valide.
    """
    liaison = session.scalars(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == claims.issuer,
            ExternalIdentity.subject == claims.subject,
        )
    ).one_or_none()

    if liaison is not None:
        utilisateur = session.get(User, liaison.user_id)
        if utilisateur is None or not utilisateur.is_active:
            raise OidcError("account_disabled", "Ce compte est désactivé.", status_code=403)
        return utilisateur

    if not claims.email:
        raise OidcError("unknown_user", "Compte inconnu.", status_code=403)
    if not claims.email_verified:
        raise OidcError(
            "email_not_verified",
            "Votre fournisseur d'identité ne certifie pas cette adresse.",
            status_code=403,
        )

    utilisateur = session.scalars(select(User).where(User.email == claims.email)).one_or_none()
    if utilisateur is None:
        raise OidcError("unknown_user", "Compte inconnu.", status_code=403)
    if not utilisateur.is_active:
        raise OidcError("account_disabled", "Ce compte est désactivé.", status_code=403)

    session.add(
        ExternalIdentity(
            user_id=utilisateur.id,
            issuer=claims.issuer,
            subject=claims.subject,
            email_at_link=claims.email,
        )
    )
    session.flush()
    return utilisateur


def active_memberships(session: Session, user: User) -> list[Membership]:
    return list(
        session.scalars(
            select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
        ).all()
    )
