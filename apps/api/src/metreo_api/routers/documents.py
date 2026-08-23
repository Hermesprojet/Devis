"""Document metadata and append-only human validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Document, DocumentRevision, ValidationDecision
from ..schemas import (
    DocumentCreate,
    DocumentOut,
    DocumentRevisionOut,
    ValidationDecisionCreate,
    ValidationDecisionOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import documents

router = APIRouter(tags=["documents"])


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentOut],
    summary="Lister les documents d'un projet",
)
def list_project_documents(
    project_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> list[Document]:
    return documents.list_documents(
        session,
        organization_id=context.organization_id,
        project_id=project_id,
    )


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un document logique",
)
def create_project_document(
    project_id: str,
    payload: DocumentCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
) -> Document:
    return documents.create_document(
        session,
        context=context,
        project_id=project_id,
        title=payload.title,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentOut,
    summary="Lire les métadonnées d'un document",
)
def get_document(
    document_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> Document:
    return documents.get_document(
        session,
        organization_id=context.organization_id,
        document_id=document_id,
    )


@router.get(
    "/documents/{document_id}/revisions",
    response_model=list[DocumentRevisionOut],
    summary="Lister les révisions d'un document",
)
def list_document_revisions(
    document_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> list[DocumentRevision]:
    return documents.list_revisions(
        session,
        organization_id=context.organization_id,
        document_id=document_id,
    )


@router.post(
    "/extraction-proposals/{proposal_id}/decisions",
    response_model=ValidationDecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer une décision humaine",
)
def create_validation_decision(
    proposal_id: str,
    payload: ValidationDecisionCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_VALIDATE)),
    session: Session = Depends(session_scope),
) -> ValidationDecision:
    return documents.record_validation_decision(
        session,
        context=context,
        proposal_id=proposal_id,
        payload=payload,
    )
