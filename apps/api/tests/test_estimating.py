"""Acceptance scenarios 3 to 8: computation, freezing and exports."""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def estimate(seeded_client: TestClient, headers: dict[str, str]) -> dict:
    return seeded_client.get("/api/v1/estimates", headers=headers).json()[0]


@pytest.fixture()
def version(seeded_client: TestClient, headers: dict[str, str], estimate: dict) -> dict:
    return seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]


def compute(client: TestClient, headers, estimate, version):
    response = client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def unpriced_item_id(client: TestClient, headers, estimate) -> str:
    items = client.get(f"/api/v1/boqs/{estimate['boq_id']}/items", headers=headers).json()
    return next(
        item["id"]
        for item in items
        if item["price_item_id"] is None
        and item["composite_price_id"] is None
        and item["kind"] != "section"
    )


def price_the_missing_line(client: TestClient, headers, estimate) -> None:
    """Give the deliberately unpriced demo line a price so freezing is allowed."""
    book = client.get("/api/v1/price-books", headers=headers).json()[0]
    version_id = client.get(f"/api/v1/price-books/{book['id']}/versions", headers=headers).json()[
        0
    ]["id"]
    created = client.post(
        f"/api/v1/price-books/versions/{version_id}/items",
        headers=headers,
        json={
            "code": "RAC-PART-001",
            "label": "Mise en conformité d'un raccordement particulier",
            "unit_code": "pce",
            "unit_price": "480.00",
            "resource_kind": "subcontract",
        },
    )
    assert created.status_code == 201, created.text
    item_id = unpriced_item_id(client, headers, estimate)
    patched = client.patch(
        f"/api/v1/boq-items/{item_id}",
        headers=headers,
        json={"price_item_id": created.json()["id"]},
    )
    assert patched.status_code == 200, patched.text


# -- scenario 3: a sub-detail is visible and reproducible ------------------


def test_excavation_line_exposes_its_whole_sub_detail(seeded_client, headers, estimate, version):
    result = compute(seeded_client, headers, estimate, version)["result"]
    line = next(line for line in result["lines"] if line["code"] == "1.2")
    assert line["quantity"] == "620"
    assert line["unit"] == "m3"
    kinds = {component["kind"] for component in line["price"]["components"]}
    assert kinds == {"equipment", "labor", "transport", "disposal"}
    for component in line["price"]["components"]:
        assert component["formula"]


def test_the_computation_is_reproducible(seeded_client, headers, estimate, version):
    first = compute(seeded_client, headers, estimate, version)["result"]
    second = compute(seeded_client, headers, estimate, version)["result"]
    assert first == second


def test_every_markup_step_is_returned(seeded_client, headers, estimate, version):
    result = compute(seeded_client, headers, estimate, version)["result"]
    line = next(line for line in result["lines"] if line["code"] == "1.2")
    assert [step["key"] for step in line["price"]["markup_steps"]] == [
        "site_overheads",
        "general_overheads",
        "contingency",
        "margin",
    ]


def test_density_source_is_carried_all_the_way_to_the_api(
    seeded_client, headers, estimate, version
):
    result = compute(seeded_client, headers, estimate, version)["result"]
    line = next(line for line in result["lines"] if line["code"] == "1.2")
    disposal = next(c for c in line["price"]["components"] if c["kind"] == "disposal")
    assert "GT-2026-018" in disposal["density_source"]


# -- scenario 4: refused conversion ----------------------------------------


def test_a_price_per_tonne_on_a_cubic_metre_line_is_refused(
    seeded_client, headers, estimate, version
):
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    price = seeded_client.get(
        f"/api/v1/price-books/versions/{pb_version}/items?q=EVA-TER-001", headers=headers
    ).json()["items"][0]

    items = seeded_client.get(f"/api/v1/boqs/{estimate['boq_id']}/items", headers=headers).json()
    excavation = next(item for item in items if item["position"] == "1.2")
    seeded_client.patch(
        f"/api/v1/boq-items/{excavation['id']}",
        headers=headers,
        json={"composite_price_id": None, "price_item_id": price["id"]},
    )

    response = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
        headers=headers,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unpriceable_lines"
    problem = detail["problems"][0]
    assert problem["code"] == "ambiguous_conversion"
    assert problem["position"] == "1.2"
    assert "masse volumique" in problem["hint"] or "sous-détail" in problem["hint"]


