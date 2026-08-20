"""Bill of quantities: units, approved quantities and manual construction."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def boq(seeded_client: TestClient, headers: dict[str, str]) -> dict:
    project = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-300", "name": "Test métré"}
    ).json()
    return seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=headers, json={"name": "Métré interne"}
    ).json()


def add(client, headers, boq, **kwargs):
    body = {
        "position": "1.1",
        "designation": "Poste",
        "unit_code": "m3",
        "quantity": "10",
        **kwargs,
    }
    return client.post(f"/api/v1/boqs/{boq['id']}/items", headers=headers, json=body)


def test_units_are_normalised_to_their_canonical_code(seeded_client, headers, boq):
    created = add(seeded_client, headers, boq, unit_code="M³").json()
    assert created["unit_code"] == "m3"


def test_an_unknown_unit_is_refused_with_a_usable_message(seeded_client, headers, boq):
    response = add(seeded_client, headers, boq, unit_code="bordure")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unknown_unit"
    assert detail["unit"] == "bordure"


def test_positions_are_unique_inside_a_bill_of_quantities(seeded_client, headers, boq):
    add(seeded_client, headers, boq, position="2.1")
    duplicate = add(seeded_client, headers, boq, position="2.1")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_position"


def test_the_same_position_is_allowed_in_another_bill_of_quantities(seeded_client, headers, boq):
    add(seeded_client, headers, boq, position="3.1")
    other_project = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-301", "name": "Autre"}
    ).json()
    other_boq = seeded_client.post(
        f"/api/v1/projects/{other_project['id']}/boqs", headers=headers, json={"name": "Métré 2"}
    ).json()
    assert add(seeded_client, headers, other_boq, position="3.1").status_code == 201


def test_bulk_creation_keeps_the_submitted_order(seeded_client, headers, boq):
    response = seeded_client.post(
        f"/api/v1/boqs/{boq['id']}/items:bulk",
        headers=headers,
        json={
            "items": [
                {
                    "position": "1",
                    "designation": "TERRASSEMENTS",
                    "kind": "section",
                    "unit_code": "fft",
                },
                {
                    "position": "1.1",
                    "designation": "Décapage",
                    "unit_code": "m2",
                    "quantity": "500",
                },
                {
                    "position": "1.2",
                    "designation": "Excavation",
                    "unit_code": "m3",
                    "quantity": "220",
                },
            ]
        },
    )
    assert response.status_code == 201
    items = seeded_client.get(f"/api/v1/boqs/{boq['id']}/items", headers=headers).json()
    assert [item["position"] for item in items] == ["1", "1.1", "1.2"]
    assert [item["sort_index"] for item in items] == sorted(i["sort_index"] for i in items)


def test_an_approved_quantity_cannot_be_changed_silently(seeded_client, headers, boq):
    item = add(seeded_client, headers, boq, position="4.1").json()
    seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=headers)

    response = seeded_client.patch(
        f"/api/v1/boq-items/{item['id']}", headers=headers, json={"quantity": "999"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "approved_quantity_locked"
    assert detail["current_quantity"] == "10"

    unchanged = seeded_client.get(f"/api/v1/boqs/{boq['id']}/items", headers=headers).json()
    assert next(i for i in unchanged if i["id"] == item["id"])["quantity"] == "10"


def test_overriding_an_approved_quantity_requires_a_reason(seeded_client, headers, boq):
    item = add(seeded_client, headers, boq, position="4.2").json()
    seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=headers)
    response = seeded_client.patch(
        f"/api/v1/boq-items/{item['id']}",
        headers=headers,
        json={"quantity": "999", "override_approved": True},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "override_reason_required"


def test_a_justified_override_is_accepted_downgraded_and_journalled(seeded_client, headers, boq):
    item = add(seeded_client, headers, boq, position="4.3").json()
    seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=headers)
    response = seeded_client.patch(
        f"/api/v1/boq-items/{item['id']}",
        headers=headers,
        json={
            "quantity": "12.5",
            "override_approved": True,
            "override_reason": "Relevé contradictoire du 3 mars",
        },
    )
    assert response.status_code == 200
    assert response.json()["quantity"] == "12.5"
    # An overridden quantity is no longer "approved": it must be re-approved.
    assert response.json()["status"] == "verified"

    event = next(
        e
        for e in seeded_client.get(
            f"/api/v1/audit/events?object_id={item['id']}", headers=headers
        ).json()["items"]
        if e["action"] == "boq_item.updated"
    )
    assert event["payload"]["override_reason"] == "Relevé contradictoire du 3 mars"
    assert event["payload"]["before"]["quantity"] == "10"


def test_changing_a_non_quantity_field_of_an_approved_item_is_allowed(seeded_client, headers, boq):
    item = add(seeded_client, headers, boq, position="4.4").json()
    seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=headers)
    response = seeded_client.patch(
        f"/api/v1/boq-items/{item['id']}", headers=headers, json={"notes": "à revoir avec le CSC"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_an_estimator_cannot_approve_a_quantity(seeded_client, headers, boq):
    item = add(seeded_client, headers, boq, position="4.5").json()
    estimator = login(seeded_client, "metreur@dubois.demo")
    response = seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=estimator)
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "boq:approve"


def test_a_boq_cannot_be_attached_to_another_project_estimate(seeded_client, headers, boq):
    other = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-302", "name": "Autre projet"}
    ).json()
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    response = seeded_client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": other["id"],
            "boq_id": boq["id"],
            "price_book_version_id": pb_version,
            "name": "Incohérente",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "boq_project_mismatch"


def test_client_quantity_can_be_recorded_next_to_the_internal_one(seeded_client, headers, boq):
    created = add(
        seeded_client, headers, boq, position="5.1", quantity="118", client_quantity="120"
    ).json()
    assert created["quantity"] == "118"
    assert created["client_quantity"] == "120"
