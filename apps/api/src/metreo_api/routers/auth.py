"""Authentication endpoints.

``/auth/dev-login`` exists so the product runs locally with no identity
provider and no payable service. It is refused outside a development
environment and when ``METREO_AUTH_MODE`` is not ``dev``; the OIDC flow that
replaces it is an explicit Phase 5 item, not a hidden switch.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Membership, Organization, User
from ..schemas import DevLoginRequest, MembershipOut, MeResponse, TokenResponse
from ..security.auth import TenantContext, current_context, issue_token
from ..security.roles import Role, permissions_for
from ..transactions import RouteTransactionnelle

router = APIRouter(prefix="/auth", tags=["auth"], route_class=RouteTransactionnelle)


def _role_label(role: str) -> str:
    try:
        return Role(role).label_fr
    except ValueError:
        return role


@router.post(
    "/dev-login",
    response_model=TokenResponse,
    summary="Jeton de développement (mode dev uniquement)",
)
def dev_login(
    payload: DevLoginRequest,
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if settings.auth_mode != "dev" or settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "dev_login_disabled",
                "message": "Le mode de connexion de développement est désactivé.",
            },
        )

    user = session.scalars(
        select(User).where(User.email == payload.email.lower(), User.is_active.is_(True))
    ).one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unknown_user", "message": "Utilisateur inconnu."},
        )

    memberships = session.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    ).all()
    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "no_membership", "message": "Aucune organisation active."},
        )

    if payload.organization_id:
        selected = next(
            (m for m in memberships if m.organization_id == payload.organization_id), None
        )
        if selected is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "no_membership",
                    "message": "Aucun accès à cette organisation.",
                },
            )
    elif len(memberships) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "organization_required",
                "message": "Cet utilisateur appartient à plusieurs organisations.",
                "organization_ids": [m.organization_id for m in memberships],
            },
        )
    else:
        selected = memberships[0]

    token, expires_in = issue_token(
        user_id=user.id, organization_id=selected.organization_id, settings=settings
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        organization_id=selected.organization_id,
        user_id=user.id,
        role=selected.role,
    )


@router.get("/me", response_model=MeResponse, summary="Profil et permissions du porteur du jeton")
def me(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
) -> MeResponse:
    rows = session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == context.user.id, Membership.is_active.is_(True))
    ).all()
    return MeResponse(
        user_id=context.user.id,
        email=context.user.email,
        full_name=context.user.full_name,
        locale=context.user.locale,
        organization_id=context.organization.id,
        organization_name=context.organization.name,
        role=context.role,
        role_label=_role_label(context.role),
        permissions=sorted(p.value for p in permissions_for(context.role)),
        memberships=[
            MembershipOut(
                organization_id=membership.organization_id,
                organization_name=organization.name,
                role=membership.role,
                role_label=_role_label(membership.role),
            )
            for membership, organization in rows
        ],
    )
