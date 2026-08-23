"""Permissions, tenant isolation and data-minimisation of document metadata."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from metreo_api.db import get_session_factory
from metreo_api.models import (
    AuditEvent,
    BoqItem,
    DocumentRevision,
    EstimateVersion,
    ExtractionProposal,
    PriceItem,
    SourceCitation,
    ValidationDecision,
)
from metreo_api.security.roles import ROLE_PERMISSIONS, Permission, Role

from .conftest import login


def _project(client: TestClient, headers: dict[str, str], reference: str) -> str:
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"reference": reference, "name": "Projet documentaire"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _document(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    *,
    title: str = "Cahier spécial des charges",
) -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=headers,
        json={"title": title},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _proposal(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
) -> tuple[str, str]:
    identity = client.get("/api/v1/auth/me", headers=headers)
    assert identity.status_code == 200, identity.text
    organization_id = str(identity.json()["organization_id"])
    user_id = str(identity.json()["user_id"])
    revision_id = str(uuid4())
    citation_id = str(uuid4())
    proposal_id = str(uuid4())

    session = get_session_factory()()
    try:
        revision = DocumentRevision(
            id=revision_id,
            organization_id=organization_id,
            document_id=document_id,
            revision_number=1,
            sha256="d" * 64,
            byte_size=2048,
            media_type="application/pdf",
            storage_key=f"tenant/{organization_id}/secret-key.pdf",
            original_filename="nom-client-confidentiel.pdf",
            created_by=user_id,
        )
        session.add(revision)
        session.flush()
        citation = SourceCitation(
            id=citation_id,
            organization_id=organization_id,
            revision_id=revision_id,
            page=2,
            char_start=10,
            char_end=40,
            x0=Decimal("0.1"),
            y0=Decimal("0.2"),
            x1=Decimal("0.8"),
            y1=Decimal("0.9"),
            extractor="test-fixture",
            confidence=Decimal("0.88"),
        )
        session.add(citation)
        session.flush()
        session.add(
            ExtractionProposal(
                id=proposal_id,
                organization_id=organization_id,
                revision_id=revision_id,
                citation_id=citation_id,
                schema_name="quantity",
                schema_version="1",
                value={"description": "Contenu extrait confidentiel", "quantity": "12.5"},
                confidence=Decimal("0.75"),
                pipeline_version="pipeline-1",
                prompt_version="prompt-1",
                model_version="model-1",
            )
        )
        session.commit()
    finally:
        session.close()
    return revision_id, proposal_id


def test_document_permissions_follow_least_privilege() -> None:
    for role in Role:
        assert Permission.DOCUMENT_READ in ROLE_PERMISSIONS[role]

    writers = {
        Role.ORG_ADMIN,
        Role.ESTIMATING_MANAGER,
        Role.ESTIMATOR,
        Role.PROJECT_MANAGER,
    }
    validators = writers
    for role in Role:
        assert (Permission.DOCUMENT_WRITE in ROLE_PERMISSIONS[role]) is (role in writers)
        assert (Permission.DOCUMENT_VALIDATE in ROLE_PERMISSIONS[role]) is (role in validators)

    assert Permission.DOCUMENT_VALIDATE in ROLE_PERMISSIONS[Role.ESTIMATOR]
    assert Permission.BOQ_APPROVE not in ROLE_PERMISSIONS[Role.ESTIMATOR]
    assert Permission.DOCUMENT_VALIDATE in ROLE_PERMISSIONS[Role.PROJECT_MANAGER]
    assert Permission.BOQ_APPROVE not in ROLE_PERMISSIONS[Role.PROJECT_MANAGER]


def test_owner_can_create_list_and_read_safe_document_metadata(
    seeded_client: TestClient,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    project_id = _project(seeded_client, admin, "DOC-OWNER")
    title = "Étude de sol — confidentielle"
    document_id = _document(seeded_client, admin, project_id, title=title)
    _revision_id, _proposal_id = _proposal(seeded_client, admin, document_id)

    detail = seeded_client.get(f"/api/v1/documents/{document_id}", headers=admin)
    listing = seeded_client.get(
        f"/api/v1/projects/{project_id}/documents",
        headers=admin,
    )
    revisions = seeded_client.get(
        f"/api/v1/documents/{document_id}/revisions",
        headers=admin,
    )
    assert detail.status_code == 200, detail.text
    assert listing.status_code == 200, listing.text
    assert revisions.status_code == 200, revisions.text
    assert detail.json()["title"] == title
    assert [row["id"] for row in listing.json()] == [document_id]

    forbidden_document = {"organization_id", "created_by", "deleted_at"}
    assert forbidden_document.isdisjoint(detail.json())
    assert forbidden_document.isdisjoint(listing.json()[0])
    revision = revisions.json()[0]
    assert {
        "storage_key",
        "original_filename",
        "organization_id",
        "created_by",
    }.isdisjoint(revision)
    assert revision["sha256"] == "d" * 64


def test_viewer_reads_but_cannot_create_document(seeded_client: TestClient) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    viewer = login(seeded_client, "lecteur@dubois.demo")
    project_id = _project(seeded_client, admin, "DOC-VIEWER")
    document_id = _document(seeded_client, admin, project_id)

    assert seeded_client.get(f"/api/v1/documents/{document_id}", headers=viewer).status_code == 200
    refused = seeded_client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=viewer,
        json={"title": "Interdit"},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["required_permission"] == Permission.DOCUMENT_WRITE


def test_cross_tenant_document_and_proposal_look_unknown(
    seeded_client: TestClient,
) -> None:
    admin_a = login(seeded_client, "admin@dubois.demo")
    admin_b = login(seeded_client, "admin@janssens.demo")
    project_id = _project(seeded_client, admin_a, "DOC-TENANT")
    document_id = _document(seeded_client, admin_a, project_id)
    _revision_id, proposal_id = _proposal(seeded_client, admin_a, document_id)

    foreign_document = seeded_client.get(
        f"/api/v1/documents/{document_id}",
        headers=admin_b,
    )
    unknown_document = seeded_client.get(
        f"/api/v1/documents/{uuid4()}",
        headers=admin_b,
    )
    assert foreign_document.status_code == unknown_document.status_code == 404
    assert foreign_document.json()["detail"]["code"] == "not_found"
    assert "confidentiel" not in foreign_document.text.lower()

    foreign_proposal = seeded_client.post(
        f"/api/v1/extraction-proposals/{proposal_id}/decisions",
        headers=admin_b,
        json={"decision": "accepted", "reason": "Tentative étrangère"},
    )
    assert foreign_proposal.status_code == 404, foreign_proposal.text
    assert "contenu extrait" not in foreign_proposal.text.lower()


def test_estimator_can_validate_without_boq_approval_and_without_data_leak(
    seeded_client: TestClient,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    estimator = login(seeded_client, "metreur@dubois.demo")
    project_id = _project(seeded_client, admin, "DOC-VALIDATE")
    title = "Nom client à ne pas journaliser"
    document_id = _document(seeded_client, admin, project_id, title=title)
    _revision_id, proposal_id = _proposal(seeded_client, admin, document_id)

    session = get_session_factory()()
    try:
        proposal = session.get(ExtractionProposal, proposal_id)
        assert proposal is not None
        original_value = dict(proposal.value)
        original_status = proposal.status
        business_before = {
            "boq_items": session.scalar(select(func.count()).select_from(BoqItem)),
            "price_items": session.scalar(select(func.count()).select_from(PriceItem)),
            "estimate_versions": session.scalar(select(func.count()).select_from(EstimateVersion)),
        }
    finally:
        session.close()

    reason = "Vérifié contre le rapport confidentiel."
    before_value = {"quantity": "12.5"}
    after_value = {"quantity": "12.0"}
    response = seeded_client.post(
        f"/api/v1/extraction-proposals/{proposal_id}/decisions",
        headers=estimator,
        json={
            "decision": "corrected",
            "reason": reason,
            "before_value": before_value,
            "after_value": after_value,
        },
    )
    assert response.status_code == 201, response.text
    assert {
        "reason",
        "before_value",
        "after_value",
        "organization_id",
    }.isdisjoint(response.json())

    session = get_session_factory()()
    try:
        proposal = session.get(ExtractionProposal, proposal_id)
        decision = session.get(ValidationDecision, response.json()["id"])
        assert proposal is not None
        assert decision is not None
        assert proposal.value == original_value
        assert proposal.status == original_status == "proposed"
        assert decision.reason == reason
        assert decision.before_value == before_value
        assert decision.after_value == after_value

        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == proposal.organization_id,
                AuditEvent.object_type == "validation_decision",
                AuditEvent.object_id == decision.id,
            )
        ).one()
        audit_material = json.dumps(
            {"summary": event.summary, "payload": event.payload},
            ensure_ascii=False,
        )
        for secret in (
            reason,
            title,
            "Contenu extrait confidentiel",
            "nom-client-confidentiel.pdf",
            "secret-key.pdf",
        ):
            assert secret not in audit_material

        business_after = {
            "boq_items": session.scalar(select(func.count()).select_from(BoqItem)),
            "price_items": session.scalar(select(func.count()).select_from(PriceItem)),
            "estimate_versions": session.scalar(select(func.count()).select_from(EstimateVersion)),
        }
        assert business_after == business_before
    finally:
        session.close()


def test_document_payloads_reject_ambiguous_or_extra_data(
    seeded_client: TestClient,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    project_id = _project(seeded_client, admin, "DOC-VALIDATION")
    document_id = _document(seeded_client, admin, project_id)
    _revision_id, proposal_id = _proposal(seeded_client, admin, document_id)

    blank = seeded_client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=admin,
        json={"title": "   "},
    )
    extra = seeded_client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=admin,
        json={"title": "Valide", "organization_id": str(uuid4())},
    )
    missing_correction = seeded_client.post(
        f"/api/v1/extraction-proposals/{proposal_id}/decisions",
        headers=admin,
        json={
            "decision": "corrected",
            "reason": "Correction",
            "before_value": {"quantity": "1"},
        },
    )
    forged_acceptance = seeded_client.post(
        f"/api/v1/extraction-proposals/{proposal_id}/decisions",
        headers=admin,
        json={
            "decision": "accepted",
            "reason": "Acceptation",
            "before_value": {"quantity": "1"},
        },
    )
    assert blank.status_code == 422
    assert extra.status_code == 422
    assert missing_correction.status_code == 422
    assert forged_acceptance.status_code == 422
