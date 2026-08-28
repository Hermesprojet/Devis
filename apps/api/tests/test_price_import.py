"""Acceptance scenario 2: preview shows errors before anything is written."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import login

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "imports"


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version_id(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    return seeded_client.get(f"/api/v1/price-books/{book['id']}/versions", headers=headers).json()[
        0
    ]["id"]


def upload(client: TestClient, headers: dict[str, str], version_id: str, name: str):
    payload = (FIXTURES / name).read_bytes()
    return client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": (name, payload, "text/csv")},
    )


def count_items(client: TestClient, headers: dict[str, str], version_id: str) -> int:
    return client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["page"]["total"]


def test_preview_reports_five_valid_and_two_broken_rows(seeded_client, headers, version_id):
    response = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["row_count"] == 7
    assert report["valid_count"] == 5
    assert report["error_count"] == 2


def test_preview_writes_nothing_into_the_library(seeded_client, headers, version_id):
    before = count_items(seeded_client, headers, version_id)
    upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv")
    assert count_items(seeded_client, headers, version_id) == before


def test_errors_name_the_line_the_column_and_the_reason(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv").json()
    failing = {row["line_number"]: row["errors"] for row in report["rows"] if not row["is_valid"]}
    assert set(failing) == {4, 7}
    assert {e["code"] for e in failing[4]} == {"unknown_unit"}
    assert {e["column"] for e in failing[4]} == {"unit_code"}
    assert {e["code"] for e in failing[7]} == {"required"}
    assert {e["column"] for e in failing[7]} == {"code", "unit_price"}


def test_commit_creates_exactly_the_valid_rows(seeded_client, headers, version_id):
    before = count_items(seeded_client, headers, version_id)
    batch = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv").json()
    response = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 5
    assert count_items(seeded_client, headers, version_id) == before + 5


def test_commit_requires_an_explicit_confirmation(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    response = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_a_batch_cannot_be_committed_twice(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    body = {"strategy": "create", "confirm": True}
    first = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit", headers=headers, json=body
    )
    second = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit", headers=headers, json=body
    )
    assert first.status_code == 200
    assert second.status_code == 409


def test_french_decimal_comma_and_semicolon_are_detected(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert report["meta"]["delimiter"] == ";"
    sand = next(r for r in report["rows"] if r["normalized"]["code"] == "MAT-SAB-001")
    assert sand["normalized"]["unit_price"] == "41.50"


def test_headers_are_mapped_from_french_labels(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert report["column_mapping"]["libelle"] == "label"
    assert report["column_mapping"]["prix_unitaire"] == "unit_price"
    assert report["meta"]["unmapped_headers"] == []


def test_second_import_of_the_same_codes_flags_duplicates(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    again = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert again["duplicate_count"] == 5


def test_replace_strategy_updates_instead_of_creating(seeded_client, headers, version_id):
    first = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{first['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    before = count_items(seeded_client, headers, version_id)
    second = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    outcome = seeded_client.post(
        f"/api/v1/price-books/imports/{second['batch_id']}/commit",
        headers=headers,
        json={"strategy": "replace", "confirm": True},
    ).json()
    assert outcome["updated"] == 5
    assert count_items(seeded_client, headers, version_id) == before


def test_create_strategy_reports_conflicts_without_overwriting(seeded_client, headers, version_id):
    first = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{first['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    second = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    outcome = seeded_client.post(
        f"/api/v1/price-books/imports/{second['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    ).json()
    assert outcome["created"] == 0
    assert outcome["conflicted"] == 5


def test_an_empty_file_is_reported_not_crashed(seeded_client, headers, version_id):
    response = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("vide.csv", b"", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["fatal"] == "empty_file"


def test_a_file_without_the_required_columns_is_rejected_before_parsing(
    seeded_client, headers, version_id
):
    response = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("bizarre.csv", b"colonne_a;colonne_b\n1;2\n", "text/csv")},
    )
    report = response.json()
    assert report["meta"]["fatal"] == "missing_required_columns"
    assert set(report["meta"]["missing_required_columns"]) == {
        "code",
        "label",
        "unit_code",
        "unit_price",
    }


def test_import_into_a_published_version_is_refused(seeded_client, headers, version_id):
    seeded_client.post(f"/api/v1/price-books/versions/{version_id}/publish", headers=headers)
    response = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_published"


def test_a_reader_cannot_import(seeded_client, version_id):
    reader = login(seeded_client, "lecteur@dubois.demo")
    response = upload(seeded_client, reader, version_id, "prix_valides_5_lignes.csv")
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "pricebook:write"
