"""Tenant-aware metadata operations for documentary records.

No binary content, storage provider, OCR or extracted value is handled here.
Every read starts from an explicit organisation and every audit event contains
identifiers and state only — never a document title, filename, free-form
validation reason or extracted value.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Document,
    DocumentRevision,
    DocumentStepRun,
    ExtractionProposal,
    Project,
    ValidationDecision,
    utcnow,
)
from ..schemas import ValidationDecisionCreate
from ..security.auth import TenantContext
from . import audit
from .locking import lock_owned
from .tenant import get_owned, owned_query

DOCUMENT_PIPELINE_STEPS = frozenset(
    {
        "receive_security",
        "detection",
        "native_text",
        "ocr",
        "tables",
        "segmentation",
        "classification",
        "structured_extraction",
        "indexing",
        "consistency",
        "human_review",
    }
)

# Only stable machine codes cross this boundary.  A provider exception or a
# document excerpt is mapped to one of these before persistence; no free-form
# failure message is accepted by the service.
SAFE_STEP_ERROR_CODES = frozenset(
    {
        "invalid_output",
        "malware_detected",
        "processing_failed",
        "provider_unavailable",
        "timeout",
        "unsupported_media_type",
    }
)


class DocumentStepRunRefused(Exception):
    """Typed refusal whose message never includes document or provider data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _nonblank_version(value: str, *, code: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        raise DocumentStepRunRefused(code)
    return normalized


def _duration(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DocumentStepRunRefused("invalid_step_duration")
    return value


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


def next_revision_number(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> int:
    """Allocate the next revision number while holding the parent document.

    PostgreSQL serialises concurrent allocators on the document row. SQLite
    serialises writers at file level; the unique constraint remains the final
    defence on both engines.
    """
    lock_owned(
        session,
        Document,
        organization_id,
        document_id,
        label="Document",
    )
    current = session.scalar(
        select(func.max(DocumentRevision.revision_number)).where(
            DocumentRevision.organization_id == organization_id,
            DocumentRevision.document_id == document_id,
        )
    )
    return int(current or 0) + 1


def claim_step_run(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    step: str,
    pipeline_version: str,
    prompt_version: str,
    model_version: str,
) -> tuple[DocumentStepRun, bool]:
    """Claim exactly one versioned step run, returning ``(row, created)``.

    The immutable revision is the concurrency mutex.  Two workers claiming
    the same key queue on it; the second then observes the row created by the
    first instead of hitting the uniqueness constraint.  The database unique
    key remains the final defence against writers that bypass this service.
    """
    if step not in DOCUMENT_PIPELINE_STEPS:
        raise DocumentStepRunRefused("invalid_document_step")
    pipeline_version = _nonblank_version(
        pipeline_version,
        code="invalid_pipeline_version",
    )
    prompt_version = _nonblank_version(
        prompt_version,
        code="invalid_prompt_version",
    )
    model_version = _nonblank_version(
        model_version,
        code="invalid_model_version",
    )

    lock_owned(
        session,
        DocumentRevision,
        organization_id,
        revision_id,
        label="Révision documentaire",
    )
    existing = session.scalars(
        owned_query(DocumentStepRun, organization_id).where(
            DocumentStepRun.revision_id == revision_id,
            DocumentStepRun.step == step,
            DocumentStepRun.pipeline_version == pipeline_version,
            DocumentStepRun.prompt_version == prompt_version,
            DocumentStepRun.model_version == model_version,
        )
    ).one_or_none()
    if existing is not None:
        return existing, False

    started_at = utcnow()
    run = DocumentStepRun(
        organization_id=organization_id,
        revision_id=revision_id,
        step=step,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        model_version=model_version,
        status="running",
        attempt=1,
        started_at=started_at,
    )
    session.add(run)
    session.flush()
    return run, True


def succeed_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    duration_ms: int,
    finished_at: datetime | None = None,
) -> DocumentStepRun:
    """Finish a running step; repeating the same outcome is idempotent."""
    duration_ms = _duration(duration_ms)
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "succeeded":
        return run
    if run.status == "failed":
        raise DocumentStepRunRefused("step_already_failed")
    if run.status != "running":
        raise DocumentStepRunRefused("step_not_running")
    run.status = "succeeded"
    run.finished_at = finished_at or utcnow()
    run.duration_ms = duration_ms
    run.error_code = None
    run.error_summary = None
    session.flush()
    return run


def fail_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    error_code: str,
    duration_ms: int,
    finished_at: datetime | None = None,
) -> DocumentStepRun:
    """Record a failed step using a bounded, non-sensitive machine code."""
    duration_ms = _duration(duration_ms)
    if error_code not in SAFE_STEP_ERROR_CODES:
        raise DocumentStepRunRefused("invalid_step_error_code")
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "failed" and run.error_code == error_code:
        return run
    if run.status == "failed":
        raise DocumentStepRunRefused("step_already_failed")
    if run.status == "succeeded":
        raise DocumentStepRunRefused("step_already_succeeded")
    if run.status != "running":
        raise DocumentStepRunRefused("step_not_running")
    run.status = "failed"
    run.finished_at = finished_at or utcnow()
    run.duration_ms = duration_ms
    run.error_code = error_code
    # Deliberately no free-form provider exception or document text.
    run.error_summary = None
    session.flush()
    return run


def retry_failed_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    started_at: datetime | None = None,
) -> tuple[DocumentStepRun, bool]:
    """Restart one failed row without changing its idempotence key."""
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "running":
        return run, False
    if run.status == "succeeded":
        raise DocumentStepRunRefused("step_already_succeeded")
    if run.status != "failed":
        raise DocumentStepRunRefused("step_not_failed")
    run.status = "running"
    run.attempt += 1
    run.started_at = started_at or utcnow()
    run.finished_at = None
    run.duration_ms = None
    run.error_code = None
    run.error_summary = None
    session.flush()
    return run, True


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
