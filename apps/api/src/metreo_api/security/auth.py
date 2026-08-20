"""Request authentication and the tenant context every handler receives.

There is exactly one way to learn who the caller is and which organisation the
request targets: :func:`current_context`. Handlers never read an organisation
id from the path or the body, which is what keeps cross-tenant reads from being
one forgotten filter away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Membership, Organization, User
from .roles import Permission, Role, has_permission, permissions_for


@dataclass(frozen=True)
class TenantContext:
    """Resolved caller: a user, an organisation and the role binding them."""

    user: User
    organization: Organization
    membership: Membership

    @property
    def organization_id(self) -> str:
        return self.organization.id

    @property
    def role(self) -> str:
        return self.membership.role

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "permission_denied",
                    "message": "Action non autorisée pour ce rôle.",
                    "required_permission": permission.value,
                    "role": self.role,
                },
            )


def issue_token(
    *, user_id: str, organization_id: str, settings: Settings | None = None
) -> tuple[str, int]:
    settings = settings or get_settings()
    expires_in = settings.jwt_ttl_seconds
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "org": organization_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "iss": "metreo-api",
    }
    token = jwt.encode(payload, settings.effective_jwt_secret(), algorithm=settings.jwt_algorithm)
    return token, expires_in


def _decode(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.effective_jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer="metreo-api",
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_expired", "message": "Session expirée."},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Jeton invalide."},
        ) from exc


def current_context(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    """Resolve the caller or refuse the request.

    The membership is re-read on every request: revoking a membership takes
    effect immediately instead of at token expiry.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_token", "message": "Authentification requise."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = _decode(authorization.split(" ", 1)[1].strip(), settings)
    user_id = claims.get("sub")
    organization_id = claims.get("org")
    if not user_id or not organization_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Jeton incomplet."},
        )

    membership = session.scalars(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
            Membership.is_active.is_(True),
        )
    ).one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "no_membership",
                "message": "Aucun accès actif à cette organisation.",
            },
        )

    user = session.get(User, user_id)
    organization = session.get(Organization, organization_id)
    if user is None or not user.is_active or organization is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "inactive_account", "message": "Compte inactif."},
        )
    return TenantContext(user=user, organization=organization, membership=membership)


def require(permission: Permission):
    """Dependency factory enforcing one permission."""

    def dependency(
        context: TenantContext = Depends(current_context),
    ) -> TenantContext:
        context.require(permission)
        return context

    return dependency


def describe_role(role: str) -> dict[str, object]:
    try:
        role_enum = Role(role)
        label = role_enum.label_fr
    except ValueError:
        label = role
    return {
        "role": role,
        "label": label,
        "permissions": sorted(p.value for p in permissions_for(role)),
    }
