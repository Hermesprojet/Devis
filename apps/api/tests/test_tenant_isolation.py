"""Acceptance scenario 1: two organisations cannot see each other's data.

The checks go through the HTTP API with a valid token, which is the attack that
matters: a caller who is legitimately authenticated but points at somebody
else's identifier.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def org_a(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def org_b(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@janssens.demo")


def test_each_organisation_only_lists_its_own_projects(seeded_client, org_a, org_b):
    a_projects = seeded_client.get("/api/v1/projects", headers=org_a).json()
    b_projects = seeded_client.get("/api/v1/projects", headers=org_b).json()
    assert a_projects["page"]["total"] == 1
    assert b_projects["page"]["total"] == 0


def test_direct_api_call_on_another_tenant_project_returns_404(seeded_client, org_a, org_b):
    project_id = seeded_client.get("/api/v1/projects", headers=org_a).json()["items"][0]["id"]
    response = seeded_client.get(f"/api/v1/projects/{project_id}", headers=org_b)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_another_tenant_cannot_patch_a_project(seeded_client, org_a, org_b):
    project_id = seeded_client.get("/api/v1/projects", headers=org_a).json()["items"][0]["id"]
    response = seeded_client.patch(
        f"/api/v1/projects/{project_id}", headers=org_b, json={"name": "détourné"}
    )
    assert response.status_code == 404


def test_another_tenant_cannot_delete_a_project(seeded_client, org_a, org_b):
    project_id = seeded_client.get("/api/v1/projects", headers=org_a).json()["items"][0]["id"]
    assert seeded_client.delete(f"/api/v1/projects/{project_id}", headers=org_b).status_code == 404
    assert seeded_client.get(f"/api/v1/projects/{project_id}", headers=org_a).status_code == 200


def test_another_tenant_cannot_read_an_estimate_or_its_computation(seeded_client, org_a, org_b):
    estimate = seeded_client.get("/api/v1/estimates", headers=org_a).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=org_a
    ).json()[0]
    assert (
        seeded_client.get(f"/api/v1/estimates/{estimate['id']}", headers=org_b).status_code == 404
    )
    assert (
        seeded_client.get(
            f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
            headers=org_b,
        ).status_code
        == 404
    )


def test_another_tenant_cannot_export_a_quote(seeded_client, org_a, org_b):
    estimate = seeded_client.get("/api/v1/estimates", headers=org_a).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=org_a
    ).json()[0]
    for suffix in ("export.csv", "quote.html"):
        response = seeded_client.get(
            f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/{suffix}",
            headers=org_b,
        )
        assert response.status_code == 404, suffix


def test_another_tenant_cannot_read_price_items(seeded_client, org_a, org_b):
    books = seeded_client.get("/api/v1/price-books", headers=org_a).json()
    versions = seeded_client.get(
        f"/api/v1/price-books/{books[0]['id']}/versions", headers=org_a
    ).json()
    version_id = versions[0]["id"]
    assert (
        seeded_client.get(
            f"/api/v1/price-books/versions/{version_id}/items", headers=org_b
        ).status_code
        == 404
    )


def test_a_boq_row_cannot_reference_another_tenant_price(seeded_client, org_a, org_b):
    """The cross-tenant reference is refused at write time, not at read time."""
    books_a = seeded_client.get("/api/v1/price-books", headers=org_a).json()
    version_a = seeded_client.get(
        f"/api/v1/price-books/{books_a[0]['id']}/versions", headers=org_a
    ).json()[0]["id"]
    price_a = seeded_client.get(
        f"/api/v1/price-books/versions/{version_a}/items", headers=org_a
    ).json()["items"][0]["id"]

    project_b = seeded_client.post(
        "/api/v1/projects", headers=org_b, json={"reference": "B-001", "name": "Projet B"}
    ).json()
    boq_b = seeded_client.post(
        f"/api/v1/projects/{project_b['id']}/boqs", headers=org_b, json={"name": "Meetstaat"}
    ).json()
    response = seeded_client.post(
        f"/api/v1/boqs/{boq_b['id']}/items",
        headers=org_b,
        json={
            "position": "1.1",
            "designation": "Poste avec prix volé",
            "unit_code": "m3",
            "quantity": "10",
            "price_item_id": price_a,
        },
    )
    assert response.status_code == 404


def test_audit_journals_are_separate(seeded_client, org_a, org_b):
    seeded_client.post(
        "/api/v1/projects", headers=org_b, json={"reference": "B-002", "name": "Projet B2"}
    )
    a_events = seeded_client.get("/api/v1/audit/events", headers=org_a).json()
    b_events = seeded_client.get("/api/v1/audit/events", headers=org_b).json()
    a_ids = {event["object_id"] for event in a_events["items"]}
    b_ids = {event["object_id"] for event in b_events["items"]}
    assert a_ids.isdisjoint(b_ids)


def test_a_token_is_bound_to_one_organisation(seeded_client, seeded):
    """A token minted for org A does not become a token for org B by
    swapping the identifier in the request."""
    headers = login(seeded_client, "admin@dubois.demo")
    response = seeded_client.get("/api/v1/organization", headers=headers)
    assert response.json()["id"] == seeded["organization_a"]


def test_unauthenticated_calls_are_refused(seeded_client):
    for path in ("/api/v1/projects", "/api/v1/estimates", "/api/v1/audit/events"):
        assert seeded_client.get(path).status_code == 401


def test_a_forged_token_is_refused(seeded_client):
    response = seeded_client.get(
        "/api/v1/projects", headers={"Authorization": "Bearer not.a.token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_token"


def test_a_token_signed_with_another_secret_is_refused(seeded_client, seeded):
    import jwt

    forged = jwt.encode(
        {
            "sub": "whoever",
            "org": seeded["organization_a"],
            "iss": "metreo-api",
            "exp": 9999999999,
        },
        "the-wrong-secret",
        algorithm="HS256",
    )
    response = seeded_client.get("/api/v1/projects", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
