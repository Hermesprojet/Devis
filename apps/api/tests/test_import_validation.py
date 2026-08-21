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
WIDE_HEADER = (
    "code,label,unit_code,unit_price,family,region_code,source,indexation,status,confidence\n"
)


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


def _wide(client: TestClient, headers: dict[str, str], version: str, body: str) -> dict:
    response = client.post(
        f"/api/v1/price-books/versions/{version}/imports/preview",
        headers=headers,
        files={"file": ("prix.csv", WIDE_HEADER + body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestLengthsComeFromTheModel:
    """Bloquant B : les limites étaient recopiées, et fausses.

    `family` était contrôlé à 120 pour une colonne `String(60)`, `region_code`
    à 20 pour un `String(10)`. Une ligne passait la prévisualisation puis
    échouait à l'écriture.
    """

    def test_the_declared_limits_match_the_sql_columns(self) -> None:
        from metreo_api.models import PriceItem
        from metreo_api.services.price_contract import sql_length

        for column in (
            "code",
            "label",
            "family",
            "supplier_name",
            "region_code",
            "source",
            "indexation",
        ):
            assert sql_length(column) == PriceItem.__table__.columns[column].type.length

    def test_a_family_past_the_real_column_length_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _wide(seeded_client, headers, version, f"A,Libellé,m3,10,{'F' * 61},BE,,,,\n")
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "family" for e in row["errors"])

    def test_a_region_code_past_the_real_column_length_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _wide(seeded_client, headers, version, f"A,Libellé,m3,10,,{'R' * 11},,,,\n")
        assert _row(report)["is_valid"] is False


class TestEnumeratedColumns:
    def test_an_arbitrary_status_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _wide(seeded_client, headers, version, "A,Libellé,m3,10,,,,,arbitrary,\n")
        row = _row(report)
        assert row["is_valid"] is False, row
        assert any(e["column"] == "status" for e in row["errors"])

    def test_an_arbitrary_confidence_is_refused(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _wide(seeded_client, headers, version, "A,Libellé,m3,10,,,,,,arbitrary\n")
        assert _row(report)["is_valid"] is False

    def test_the_accepted_values_still_pass(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = _wide(seeded_client, headers, version, "A,Libellé,m3,10,,,,,active,quoted\n")
        assert _row(report)["is_valid"] is True, _row(report)


class TestCommitRevalidates:
    def test_a_staging_row_altered_after_preview_is_refused_at_commit(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        """La prévisualisation ne fait pas foi indéfiniment.

        Entre les deux requêtes, la ligne de staging peut être altérée ou une
        contrainte peut avoir changé. L'écriture revalide.
        """
        from sqlalchemy import select, update

        from metreo_api.db import get_session_factory
        from metreo_api.models import ImportBatchRow

        report = _preview(seeded_client, headers, version, "A,Bon,m3,10,\n")
        assert report["valid_count"] == 1
        batch_id = report["batch_id"]

        session = get_session_factory()()
        try:
            row = session.scalars(
                select(ImportBatchRow).where(ImportBatchRow.batch_id == batch_id)
            ).first()
            assert row is not None
            forged = dict(row.normalized)
            forged["label"] = "L" * 300
            session.execute(
                update(ImportBatchRow).where(ImportBatchRow.id == row.id).values(normalized=forged)
            )
            session.commit()
        finally:
            session.close()

        committed = seeded_client.post(
            f"/api/v1/price-books/imports/{batch_id}/commit",
            headers=headers,
            json={"confirm": True},
        )
        assert committed.status_code == 200, committed.text
        body = committed.json()
        assert body["created"] == 0
        assert body["rejected_at_commit"] == 1


class TestTheContractIsShared:
    """Le contrat unique s'applique aux trois chemins d'écriture.

    Avant, chacun appliquait ses propres règles : la saisie manuelle ignorait
    les longueurs SQL, la revalidation du commit ignorait l'unité, la devise,
    le type de ressource et la plage de dates.
    """

    @pytest.mark.parametrize(
        "column,value",
        [
            ("family", "F" * 61),
            ("supplier_name", "S" * 201),
            ("region_code", "R" * 11),
        ],
    )
    def test_manual_entry_refuses_a_value_longer_than_its_column(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        version: str,
        column: str,
        value: str,
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{version}/items",
            headers=headers,
            json={
                "code": "M1",
                "label": "Saisie manuelle",
                "unit_code": "m3",
                "unit_price": "10",
                column: value,
            },
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "field,value",
        [
            ("unit_code", "NOT_A_UNIT"),
            ("currency", "EURO"),
            ("resource_kind", "arbitrary"),
            ("min_quantity", "NaN"),
            ("status", "arbitrary"),
            ("confidence", "arbitrary"),
        ],
    )
    def test_the_commit_revalidation_catches_every_category(self, field: str, value: str) -> None:
        """Chaque catégorie que la revalidation laissait passer."""
        from metreo_api.services.price_import import validate_normalized

        row = {
            "code": "A",
            "label": "Ligne",
            "unit_code": "m3",
            "unit_price": "10",
            "currency": "EUR",
            "resource_kind": "material",
            field: value,
        }
        errors = validate_normalized(row)
        assert errors, f"{field}={value} accepté par la revalidation"
        assert any(e.column == field for e in errors), [e.to_dict() for e in errors]

    def test_the_commit_revalidation_catches_an_inverted_date_range(self) -> None:
        from datetime import date

        from metreo_api.services.price_import import validate_normalized

        errors = validate_normalized(
            {
                "code": "A",
                "label": "Ligne",
                "unit_code": "m3",
                "unit_price": "10",
                "valid_from": date(2026, 12, 31),
                "valid_to": date(2026, 1, 1),
            }
        )
        assert any(e.column == "valid_to" for e in errors), [e.to_dict() for e in errors]

    def test_an_empty_code_or_label_is_refused(self) -> None:
        from metreo_api.services.price_import import validate_normalized

        errors = validate_normalized(
            {"code": "  ", "label": "", "unit_code": "m3", "unit_price": "1"}
        )
        columns = {e.column for e in errors}
        assert {"code", "label"} <= columns, [e.to_dict() for e in errors]

    def test_a_lead_time_beyond_ten_years_is_refused(self) -> None:
        from metreo_api.services.price_import import validate_normalized

        errors = validate_normalized(
            {
                "code": "A",
                "label": "L",
                "unit_code": "m3",
                "unit_price": "1",
                "lead_time_days": "99999",
            }
        )
        assert any(e.column == "lead_time_days" for e in errors)