# -- scenario 5: missing price blocks approval ------------------------------


def test_an_unpriced_line_is_flagged_and_blocks(seeded_client, headers, estimate, version):
    result = compute(seeded_client, headers, estimate, version)["result"]
    assert result["blocking"] is True
    missing = [line for line in result["lines"] if line["missing_price"]]
    assert [line["code"] for line in missing] == ["4.2"]


def test_freezing_is_refused_while_a_line_has_no_price(seeded_client, headers, estimate, version):
    response = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "missing_prices"


def test_warn_policy_lets_the_version_be_frozen(seeded_client, headers, estimate, version):
    seeded_client.patch(
        "/api/v1/organization/settings", headers=headers, json={"missing_price_policy": "warn"}
    )
    response = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "frozen"


# -- scenarios 6 and 7: freezing is immutable -------------------------------


def test_freezing_requires_an_explicit_confirmation(seeded_client, headers, estimate, version):
    price_the_missing_line(seeded_client, headers, estimate)
    response = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_freezing_records_the_price_book_version_and_a_digest(
    seeded_client, headers, estimate, version
):
    price_the_missing_line(seeded_client, headers, estimate)
    frozen = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True, "label": "Remise du 12 mars"},
    ).json()
    assert frozen["status"] == "frozen"
    assert frozen["price_book_version_id"] == estimate["price_book_version_id"]
    assert len(frozen["snapshot_sha256"]) == 64
    assert frozen["total_selling_price_ht"] is not None
    # Stored unrounded, displayed with the version's rounding policy.
    assert frozen["total_selling_price_ht_display"].count(".") == 1
    assert len(frozen["total_selling_price_ht_display"].split(".")[1]) == 2


def test_a_later_price_change_does_not_move_a_frozen_total(
    seeded_client, headers, estimate, version
):
    price_the_missing_line(seeded_client, headers, estimate)
    frozen = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    ).json()
    total_before = frozen["total_selling_price_ht"]

    # Triple the price of a resource used by the frozen version.
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    csv_payload = (
        "code;libelle;unite;prix_unitaire\nMAT-TUY-160;Tuyau béton non armé DN 400;m;146,25\n"
    ).encode()
    batch = seeded_client.post(
        f"/api/v1/price-books/versions/{pb_version}/imports/preview",
        headers=headers,
        files={"file": ("hausse.csv", csv_payload, "text/csv")},
    ).json()
    outcome = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "replace", "confirm": True},
    ).json()
    assert outcome["updated"] == 1

    after = compute(seeded_client, headers, estimate, version)
    assert after["from_snapshot"] is True
    # The stored figure is unrounded; the displayed one applies the version's
    # own rounding policy. Neither moved.
    assert after["result"]["total_selling_price_ht_raw"] == total_before
    assert after["result"]["total_selling_price_ht"] == frozen["total_selling_price_ht_display"]
    assert after["version"]["total_selling_price_ht"] == total_before


def test_a_frozen_version_cannot_be_frozen_again(seeded_client, headers, estimate, version):
    price_the_missing_line(seeded_client, headers, estimate)
    body = {"confirm": True}
    url = f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze"
    assert seeded_client.post(url, headers=headers, json=body).status_code == 200
    second = seeded_client.post(url, headers=headers, json=body)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_frozen"


def test_a_new_draft_version_reflects_the_new_prices(seeded_client, headers, estimate, version):
    price_the_missing_line(seeded_client, headers, estimate)
    frozen = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    ).json()
    new_version = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions",
        headers=headers,
        json={"label": "Après hausse tarifaire"},
    ).json()
    assert new_version["version_number"] == frozen["version_number"] + 1
    assert new_version["status"] == "draft"


