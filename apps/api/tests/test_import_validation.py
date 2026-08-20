"""L'import CSV applique exactement les mêmes règles que la saisie manuelle.

Régression P0-3 de la revue indépendante : le CSV contournait `PriceItemCreate`
et les bornes. Un prix de 1e20 et un `Infinity` étaient déclarés valides, un
`NaN` levait une exception, et un délai fractionnaire était tronqué en silence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login

HEADER = "code,label,unit_code,unit_price,lead_time_days\n"


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.post(
        "/api/v1/price-books", headers=headers, json={"name": "Import"}
    ).json()
    return seeded_client.post(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers, params={"label": "v1"}
    ).json()["id"]


def _preview(client: TestClient, headers: dict[str, str], version: str, body: str) -> dict:
    response = client.post(
        f"/api/v1/price-books/versions/{version}/imports/preview",
        headers=headers,
        files={"file": ("prix.csv", HEADER + body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(report: dict, line: int = 2) -> dict:
    return next(r for r in report["rows"] if r["line_number"] == line)


class TestNumericValues:
    def test_a_price_past_the_bound_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _preview(
            seeded_client, headers, version, "A,Trop cher,m3,100000000000000000000,\n"
        )
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "unit_price" for e in row["errors"])

    @pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
    def test_a_non_finite_price_is_refused_as_a_row_error(
        self, seeded_client: TestClient, headers: dict[str, str], version: str, literal: str
    ) -> None:
        """Jamais une exception globale : une ligne fautive reste une ligne."""
        report = _preview(seeded_client, headers, version, f"A,Non fini,m3,{literal},\n")
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "unit_price" for e in row["errors"])

    def test_the_maximum_price_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _preview(seeded_client, headers, version, "A,Au maximum,m3,1000000000,\n")
        assert _row(report)["is_valid"] is True, _row(report)


class TestLeadTime:
    def test_a_fractional_lead_time_is_refused_rather_than_truncated(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        """1,5 jour tronqué en 1 jour est une donnée fausse, pas un arrondi."""
        report = _preview(seeded_client, headers, version, "A,Délai,m3,10,1.5\n")
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "lead_time_days" for e in row["errors"])

    def test_a_negative_lead_time_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _preview(seeded_client, headers, version, "A,Délai,m3,10,-3\n")
        assert _row(report)["is_valid"] is False


class TestFieldLengths:
    def test_a_code_longer_than_the_column_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        """La saisie manuelle refuse au-delà de 60 : l'import doit faire pareil."""
        report = _preview(seeded_client, headers, version, f"{'X' * 61},Long,m3,10,\n")
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "code" for e in row["errors"])

    def test_a_label_longer_than_the_column_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _preview(seeded_client, headers, version, f"A,{'L' * 256},m3,10,\n")
        assert _row(report)["is_valid"] is False


class TestNothingIsWrittenBeforeConfirmation:
    def test_an_invalid_row_never_reaches_the_database(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _preview(seeded_client, headers, version, "A,Bon,m3,10,\nB,Mauvais,m3,1e30,\n")
        assert report["valid_count"] == 1
        assert report["error_count"] == 1
        listed = seeded_client.get(
            f"/api/v1/price-books/versions/{version}/items", headers=headers
        ).json()
        assert listed == [] or listed.get("items") == []
