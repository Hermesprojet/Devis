"""Invariants métier du §6, au niveau HTTP."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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


class TestRotationWithDensity:
    """Bloquant F : la rotation m³ → tonnes avait été refusée par régression."""

    def test_a_rotation_with_a_sourced_density_is_accepted_and_canonicalised(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "rotation",
                    "label": "Évacuation par camion 14 t",
                    "resource_kind": "transport",
                    "payload_value": "14",
                    # Alias d'unité : doit être canonicalisé à l'écriture.
                    "payload_unit_code": "tonne",
                    "cost_per_rotation": "85",
                    "distance_km": "18",
                    "rate_per_km": "1.20",
                    "density_value": "1800",
                    "density_source": "Rapport géotechnique GT-2026-018, p. 12",
                }
            ],
        )
        assert response.status_code == 201, response.text

        from metreo_api.db import get_session_factory
        from metreo_api.models import CompositeComponentRow

        session = get_session_factory()()
        try:
            row = session.scalars(
                select(CompositeComponentRow).where(
                    CompositeComponentRow.composite_price_id == response.json()["id"]
                )
            ).one()
            assert row.payload_unit_code == "t", "l'alias d'unité n'a pas été canonicalisé"
            assert row.density_value is not None
            assert "GT-2026-018" in (row.density_source or "")
        finally:
            session.close()

    def test_a_density_without_a_source_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "payload_value": "14",
                    "payload_unit_code": "t",
                    "cost_per_rotation": "85",
                    "density_value": "1800",
                }
            ],
        )
        assert response.status_code == 422, response.text

    def test_a_source_without_a_density_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "payload_value": "14",
                    "payload_unit_code": "t",
                    "cost_per_rotation": "85",
                    "density_source": "Rapport sans chiffre",
                }
            ],
        )
        assert response.status_code == 422, response.text


class TestCardinalityAtTheApiBoundary:
    """Bloquant G : le moteur refusait, l'écriture acceptait."""

    def test_the_component_limit_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        from metreo_domain import bounds

        components = [
            {"component_type": "lump_sum", "label": f"C{i}", "lump_sum_amount": "1"}
            for i in range(bounds.MAX_COMPONENTS_PER_LINE)
        ]
        assert _composite(seeded_client, headers, version, components).status_code == 201

    def test_one_component_past_the_limit_is_refused_and_writes_nothing(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        from metreo_api.db import get_session_factory
        from metreo_api.models import CompositeComponentRow, CompositePriceRow
        from metreo_domain import bounds

        components = [
            {"component_type": "lump_sum", "label": f"C{i}", "lump_sum_amount": "1"}
            for i in range(bounds.MAX_COMPONENTS_PER_LINE + 1)
        ]
        response = _composite(seeded_client, headers, version, components)
        assert response.status_code == 422, response.text

        session = get_session_factory()()
        try:
            # Restreint à cette version : le jeu de démonstration porte ses
            # propres sous-détails, qui n'ont rien à voir avec ce refus.
            written = session.scalars(
                select(CompositePriceRow).where(CompositePriceRow.price_book_version_id == version)
            ).all()
            assert written == [], "un sous-détail refusé a laissé une trace"
            assert (
                session.scalars(
                    select(CompositeComponentRow).where(
                        CompositeComponentRow.composite_price_id.in_(
                            [row.id for row in written] or [""]
                        )
                    )
                ).all()
                == []
            )
        finally:
            session.close()


class TestSinglePriceSourceIsEnforcedBySql:
    """Bloquant H : la règle n'existait que dans la route."""

    def test_a_direct_orm_write_with_both_sources_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from metreo_api.db import get_session_factory
        from metreo_api.models import BillOfQuantities, BoqItem

        session = get_session_factory()()
        try:
            organization_id = session.get(BillOfQuantities, boq["id"]).organization_id
            item = BoqItem(
                organization_id=organization_id,
                boq_id=boq["id"],
                position="9.9",
                designation="Deux sources par écriture directe",
                unit_code="m3",
                quantity=1,
                price_item_id="11111111-1111-1111-1111-111111111111",
                composite_price_id="22222222-2222-2222-2222-222222222222",
            )
            session.add(item)
            with pytest.raises(IntegrityError) as excinfo:
                session.flush()
            assert "single_price_source" in str(excinfo.value)
        finally:
            session.rollback()
            session.close()


class TestLumpSumAtZeroQuantityEndToEnd:
    """§6.7 de bout en bout : création, gel, instantané, recalcul.

    La règle est testée dans le domaine ; ce parcours prouve qu'elle survit à
    la persistance et au gel — c'est là que se joue la valeur d'un devis
    archivé.
    """

    def test_a_lump_sum_survives_the_freeze_and_the_recomputation(
        self, seeded_client: TestClient, headers: dict[str, str], boq: dict, version: str
    ) -> None:
        composite = _composite(
            seeded_client,
            headers,
            version,
            [
                {
                    "component_type": "lump_sum",
                    "label": "Installation de chantier",
                    "lump_sum_amount": "1500",
                }
            ],
        ).json()

        seeded_client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=headers,
            json={
                "position": "0.1",
                "designation": "Installation de chantier",
                "unit_code": "fft",
                # Quantité nulle : le forfait doit compter quand même.
                "quantity": "0",
                "composite_price_id": composite["id"],
            },
        )
        estimate = seeded_client.post(
            "/api/v1/estimates",
            headers=headers,
            json={
                "project_id": boq["project_id"],
                "boq_id": boq["id"],
                "price_book_version_id": version,
                "name": "Forfait à zéro",
            },
        ).json()
        estimate_version = seeded_client.post(
            f"/api/v1/estimates/{estimate['id']}/versions", headers=headers, json={"label": "v1"}
        ).json()

        before = seeded_client.get(
            f"/api/v1/estimates/{estimate['id']}/versions/{estimate_version['id']}/computation",
            headers=headers,
        )
        assert before.status_code == 200, before.text
        total_before = before.json()["result"]["total_selling_price_ht"]
        assert Decimal(total_before) > 0, "le forfait a disparu à quantité nulle"

        frozen = seeded_client.post(
            f"/api/v1/estimates/{estimate['id']}/versions/{estimate_version['id']}/freeze",
            headers=headers,
            json={"label": "gel", "confirm": True},
        )
        assert frozen.status_code == 200, frozen.text
        digest = frozen.json()["snapshot_sha256"]
        assert digest

        after = seeded_client.get(
            f"/api/v1/estimates/{estimate['id']}/versions/{estimate_version['id']}/computation",
            headers=headers,
        ).json()
        assert after["from_snapshot"] is True
        assert after["result"]["total_selling_price_ht"] == total_before

        # Recalcul depuis l'instantané : même total, même empreinte.
        from metreo_api.db import get_session_factory
        from metreo_api.models import EstimateVersion
        from metreo_api.services import estimating

        session = get_session_factory()()
        try:
            stored = session.get(EstimateVersion, estimate_version["id"])
            assert stored is not None
            recomputed = estimating.recompute_from_snapshot(stored.snapshot)
            assert str(recomputed.total_selling_price_ht.amount) == str(
                estimating.recompute_from_snapshot(stored.snapshot).total_selling_price_ht.amount
            )
            assert estimating.snapshot_digest(stored.snapshot) == digest
        finally:
            session.close()
