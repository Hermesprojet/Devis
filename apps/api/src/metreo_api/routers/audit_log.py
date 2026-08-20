"""Read-only access to the audit journal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import AuditEvent
from ..schemas import AuditEventOut, AuditPage, AuditVerifyOut, Page
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit

router = APIRouter(prefix="/audit", tags=["audit"])


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
    return AuditPage(
        items=[AuditEventOut.model_validate(event) for event in events],
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
