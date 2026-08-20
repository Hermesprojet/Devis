"""Guarantees the pricing chain must keep, asserted end to end.

The arithmetic itself is covered by ``packages/domain/tests``. What is checked
here is what only the assembled application can show: that exact decimals
survive the database and the JSON layer, that the canonical digest is stable,
and that a frozen version stays frozen.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from metreo_api.services.estimating import canonical_snapshot, snapshot_digest

from .conftest import login
from .test_estimating import price_the_missing_line

# --------------------------------------------------------------------------
# Canonical serialisation of the frozen snapshot
# --------------------------------------------------------------------------


def test_equal_decimals_written_differently_produce_the_same_digest() -> None:
    """The property that makes the digest meaningful across backends.

    PostgreSQL hands back ``NUMERIC(28, 10)`` padded to ten decimals while
    SQLite returns the text that was written. Without canonicalisation the same
    frozen version would digest differently depending on the database it was
    read from.
    """
    padded = {"total": Decimal("10.0000000000"), "qty": Decimal("1.00"), "n": Decimal("1E+2")}
    bare = {"total": Decimal("10"), "qty": Decimal("1"), "n": Decimal("100")}
    assert padded["total"] == bare["total"]
    assert snapshot_digest(padded) == snapshot_digest(bare)


def test_key_order_does_not_change_the_digest() -> None:
    assert snapshot_digest({"a": 1, "b": 2}) == snapshot_digest({"b": 2, "a": 1})


def test_a_changed_amount_changes_the_digest() -> None:
    base = {"total": Decimal("10.00")}
    assert snapshot_digest(base) != snapshot_digest({"total": Decimal("10.01")})


def test_a_decimal_and_its_string_spelling_are_not_interchangeable() -> None:
    """Tagging keeps ``Decimal(10)`` and ``"10"`` apart in the digest."""
    assert snapshot_digest({"v": Decimal("10")}) != snapshot_digest({"v": "10"})


def test_negative_zero_is_canonicalised() -> None:
    assert snapshot_digest({"v": Decimal("-0")}) == snapshot_digest({"v": Decimal("0")})


def test_a_binary_float_is_refused_rather_than_stringified() -> None:
    """No ``default=str`` fallback: an unexpected type is a bug, not a value."""
    with pytest.raises(TypeError, match="flottant binaire"):
        snapshot_digest({"total": 10.5})


def test_an_unknown_type_is_refused() -> None:
    class Opaque:
        pass

    with pytest.raises(TypeError, match="non sérialisable"):
        snapshot_digest({"v": Opaque()})


def test_dates_survive_canonicalisation() -> None:
    assert canonical_snapshot({"d": date(2026, 8, 20)}) == {"d": {"$date": "2026-08-20"}}


def test_a_non_finite_decimal_is_refused() -> None:
    with pytest.raises(ValueError, match="non finie"):
        snapshot_digest({"v": Decimal("NaN")})


# --------------------------------------------------------------------------
# Exact decimals across storage and transport
# --------------------------------------------------------------------------


def _seeded_estimate(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    estimates = client.get("/api/v1/estimates", headers=headers).json()
    assert estimates, "le jeu de démonstration doit contenir une estimation"
    estimate_id = estimates[0]["id"]
    versions = client.get(f"/api/v1/estimates/{estimate_id}/versions", headers=headers).json()
    assert versions
    return estimate_id, versions[0]["id"]


def test_amounts_travel_as_json_strings_not_as_numbers(seeded_client: TestClient) -> None:
    """A JSON number would go through a double on the client and lose cents."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate_id, version_id = _seeded_estimate(seeded_client, headers)
    response = seeded_client.get(
        f"/api/v1/estimates/{estimate_id}/versions/{version_id}/computation", headers=headers
    )
    assert response.status_code == 200, response.text

    raw = json.loads(response.text)
    numeric_amounts: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, float):
            numeric_amounts.append(path)

    walk(raw, "computation")
    assert numeric_amounts == [], (
        "Ces champs voyagent en nombre JSON et perdront de la précision côté "
        f"client : {numeric_amounts}"
    )


def test_an_exact_decimal_survives_a_round_trip_through_the_database(
    seeded_client: TestClient,
) -> None:
    """0.1 + 0.2 is the classic float trap; the stored value must be exact."""
    headers = login(seeded_client, "admin@dubois.demo")
    book = seeded_client.post(
        "/api/v1/price-books", headers=headers, json={"name": "Précision"}
    ).json()
    version = seeded_client.post(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers, json={"label": "v1"}
    ).json()
    created = seeded_client.post(
        f"/api/v1/price-books/versions/{version['id']}/items",
        headers=headers,
        json={
            "code": "P.1",
            "label": "Article à décimales longues",
            "unit_code": "m3",
            "unit_price": "12.3456789012",
        },
    )
    assert created.status_code == 201, created.text

    listed = seeded_client.get(
        f"/api/v1/price-books/versions/{version['id']}/items", headers=headers
    ).json()["items"]
    stored = next(row for row in listed if row["code"] == "P.1")
    assert stored["unit_price"] == "12.3456789012"


# --------------------------------------------------------------------------
# The digest actually matches the snapshot it claims to cover
# --------------------------------------------------------------------------
#
# That a frozen version refuses a second freeze, demands an explicit
# confirmation and ignores later price changes is covered by
# ``tests/test_estimating.py``. What is asserted here is the property that
# makes the stored digest worth storing.


def test_the_stored_digest_matches_a_recomputation_from_the_stored_snapshot(
    seeded_client: TestClient,
) -> None:
    """Recomputing the digest from the row detects a snapshot edited in place."""
    from metreo_api.db import get_session_factory
    from metreo_api.models import EstimateVersion

    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    frozen = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert frozen.status_code == 200, frozen.text

    session = get_session_factory()()
    try:
        row = session.get(EstimateVersion, version["id"])
        assert row is not None and row.snapshot is not None
        assert row.snapshot_sha256 == snapshot_digest(row.snapshot)

        tampered = json.loads(json.dumps(row.snapshot))
        tampered["currency"] = "USD"
        assert snapshot_digest(tampered) != row.snapshot_sha256
    finally:
        session.close()
