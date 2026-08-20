"""Company profile, calculation rules and tax rates."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import OrganizationSettings, TaxRateRow
from ..schemas import (
    OrganizationOut,
    OrganizationSettingsOut,
    OrganizationSettingsUpdate,
    TaxRateOut,
)
from ..security.auth import TenantContext, current_context, require
from ..security.roles import Permission
from ..services import audit

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrganizationOut, summary="Organisation courante")
def get_organization(context: TenantContext = Depends(current_context)) -> OrganizationOut:
    return OrganizationOut.model_validate(context.organization)


@router.get(
    "/settings",
    response_model=OrganizationSettingsOut,
    summary="Règles de calcul de l'entreprise",
)
def get_settings_endpoint(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
) -> OrganizationSettingsOut:
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    payload = OrganizationSettingsOut.model_validate(settings)
    if not context.can(Permission.MARGIN_READ):
        # Coefficients that reveal commercial policy are masked rather than
        # refused: the rest of the screen stays usable. They become null, never
        # zero, so a client cannot mistake a mask for a real rate.
        payload = payload.model_copy(
            update={
                "commercial_rates_visible": False,
                "site_overheads_rate": None,
                "site_overheads_base": None,
                "general_overheads_rate": None,
                "general_overheads_base": None,
                "contingency_rate": None,
                "contingency_base": None,
                "margin_rate": None,
                "margin_method": None,
            }
        )
    return payload


@router.patch(
    "/settings",
    response_model=OrganizationSettingsOut,
    summary="Modifier les règles de calcul",
)
def update_settings(
    payload: OrganizationSettingsUpdate,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> OrganizationSettingsOut:
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    changes = payload.model_dump(exclude_unset=True)
    before = {key: str(getattr(settings, key)) for key in changes}
    for key, value in changes.items():
        setattr(settings, key, value)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="organization.settings.updated",
        object_type="organization_settings",
        object_id=context.organization_id,
        summary="Règles de calcul modifiées",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"before": before, "after": {k: str(v) for k, v in changes.items()}},
    )
    return OrganizationSettingsOut.model_validate(settings)


@router.get("/tax-rates", response_model=list[TaxRateOut], summary="Taux de taxe")
def list_tax_rates(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
) -> list[TaxRateRow]:
    return list(
        session.scalars(
            select(TaxRateRow)
            .where(TaxRateRow.organization_id == context.organization_id)
            .order_by(TaxRateRow.code)
        ).all()
    )
