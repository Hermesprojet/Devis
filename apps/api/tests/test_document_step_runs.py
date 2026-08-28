"""Application-level idempotence and state transitions for document steps."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from metreo_api.db import get_session_factory
from metreo_api.models import (
    Document,
    DocumentRevision,
    ExtractionProposal,
    Membership,
    Organization,
    Project,
    SourceCitation,
    User,
)
from metreo_api.services import documents


def _id() -> str:
    return str(uuid4())


def _revision_graph(session: Session, *, label: str) -> tuple[Organization, DocumentRevision]:
    organization = Organization(id=_id(), name=f"Organisation {label}")
    user = User(
        id=_id(),
        email=f"{label}-{_id()}@example.invalid",
        full_name=f"Humain {label}",
    )
    project = Project(
        id=_id(),
        organization_id=organization.id,
        reference=f"DOC-{label}-{_id()[:8]}",
        name=f"Projet {label}",
    )
    document = Document(
        id=_id(),
        organization_id=organization.id,
        project_id=project.id,
        title=f"Document {label}",
        created_by=user.id,
    )
    revision = DocumentRevision(
        id=_id(),
        organization_id=organization.id,
        document_id=document.id,
        revision_number=1,
        sha256="a" * 64,
        byte_size=128,
        media_type="application/pdf",
        storage_key=f"tenant/{organization.id}/revision.pdf",
        original_filename="fixture.pdf",
        status="draft",
        created_by=user.id,
    )
    session.add_all(
        (
            organization,
            user,
            Membership(
                id=_id(),
                organization_id=organization.id,
                user_id=user.id,
                role="estimator",
            ),
            project,
        )
    )
    session.flush()
    session.add(document)
    session.flush()
    session.add(revision)
    session.commit()
    return organization, revision


@pytest.fixture()
def session(migrated: None) -> Session:
    database = get_session_factory()()
    try:
        yield database
    finally:
        database.rollback()
        database.close()


def _claim(session: Session, organization_id: str, revision_id: str, **changes: str):
    values = {
        "organization_id": organization_id,
        "revision_id": revision_id,
        "step": "ocr",
        "pipeline_version": "pipeline-1",
        "prompt_version": "none",
        "model_version": "ocr-1",
    }
    values.update(changes)
    return documents.claim_step_run(session, **values)


def test_claim_is_idempotent_versioned_and_tenant_scoped(session: Session) -> None:
    organization, revision = _revision_graph(session, label="A")
    foreign, _foreign_revision = _revision_graph(session, label="B")

    first, first_created = _claim(session, organization.id, revision.id)
    duplicate, duplicate_created = _claim(session, organization.id, revision.id)
    changed_prompt, changed_created = _claim(
        session,
        organization.id,
        revision.id,
        prompt_version="prompt-2",
    )
    session.commit()

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert changed_created is True
    assert changed_prompt.id != first.id
    assert (
        session.scalar(
            select(func.count())
            .select_from(type(first))
            .where(type(first).revision_id == revision.id)
        )
        == 2
    )

    with pytest.raises(HTTPException) as refused:
        _claim(session, foreign.id, revision.id)
    assert refused.value.status_code == 404


def test_state_transitions_retry_and_error_codes_are_explicit(session: Session) -> None:
    organization, revision = _revision_graph(session, label="STATE")
    succeeded, _created = _claim(session, organization.id, revision.id)

    result = documents.succeed_step_run(
        session,
        organization_id=organization.id,
        step_run_id=succeeded.id,
        duration_ms=12,
    )
    repeated = documents.succeed_step_run(
        session,
        organization_id=organization.id,
        step_run_id=succeeded.id,
        duration_ms=999,
    )
    assert result.status == repeated.status == "succeeded"
    assert repeated.duration_ms == 12

    with pytest.raises(documents.DocumentStepRunRefused) as refused:
        documents.fail_step_run(
            session,
            organization_id=organization.id,
            step_run_id=succeeded.id,
            error_code="provider_unavailable",
            duration_ms=13,
        )
    assert refused.value.code == "step_already_succeeded"

    failed, _created = _claim(
        session,
        organization.id,
        revision.id,
        model_version="ocr-2",
    )
    documents.fail_step_run(
        session,
        organization_id=organization.id,
        step_run_id=failed.id,
        error_code="provider_unavailable",
        duration_ms=8,
    )
    repeated_failure = documents.fail_step_run(
        session,
        organization_id=organization.id,
        step_run_id=failed.id,
        error_code="provider_unavailable",
        duration_ms=999,
    )
    assert repeated_failure.duration_ms == 8
    assert repeated_failure.error_summary is None

    retried, restarted = documents.retry_failed_step_run(
        session,
        organization_id=organization.id,
        step_run_id=failed.id,
    )
    assert restarted is True
    assert retried.status == "running"
    assert retried.attempt == 2
    assert retried.error_code is None
    assert retried.error_summary is None
    assert retried.finished_at is None
    assert retried.duration_ms is None

    with pytest.raises(documents.DocumentStepRunRefused) as unsafe:
        documents.fail_step_run(
            session,
            organization_id=organization.id,
            step_run_id=failed.id,
            error_code="contenu client: mot de passe",
            duration_ms=1,
        )
    assert unsafe.value.code == "invalid_step_error_code"
    assert "mot de passe" not in str(unsafe.value)


def test_low_confidence_proposal_never_becomes_business_data(session: Session) -> None:
    organization, revision = _revision_graph(session, label="LOW")
    citation = SourceCitation(
        id=_id(),
        organization_id=organization.id,
        revision_id=revision.id,
        page=1,
        char_start=0,
        char_end=4,
        x0=Decimal("0.1"),
        y0=Decimal("0.1"),
        x1=Decimal("0.2"),
        y1=Decimal("0.2"),
        extractor="fixture",
        confidence=Decimal("0.000001"),
    )
    proposal = ExtractionProposal(
        id=_id(),
        organization_id=organization.id,
        revision_id=revision.id,
        citation_id=citation.id,
        schema_name="quantity",
        schema_version="1",
        value={"quantity": "999"},
        confidence=Decimal("0.000001"),
        pipeline_version="pipeline-1",
        prompt_version="prompt-1",
        model_version="model-1",
        status="proposed",
    )
    session.add(citation)
    session.flush()
    session.add(proposal)
    session.commit()

    business_tables = ("boq_items", "price_items", "estimate_versions")
    before = {
        table: session.scalar(text(f"SELECT count(*) FROM {table}")) for table in business_tables
    }
    run, _created = _claim(
        session,
        organization.id,
        revision.id,
        step="structured_extraction",
        prompt_version="prompt-1",
        model_version="model-1",
    )
    documents.succeed_step_run(
        session,
        organization_id=organization.id,
        step_run_id=run.id,
        duration_ms=1,
    )
    session.commit()
    session.refresh(proposal)

    assert proposal.status == "proposed"
    assert proposal.value == {"quantity": "999"}
    after = {
        table: session.scalar(text(f"SELECT count(*) FROM {table}")) for table in business_tables
    }
    assert after == before
