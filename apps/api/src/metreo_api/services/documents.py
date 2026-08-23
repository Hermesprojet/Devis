"""Tenant-aware metadata operations for documentary records.

No binary content, storage provider, OCR or extracted value is handled here.
Every read starts from an explicit organisation and every audit event contains
identifiers and state only — never a document title, filename, free-form
validation reason or extracted value.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Document,
    DocumentRevision,
    ExtractionProposal,
    Project,
    ValidationDecision,
)
from ..schemas import ValidationDecisionCreate
from ..security.auth import TenantContext
from . import audit
from .tenant import get_owned, owned_query


def list_documents(
    session: Session,
    *,
    organization_id: str,
    project_id: str,
) -> list[Document]:
    """List active documents after proving ownership of the project."""
    get_owned(session, Project, organization_id, project_id, label="Projet")
    return list(
        session.scalars(
            owned_query(Document, organization_id)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
        ).all()
    )


def create_document(
    session: Session,
    *,
    context: TenantContext,
    project_id: str,
    title: str,
) -> Document:
    """Create a logical document; no file or storage key is accepted."""
    get_owned(session, Project, context.organization_id, project_id, label="Projet")
    document = Document(
        organization_id=context.organization_id,
        project_id=project_id,
        title=title,
        created_by=context.user.id,
    )
    session.add(document)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.created",
        object_type="document",
        object_id=document.id,
        summary="Document créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"project_id": project_id},
    )
    return document


def get_document(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> Document:
    return get_owned(
        session,
        Document,
        organization_id,
        document_id,
        label="Document",
    )


def list_revisions(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> list[DocumentRevision]:
    """List safe revision metadata after a tenant-scoped parent lookup."""
    get_document(session, organization_id=organization_id, document_id=document_id)
    return list(
        session.scalars(
            select(DocumentRevision)
            .where(
                DocumentRevision.organization_id == organization_id,
                DocumentRevision.document_id == document_id,
            )
            .order_by(DocumentRevision.revision_number.desc())
        ).all()
    )


def record_validation_decision(
    session: Session,
    *,
    context: TenantContext,
    proposal_id: str,
    payload: ValidationDecisionCreate,
) -> ValidationDecision:
    """Append one human decision without rewriting the machine proposal."""
    proposal = get_owned(
        session,
        ExtractionProposal,
        context.organization_id,
        proposal_id,
        label="Proposition",
    )
    decision = ValidationDecision(
        organization_id=context.organization_id,
        proposal_id=proposal.id,
        actor_user_id=context.user.id,
        decision=payload.decision,
        reason=payload.reason,
        before_value=payload.before_value,
        after_value=payload.after_value,
    )
    session.add(decision)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.validation_decided",
        object_type="validation_decision",
        object_id=decision.id,
        summary="Décision documentaire enregistrée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "proposal_id": proposal.id,
            "decision": decision.decision,
        },
    )
    return decision
