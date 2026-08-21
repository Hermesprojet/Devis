"""Les exports refusent proprement et ne fabriquent pas d'arme.

§7 de la revue. Trois familles de défauts :

* `/computation` et `/freeze` rattrapaient `PricingInputError`, les deux
  exports non — une ligne incalculable y produisait un 500 ;
* un tableur interprète une cellule commençant par `=`, `+`, `-` ou `@` comme
  une formule : un libellé de poste devient une commande exécutée à
  l'ouverture, sur le poste du client ;
* la référence projet allait telle quelle dans `Content-Disposition`, où un
  guillemet ou un retour chariot casse l'en-tête.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from metreo_domain import bounds

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _project(client: TestClient, headers: dict[str, str], reference: str, name: str) -> dict:
    return client.post(
        "/api/v1/projects", headers=headers, json={"reference": reference, "name": name}
    ).json()


def _estimate_with(
    client: TestClient, headers: dict[str, str], project: dict, *, designation: str, quantity: str
) -> tuple[str, str]:
    boq = client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=headers, json={"name": "Métré"}
    ).json()
    book = client.post("/api/v1/price-books", headers=headers, json={"name": "Prix"}).json()
    version = client.post(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers, params={"label": "v1"}
    ).json()
    price = client.post(
        f"/api/v1/price-books/versions/{version['id']}/items",
        headers=headers,
        json={
            "code": "X",
            "label": "Prix",
            "unit_code": "m3",
            "unit_price": str(bounds.UNIT_PRICE.maximum),
        },
    ).json()
    client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=headers,
        json={
            "position": "1.1",
            "designation": designation,
            "unit_code": "m3",
            "quantity": quantity,
            "price_item_id": price["id"],
        },
    )
    estimate = client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": project["id"],
            "boq_id": boq["id"],
            "price_book_version_id": version["id"],
            "name": "Export",
        },
    ).json()
    estimate_version = client.post(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers, json={"label": "v1"}
    ).json()
    return estimate["id"], estimate_version["id"]


class TestAnUnpriceableLineNeverYieldsA500:
    @pytest.fixture()
    def overflowing(self, seeded_client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
        project = _project(seeded_client, headers, "EXP-1", "Export refusé")
        return _estimate_with(
            seeded_client,
            headers,
            project,
            designation="Poste démesuré",
            quantity=str(bounds.QUANTITY.maximum),
        )

    @pytest.mark.parametrize("suffix", ["export.csv", "quote.html"])
    def test_the_export_answers_422(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        overflowing: tuple[str, str],
        suffix: str,
    ) -> None:
        estimate_id, version_id = overflowing
        response = seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/{suffix}", headers=headers
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "unpriceable_lines"

    @pytest.mark.parametrize("suffix", ["export.csv", "quote.html"])
    def test_no_export_event_is_journalled_after_a_refusal(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        overflowing: tuple[str, str],
        suffix: str,
    ) -> None:
        """Un export refusé n'a pas eu lieu : le journal ne doit pas l'affirmer."""
        estimate_id, version_id = overflowing
        seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/{suffix}", headers=headers
        )
        events = seeded_client.get(
            f"/api/v1/audit/events?object_id={version_id}", headers=headers
        ).json()["items"]
        assert not [e for e in events if e["action"] == "estimate_version.exported"], events


class TestCsvFormulaInjection:
    """Une cellule de données ne doit jamais devenir une formule."""

    DANGEROUS: ClassVar[list] = [
        pytest.param("=1+1", id="égal"),
        pytest.param("+1+1", id="plus"),
        pytest.param("-1+1", id="moins"),
        pytest.param("@SUM(A1)", id="arobase"),
        pytest.param("\tinjection", id="tabulation"),
        pytest.param("\rinjection", id="retour-chariot"),
        pytest.param('=HYPERLINK("http://exemple.test","clic")', id="hyperlien"),
        pytest.param("=cmd|'/c calc'!A0", id="commande-DDE"),
    ]

    @pytest.mark.parametrize("payload", DANGEROUS)
    def test_a_dangerous_designation_is_neutralised(
        self, seeded_client: TestClient, headers: dict[str, str], payload: str
    ) -> None:
        project = _project(seeded_client, headers, "EXP-2", "Injection")
        estimate_id, version_id = _estimate_with(
            seeded_client, headers, project, designation=payload, quantity="1"
        )
        response = seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/export.csv", headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.content.decode("utf-8-sig")
        # La valeur reste lisible, mais aucune cellule ne commence par un
        # caractère que le tableur interprète.
        for line in body.splitlines():
            for cell in line.split(";"):
                raw = cell.strip('"')
                assert raw[:1] not in ("=", "+", "-", "@", "\t", "\r"), (
                    f"cellule interprétable : {cell!r}"
                )

    def test_a_dangerous_project_reference_is_neutralised(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        project = _project(seeded_client, headers, "=1+1", "Référence piégée")
        estimate_id, version_id = _estimate_with(
            seeded_client, headers, project, designation="Poste", quantity="1"
        )
        body = seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/export.csv", headers=headers
        ).content.decode("utf-8-sig")
        assert "\n=1+1" not in body and not body.startswith("=1+1")

    def test_an_ordinary_negative_amount_stays_readable(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        """La neutralisation ne doit pas rendre les nombres illisibles.

        Un montant négatif commence par « - » : le préfixer aveuglément
        casserait toute réimportation dans un tableur.
        """
        from metreo_api.services.exports import neutralise_cell

        assert neutralise_cell("-1234.56") == "-1234.56"
        assert neutralise_cell("1234.56") == "1234.56"
        assert neutralise_cell("-Poste") != "-Poste"


class TestContentDisposition:
    def test_a_reference_with_a_quote_cannot_break_the_header(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        project = _project(seeded_client, headers, 'A"B;C', "Guillemet")
        estimate_id, version_id = _estimate_with(
            seeded_client, headers, project, designation="Poste", quantity="1"
        )
        response = seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/export.csv", headers=headers
        )
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        # Un seul nom entre guillemets : pas de guillemet non échappé au milieu.
        assert disposition.count('"') == 2, disposition

    def test_a_unicode_reference_falls_back_to_ascii_and_offers_filename_star(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        project = _project(seeded_client, headers, "Réfection-Éghezée", "Accents")
        estimate_id, version_id = _estimate_with(
            seeded_client, headers, project, designation="Poste", quantity="1"
        )
        disposition = seeded_client.get(
            f"/api/v1/estimates/{estimate_id}/versions/{version_id}/export.csv", headers=headers
        ).headers["content-disposition"]
        assert disposition.isascii(), disposition
        assert "filename*=UTF-8''" in disposition, disposition
