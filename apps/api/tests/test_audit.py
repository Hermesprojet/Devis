"""Acceptance scenario 9: important changes are journalled and verifiable."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def actions(client: TestClient, headers: dict[str, str]) -> list[str]:
    return [
        e["action"]
        for e in client.get("/api/v1/audit/events?limit=200", headers=headers).json()["items"]
    ]


def test_creating_a_project_is_journalled_with_actor_date_action_and_object(seeded_client, headers):
    created = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-099", "name": "Test audit"}
    ).json()
    events = seeded_client.get(
        f"/api/v1/audit/events?object_id={created['id']}", headers=headers
    ).json()["items"]
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "project.created"
    assert event["object_type"] == "project"
    assert event["actor_email"] == "admin@dubois.demo"
    assert event["occurred_at"]
    assert "2026-099" in event["summary"]


def test_the_main_phase_one_actions_are_all_journalled(seeded_client, headers):
    """Walk the whole Phase 1 flow through the API and check what it left behind.

    The seed loads data directly and deliberately journals only its own
    ``organization.seeded`` event, so this test performs every action itself.
    """
    project = seeded_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"reference": "2026-200", "name": "Piste cyclable"},
    ).json()
    boq = seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=headers, json={"name": "Métré"}
    ).json()
    item = seeded_client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=headers,
        json={
            "position": "1.1",
            "designation": "Empierrement",
            "unit_code": "m2",
            "quantity": "100",
        },
    ).json()
    seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=headers)

    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    estimate = seeded_client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": project["id"],
            "boq_id": boq["id"],
            "price_book_version_id": pb_version,
            "name": "Étude",
        },
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]

    seeded_client.patch(
        "/api/v1/organization/settings", headers=headers, json={"missing_price_policy": "warn"}
    )
    seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv",
        headers=headers,
    )

    recorded = set(actions(seeded_client, headers))
    assert {
        "project.created",
        "boq.created",
        "boq_item.created",
        "boq_item.approved",
        "estimate.created",
        "organization.settings.updated",
        "estimate_version.frozen",
        "estimate_version.exported",
    } <= recorded


def test_the_frozen_event_records_the_digest_and_the_price_book_version(seeded_client, headers):
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    seeded_client.patch(
        "/api/v1/organization/settings", headers=headers, json={"missing_price_policy": "warn"}
    )
    seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    event = next(
        e
        for e in seeded_client.get("/api/v1/audit/events", headers=headers).json()["items"]
        if e["action"] == "estimate_version.frozen"
    )
    assert len(event["payload"]["snapshot_sha256"]) == 64
    assert event["payload"]["price_book_version_id"]


def test_the_chain_is_valid_on_a_fresh_journal(seeded_client, headers):
    report = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
    assert report["valid"] is True
    assert report["checked"] >= 1
    assert len(report["head_hash"]) == 64


def test_events_are_numbered_without_gaps(seeded_client, headers):
    seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-100", "name": "A"}
    )
    seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-101", "name": "B"}
    )
    events = seeded_client.get("/api/v1/audit/events?limit=200", headers=headers).json()["items"]
    sequences = sorted(e["sequence"] for e in events)
    assert sequences == list(range(1, len(sequences) + 1))


def test_tampering_with_a_row_is_detected(seeded_client, headers, seeded):
    """The journal is tamper-evident: editing a stored row breaks verification."""
    from metreo_api.db import get_session_factory
    from metreo_api.models import AuditEvent

    session = get_session_factory()()
    try:
        event = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == seeded["organization_a"])
            .order_by(AuditEvent.sequence.asc())
            .limit(1)
        ).one()
        event.summary = "résumé falsifié"
        session.commit()
    finally:
        session.close()

    report = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
    assert report["valid"] is False
    assert report["reason"] == "hash_mismatch"
    assert report["failed_at_sequence"] == 1


def test_deleting_a_row_is_detected(seeded_client, headers, seeded):
    from metreo_api.db import get_session_factory
    from metreo_api.models import AuditEvent

    seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-102", "name": "C"}
    )
    session = get_session_factory()()
    try:
        first = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == seeded["organization_a"])
            .order_by(AuditEvent.sequence.asc())
            .limit(1)
        ).one()
        session.delete(first)
        session.commit()
    finally:
        session.close()

    report = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
    assert report["valid"] is False
    assert report["reason"] == "sequence_gap"


def test_the_journal_carries_no_document_content_or_secret(seeded_client, headers):
    events = seeded_client.get("/api/v1/audit/events?limit=200", headers=headers).json()["items"]
    serialised = repr(events).lower()
    for forbidden in ("bearer ", "password", "access_token", "jwt_secret"):
        assert forbidden not in serialised


def test_an_estimator_cannot_read_the_journal(seeded_client):
    estimator = login(seeded_client, "metreur@dubois.demo")
    response = seeded_client.get("/api/v1/audit/events", headers=estimator)
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "audit:read"


def test_a_reader_can_read_the_journal(seeded_client):
    reader = login(seeded_client, "lecteur@dubois.demo")
    assert seeded_client.get("/api/v1/audit/events", headers=reader).status_code == 200