# -- scenario 8: exports ----------------------------------------------------


def test_csv_export_carries_reference_version_units_and_amounts(
    seeded_client, headers, estimate, version
):
    response = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    assert "# Référence projet;2026-014" in text
    assert "# Version;1" in text

    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    header_row = next(row for row in rows if row and row[0] == "Poste")
    assert header_row == [
        "Poste",
        "Désignation",
        "Unité",
        "Quantité",
        "P.U. HT",
        "Total HT",
        "Statut",
    ]
    flat = "\n".join(";".join(row) for row in rows)
    assert "PRIX MANQUANT" in flat
    assert "HORS TOTAL (option/variante)" in flat
    assert "Total HT" in flat


def test_client_csv_export_hides_internal_costs(seeded_client, headers, estimate, version):
    text = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv",
        headers=headers,
    ).content.decode("utf-8-sig")
    assert "Déboursé sec" not in text


def test_internal_csv_export_adds_cost_columns(seeded_client, headers, estimate, version):
    text = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv?include_internal=true",
        headers=headers,
    ).content.decode("utf-8-sig")
    assert "Déboursé sec" in text
    assert "Prix de revient" in text
    assert "Marge" in text


def test_a_project_manager_cannot_export_internal_costs(seeded_client, estimate, version):
    """The role may read costs on screen but not export them."""
    from metreo_api.security.roles import Permission, permissions_for

    assert Permission.EXPORT_INTERNAL not in permissions_for("project_manager")


def test_quote_preview_is_html_and_warns_about_demo_prices(
    seeded_client, headers, estimate, version
):
    response = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/quote.html",
        headers=headers,
    )
    assert response.status_code == 200
    html = response.text
    assert html.startswith("<!doctype html>")
    assert "Terrassements Dubois SA" in html
    assert "prix de démonstration fictifs" in html
    assert "version brouillon" in html
    assert "Déboursé sec" not in html


def test_quote_preview_of_a_frozen_version_shows_its_digest(
    seeded_client, headers, estimate, version
):
    price_the_missing_line(seeded_client, headers, estimate)
    seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    html = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/quote.html",
        headers=headers,
    ).text
    assert "Version gelée" in html
    assert "Empreinte de la version gelée" in html


# -- roles ------------------------------------------------------------------


def test_an_estimator_cannot_freeze(seeded_client, estimate, version):
    estimator = login(seeded_client, "metreur@dubois.demo")
    response = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=estimator,
        json={"confirm": True},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "estimate:freeze"


def test_a_reader_sees_totals_but_no_internal_costs(seeded_client, estimate, version):
    reader = login(seeded_client, "lecteur@dubois.demo")
    payload = compute(seeded_client, reader, estimate, version)
    assert payload["includes_internal_costs"] is False
    assert "total_direct_cost" not in payload["result"]
    line = next(line for line in payload["result"]["lines"] if line["code"] == "1.2")
    assert "components" not in line["price"]
    assert line["price"]["selling_price_ht"]


def test_margin_rates_are_masked_as_null_not_as_zero_for_a_reader(seeded_client):
    reader = login(seeded_client, "lecteur@dubois.demo")
    masked = seeded_client.get("/api/v1/organization/settings", headers=reader).json()
    assert masked["commercial_rates_visible"] is False
    assert masked["margin_rate"] is None
    assert masked["general_overheads_rate"] is None
    # The non-commercial part of the screen stays usable.
    assert masked["rounding_scale"] == 2
    assert masked["missing_price_policy"] == "block"

    admin = login(seeded_client, "admin@dubois.demo")
    visible = seeded_client.get("/api/v1/organization/settings", headers=admin).json()
    assert visible["commercial_rates_visible"] is True
    assert visible["margin_rate"] == "0.08"
