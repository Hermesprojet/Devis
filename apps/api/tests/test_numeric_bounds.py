"""Les bornes métier au niveau HTTP : refus lisible, jamais d'erreur SQL."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from metreo_domain import bounds

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def boq(seeded_client: TestClient, headers: dict[str, str]) -> dict:
    project = seeded_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"reference": "2026-BORNES", "name": "Essai des bornes"},
    ).json()
    return seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs",
        headers=headers,
        json={"name": "Métré interne"},
    ).json()


@pytest.fixture()
def price_version(seeded_client: TestClient, headers: dict[str, str]) -> dict:
    """Les prix vivent sur une version de bibliothèque, pas sur la bibliothèque."""
    book = seeded_client.post(
        "/api/v1/price-books",
        headers=headers,
        json={"name": "Bibliothèque d'essai"},
    ).json()
    # `label` est un paramètre de requête sur cette route, pas un corps JSON.
    return seeded_client.post(
        f"/api/v1/price-books/{book['id']}/versions",
        headers=headers,
        params={"label": "v1"},
    ).json()


def _first_boq_item_payload(quantity: str) -> dict[str, object]:
    return {
        "position": "1.1",
        "designation": "Déblai en pleine masse",
        "unit_code": "m3",
        "quantity": quantity,
    }


class TestQuantityBounds:
    def test_a_quantity_past_the_maximum_is_refused_with_422(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json=_first_boq_item_payload("1e12"),
        )
        assert response.status_code == 422
        body = response.json()
        # Le refus vient de la validation d'entrée, pas d'une erreur de base.
        assert "detail" in body
        assert "500" not in str(response.status_code)

    def test_the_maximum_itself_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json=_first_boq_item_payload(str(bounds.QUANTITY.maximum)),
        )
        assert response.status_code == 201, response.text

    def test_a_negative_quantity_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json=_first_boq_item_payload("-1"),
        )
        assert response.status_code == 422


class TestPriceBounds:
    def test_a_unit_price_past_the_maximum_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], price_version: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{price_version['id']}/items",
            headers=headers,
            json={
                "code": "X1",
                "label": "Poste hors bornes",
                "unit_code": "m3",
                "unit_price": str(bounds.UNIT_PRICE.maximum + Decimal(1)),
            },
        )
        assert response.status_code == 422

    def test_the_maximum_unit_price_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], price_version: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{price_version['id']}/items",
            headers=headers,
            json={
                "code": "X2",
                "label": "Ouvrage d'art au forfait",
                "unit_code": "fft",
                "unit_price": str(bounds.UNIT_PRICE.maximum),
            },
        )
        assert response.status_code == 201, response.text


class TestStoredValuesStayWithinCapacity:
    def test_a_maximal_quantity_round_trips_through_the_database(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        """Le point que la borne protège : écrire puis relire sans perte.

        Sur PostgreSQL une valeur trop large lève une erreur SQL ; sur SQLite,
        où le décimal est stocké en texte, elle passerait en silence. Les deux
        moteurs doivent rendre exactement ce qui a été écrit.
        """
        created = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json=_first_boq_item_payload(str(bounds.QUANTITY.maximum)),
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        listed = seeded_client.get(f"/api/v1/boqs/{boq['id']}/items", headers=headers)
        assert listed.status_code == 200
        stored = next(row for row in listed.json() if row["id"] == item_id)
        assert Decimal(stored["quantity"]) == bounds.QUANTITY.maximum
