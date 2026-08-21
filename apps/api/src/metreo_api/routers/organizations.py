"""Company profile, calculation rules and tax rates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

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
from ..services.estimating import markup_from_settings

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


@dataclass(slots=True)
class _MergedSettings:
    """Vue en lecture de l'état final, sans toucher à l'objet persistant.

    Muter puis valider laisserait, en cas de refus, une session portant des
    valeurs invalides : un `flush()` déclenché ailleurs les écrirait.
    """

    site_overheads_rate: Any
    site_overheads_base: Any
    general_overheads_rate: Any
    general_overheads_base: Any
    contingency_rate: Any
    contingency_base: Any
    margin_rate: Any
    margin_method: Any


def _merged(settings: OrganizationSettings, changes: dict[str, Any]) -> Any:
    fields = (
        "site_overheads_rate",
        "site_overheads_base",
        "general_overheads_rate",
        "general_overheads_base",
        "contingency_rate",
        "contingency_base",
        "margin_rate",
        "margin_method",
    )
    return _MergedSettings(**{name: changes.get(name, getattr(settings, name)) for name in fields})


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

    # Valider l'état FINAL fusionné, avant d'écrire quoi que ce soit. Une
    # modification partielle suffit à rendre la configuration incalculable :
    # passer la méthode à `on_price` alors que le taux stocké vaut 1,5 — ou
    # l'inverse — produit une division par un nombre négatif ou nul, et TOUTES
    # les estimations de l'entreprise deviennent incalculables d'un coup. La
    # construction de MarkupPolicy est le seul juge : elle porte déjà la règle.
    try:
        markup_from_settings(_merged(settings, changes))
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": exc.code,
                "message": exc.message,
                "context": exc.context,
            },
        ) from exc

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
