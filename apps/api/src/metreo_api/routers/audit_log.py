"""Read-only access to the audit journal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import AuditEvent
from ..schemas import (
    CHAMPS_COMMERCIAUX_SENSIBLES,
    AuditEventOut,
    AuditPage,
    AuditVerifyOut,
    Page,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit

router = APIRouter(prefix="/audit", tags=["audit"])


def _sans_secret_commercial(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Retire du payload RENDU les valeurs qui révèlent la politique commerciale.

    `/organization/settings` masque déjà ces huit champs pour qui n'a pas
    `margin:read`. Le journal, lui, les rendait en clair au même utilisateur :
    un `VIEWER` lisait `margin_rate: null` sur les réglages et
    `{"before": {"margin_rate": "0.08"}, "after": {"margin_rate": "0.17"}}`
    dans `/audit/events`. Le masque d'un écran ne vaut rien si un autre écran
    donne la valeur.

    Le nom du champ est conservé, seule la valeur part : qu'une politique
    commerciale ait été modifiée, par qui et quand, est une information d'audit
    légitime ; son montant est le secret. Les champs non commerciaux du même
    événement — `rounding_scale`, `missing_price_policy` — restent lisibles,
    sans quoi le journal deviendrait inutilisable pour un auditeur.

    La valeur masquée est `null`. Le payload stocké ne contient que des chaînes
    (`str(...)` à l'écriture), donc un `null` ne peut pas être confondu avec une
    valeur réelle.

    **Le payload stocké n'est pas touché.** Seule la copie rendue l'est, et le
    hash scellé continue de porter sur l'original : `/audit/verify` recalcule la
    chaîne depuis la base, pas depuis cette réponse.
    """
    masque = False
    rendu: dict[str, Any] = {}
    for cle, valeur in payload.items():
        if cle in {"before", "after"} and isinstance(valeur, dict):
            copie = dict(valeur)
            for champ in copie:
                if champ in CHAMPS_COMMERCIAUX_SENSIBLES and copie[champ] is not None:
                    copie[champ] = None
                    masque = True
            rendu[cle] = copie
        else:
            rendu[cle] = valeur
    return rendu, masque


@router.get("/events", response_model=AuditPage, summary="Journal d'audit")
def list_events(
    action: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require(Permission.AUDIT_READ)),
    session: Session = Depends(session_scope),
) -> AuditPage:
    query = select(AuditEvent).where(AuditEvent.organization_id == context.organization_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if object_type:
        query = query.where(AuditEvent.object_type == object_type)
    if object_id:
        query = query.where(AuditEvent.object_id == object_id)

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    events = session.scalars(
        query.order_by(AuditEvent.sequence.desc()).limit(limit).offset(offset)
    ).all()
    voit_les_marges = context.can(Permission.MARGIN_READ)
    rendus: list[AuditEventOut] = []
    for event in events:
        sortie = AuditEventOut.model_validate(event)
        if not voit_les_marges:
            payload, masque = _sans_secret_commercial(sortie.payload)
            sortie = sortie.model_copy(update={"payload": payload, "payload_redacted": masque})
        rendus.append(sortie)
    return AuditPage(
        items=rendus,
        page=Page(total=int(total or 0), limit=limit, offset=offset),
    )


@router.get(
    "/verify",
    response_model=AuditVerifyOut,
    summary="Vérifier la chaîne d'intégrité du journal",
)
def verify(
    context: TenantContext = Depends(require(Permission.AUDIT_READ)),
    session: Session = Depends(session_scope),
) -> AuditVerifyOut:
    return AuditVerifyOut(**audit.verify_chain(session, context.organization_id))
