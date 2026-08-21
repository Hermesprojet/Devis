"""Invariants métier du §6, au niveau HTTP."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def boq(seeded_client: TestClient, headers: dict[str, str]) -> dict:
    project = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "INV-1", "name": "Invariants"}
    ).json()
    return seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=headers, json={"name": "Métré"}
    ).json()


@pytest.fixture()
def version(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.post(
        "/api/v1/price-books", headers=headers, json={"name": "Invariants"}
    ).json()
    return seeded_client.post(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers, params={"label": "v1"}
    ).json()["id"]


def _composite(
    seeded_client: TestClient, headers: dict[str, str], version: str, components: list[dict]
) -> Any:
    return seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites",
        headers=headers,
        json={
            "code": "C1",
            "label": "Sous-détail",
            "unit_code": "m3",
            "components": components,
        },
    )


class TestDiscriminatedComponents:
    """§6.1 — un composant ne porte plus les champs d'un autre type."""

    def test_a_lump_sum_carrying_an_output_rate_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "lump_sum",
                    "label": "Forfait",
                    "lump_sum_amount": "100",
                    # Champ d'un autre type : auparavant accepté puis ignoré,
                    # l'utilisateur croyant avoir paramétré un rendement.
                    "output_rate": "12",
                }
            ],
        )
        assert response.status_code == 422, response.text

    def test_a_consumption_carrying_a_distance_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "consumption",
                    "label": "Grave",
                    "consumption": "0.35",
                    "resource_unit_code": "t",
                    "unit_price": "18",
                    "distance_km": "12",
                }
            ],
        )
        assert response.status_code == 422, response.text

    def test_a_well_formed_component_is_still_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "consumption",
                    "label": "Grave",
                    "consumption": "0.35",
                    "resource_unit_code": "t",
                    "unit_price": "18",
                }
            ],
        )
        assert response.status_code == 201, response.text


class TestComponentInvariants:
    """§6.2 — valeurs strictement positives, densité sourcée, kilométrage."""

    @pytest.mark.parametrize(
        "component",
        [
            pytest.param(
                {
                    "component_type": "output_rate",
                    "label": "Pelle",
                    "output_rate": "0",
                    "hourly_rate": "45",
                },
                id="rendement-nul",
            ),
            pytest.param(
                {
                    "component_type": "output_rate",
                    "label": "Équipe",
                    "output_rate": "12",
                    "hourly_rate": "45",
                    "crew_size": "0",
                },
                id="effectif-nul",
            ),
            pytest.param(
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "payload_value": "0",
                    "payload_unit_code": "t",
                    "cost_per_rotation": "85",
                },
                id="charge-utile-nulle",
            ),
            pytest.param(
                {
                    "component_type": "consumption",
                    "label": "Terres",
                    "consumption": "1",
                    "resource_unit_code": "t",
                    "unit_price": "12",
                    "density_value": "1800",
                },
                id="densité-sans-source",
            ),
            pytest.param(
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "payload_value": "8",
                    "payload_unit_code": "t",
                    "distance_km": "12",
                },
                id="distance-sans-tarif",
            ),
            pytest.param(
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "payload_value": "8",
                    "payload_unit_code": "t",
                    "rate_per_km": "1.20",
                },
                id="tarif-sans-distance",
            ),
        ],
    )
    def test_the_invariant_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str, component: dict
    ) -> None:
        assert _composite(seeded_client, headers, version, [component]).status_code == 422

    def test_a_sourced_density_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "consumption",
                    "label": "Terres",
                    "consumption": "1",
                    "resource_unit_code": "t",
                    "unit_price": "12",
                    "convert_boq_quantity": True,
                    "density_value": "1800",
                    "density_source": "Rapport de sol GEO-2026-014, essai n°3",
                }
            ],
        )
        assert response.status_code == 201, response.text


class TestExclusivePriceSource:
    """§6.4 — un poste tire son prix d'une seule source."""

    def test_creating_a_row_with_both_sources_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json={
                "position": "1.1",
                "designation": "Deux sources",
                "unit_code": "m3",
                "quantity": "10",
                "price_item_id": "11111111-1111-1111-1111-111111111111",
                "composite_price_id": "22222222-2222-2222-2222-222222222222",
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "conflicting_price_sources"

    def test_adding_a_second_source_by_update_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict, version: str
    ) -> None:
        """La règle porte sur l'état FINAL, pas sur la seule requête."""
        composite = _composite(
            seeded_client,
            headers,
            version,
            [{"component_type": "lump_sum", "label": "Forfait", "lump_sum_amount": "100"}],
        ).json()
        item = seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json={
                "position": "2.1",
                "designation": "Une source",
                "unit_code": "m3",
                "quantity": "10",
                "composite_price_id": composite["id"],
            },
        ).json()

        prices = seeded_client.get("/api/v1/price-books", headers=headers).json()
        version = seeded_client.get(
            f"/api/v1/price-books/{prices[0]['id']}/versions", headers=headers
        ).json()[0]
        items = seeded_client.get(
            f"/api/v1/price-books/versions/{version['id']}/items", headers=headers
        ).json()
        existing_price = (items["items"] if isinstance(items, dict) else items)[0]

        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}",
            headers=headers,
            json={"price_item_id": existing_price["id"]},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "conflicting_price_sources"


class TestProjectUpdateLengths:
    """§6.5 — la même donnée ne peut pas être valide selon le verbe employé."""

    def test_an_over_long_address_is_refused_on_update_as_on_create(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        project = seeded_client.post(
            "/api/v1/projects", headers=headers, json={"reference": "LEN-1", "name": "Longueurs"}
        ).json()
        too_long = "A" * 300
        created = seeded_client.post(
            "/api/v1/projects",
            headers=headers,
            json={"reference": "LEN-2", "name": "Longueurs", "address": too_long},
        )
        updated = seeded_client.patch(
            f"/api/v1/projects/{project['id']}", headers=headers, json={"address": too_long}
        )
        assert created.status_code == 422
        assert updated.status_code == 422, updated.text

    def test_a_blank_name_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        response = seeded_client.post(
            "/api/v1/projects", headers=headers, json={"reference": "LEN-3", "name": "   "}
        )
        assert response.status_code == 422


class TestMarkupPolicyIsValidatedBeforeWriting:
    """§6.6 — une configuration incalculable ne doit jamais être écrite."""

    def test_on_price_with_a_rate_at_or_above_one_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        response = seeded_client.patch(
            "/api/v1/organization/settings",
            headers=headers,
            json={"margin_method": "on_price", "margin_rate": "1.5"},
        )
        assert response.status_code == 422, response.text

    def test_changing_only_the_method_against_an_incompatible_stored_rate_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        """Une modification partielle suffit à rendre le tout incalculable."""
        seeded_client.patch(
            "/api/v1/organization/settings", headers=headers, json={"margin_rate": "1.5"}
        )
        response = seeded_client.patch(
            "/api/v1/organization/settings",
            headers=headers,
            json={"margin_method": "on_price"},
        )
        assert response.status_code == 422, response.text

    def test_after_a_refusal_the_settings_are_unchanged(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        before = seeded_client.get("/api/v1/organization/settings", headers=headers).json()
        seeded_client.patch(
            "/api/v1/organization/settings",
            headers=headers,
            json={"margin_method": "on_price", "margin_rate": "1.5"},
        )
        after = seeded_client.get("/api/v1/organization/settings", headers=headers).json()
        assert after == before, "un refus a laissé des valeurs modifiées"
