"""Les trois parcours d'écriture d'un prix décident et normalisent pareil.

Bloquant D : `validate_price_row()` n'était appelé que par l'import, la saisie
manuelle validait seulement l'unité, et la prévisualisation gardait son
validateur parallèle. Une prévisualisation pouvait donc annoncer valide une
ligne que la confirmation rejetterait, et les valeurs écrites n'étaient pas
celles que la validation avait normalisées.

Chaque cas est joué sur les trois chemins. Les tests d'écriture contrôlent la
valeur **relue en base**, pas seulement la réponse du validateur.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from .conftest import login

HEADER = (
    "code,label,unit_code,unit_price,currency,resource_kind,family,region_code,"
    "status,confidence,lead_time_days,valid_from,valid_to\n"
)

#: Une valeur refusée doit l'être partout, avec la même colonne fautive.
REFUSED = [
    pytest.param({"code": "   "}, "code", id="code-blanc"),
    pytest.param({"label": "   "}, "label", id="libellé-blanc"),
    pytest.param({"currency": "EU1"}, "currency", id="devise-invalide"),
    pytest.param({"currency": "EURO"}, "currency", id="devise-trop-longue"),
    pytest.param({"unit_code": "NOT_A_UNIT"}, "unit_code", id="unité-inconnue"),
    pytest.param({"resource_kind": "arbitrary"}, "resource_kind", id="type-arbitraire"),
    pytest.param({"status": "arbitrary"}, "status", id="statut-arbitraire"),
    pytest.param({"confidence": "arbitrary"}, "confidence", id="confiance-arbitraire"),
    pytest.param({"lead_time_days": "3651"}, "lead_time_days", id="délai-au-delà-de-dix-ans"),
    pytest.param({"lead_time_days": "1.5"}, "lead_time_days", id="délai-fractionnaire"),
    pytest.param({"unit_price": "NaN"}, "unit_price", id="prix-NaN"),
    pytest.param({"unit_price": "Infinity"}, "unit_price", id="prix-infini"),
    pytest.param({"unit_price": "1e30"}, "unit_price", id="prix-hors-borne"),
    pytest.param({"family": "F" * 61}, "family", id="famille-trop-longue"),
    pytest.param({"region_code": "R" * 11}, "region_code", id="région-trop-longue"),
    pytest.param(
        {"valid_from": "2026-12-31", "valid_to": "2026-01-01"}, "valid_to", id="dates-inversées"
    ),
    pytest.param({"valid_from": "pas-une-date"}, "valid_from", id="date-illisible"),
]

BASE: dict[str, Any] = {
    "code": "P1",
    "label": "Poste de référence",
    "unit_code": "m3",
    "unit_price": "10",
    "currency": "EUR",
    "resource_kind": "material",
}


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.post(
        "/api/v1/price-books", headers=headers, json={"name": "Matrice"}
    ).json()
    return seeded_client.post(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers, params={"label": "v1"}
    ).json()["id"]


def _csv_line(overrides: dict[str, Any]) -> bytes:
    row = {**BASE, **overrides}
    order = [
        "code",
        "label",
        "unit_code",
        "unit_price",
        "currency",
        "resource_kind",
        "family",
        "region_code",
        "status",
        "confidence",
        "lead_time_days",
        "valid_from",
        "valid_to",
    ]
    return (HEADER + ",".join(str(row.get(k, "")) for k in order) + "\n").encode()


class TestTheSameDecisionOnAllThreePaths:
    @pytest.mark.parametrize("overrides,column", REFUSED)
    def test_manual_entry_refuses(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        version: str,
        overrides: dict[str, Any],
        column: str,
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{version}/items",
            headers=headers,
            json={**BASE, **overrides},
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("overrides,column", REFUSED)
    def test_the_csv_preview_refuses_the_same_row(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        version: str,
        overrides: dict[str, Any],
        column: str,
    ) -> None:
        report = seeded_client.post(
            f"/api/v1/price-books/versions/{version}/imports/preview",
            headers=headers,
            files={"file": ("prix.csv", _csv_line(overrides), "text/csv")},
        ).json()
        row = report["rows"][0]
        assert row["is_valid"] is False, row
        assert any(e["column"] == column for e in row["errors"]), row["errors"]

    @pytest.mark.parametrize("overrides,column", REFUSED)
    def test_the_commit_revalidation_refuses_the_same_row(
        self, overrides: dict[str, Any], column: str
    ) -> None:
        """Le staging peut être altéré entre prévisualisation et écriture."""
        from metreo_api.services.price_import import validate_normalized

        errors = validate_normalized({**BASE, **overrides})
        assert errors, f"{overrides} accepté à la confirmation"
        assert any(e.column == column for e in errors), [e.to_dict() for e in errors]


class TestTheSameNormalisationReachesTheDatabase:
    """Ce qui est écrit est ce que le contrat a normalisé, pas la saisie brute."""

    @staticmethod
    def _stored(version_id: str) -> Any:
        from metreo_api.db import get_session_factory
        from metreo_api.models import PriceItem

        session = get_session_factory()()
        try:
            return session.scalars(
                select(PriceItem).where(PriceItem.price_book_version_id == version_id)
            ).all()
        finally:
            session.close()

    def test_manual_entry_stores_the_canonical_unit_and_currency(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{version}/items",
            headers=headers,
            json={**BASE, "code": " P3 ", "label": " Prix ", "unit_code": "m³", "currency": "eur"},
        )
        assert response.status_code == 201, response.text
        stored = self._stored(version)[0]
        assert stored.code == "P3", "le code n'a pas été dépouillé"
        assert stored.label == "Prix"
        assert stored.unit_code == "m3", "l'alias d'unité n'a pas été canonicalisé"
        assert stored.currency == "EUR"

    def test_an_imported_row_stores_the_same_canonical_values(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        report = seeded_client.post(
            f"/api/v1/price-books/versions/{version}/imports/preview",
            headers=headers,
            files={
                "file": (
                    "prix.csv",
                    _csv_line(
                        {"code": " P3 ", "label": " Prix ", "unit_code": "m³", "currency": "eur"}
                    ),
                    "text/csv",
                )
            },
        ).json()
        assert report["valid_count"] == 1, report["rows"]

        committed = seeded_client.post(
            f"/api/v1/price-books/imports/{report['batch_id']}/commit",
            headers=headers,
            json={"confirm": True},
        )
        assert committed.status_code == 200, committed.text
        assert committed.json()["created"] == 1

        stored = self._stored(version)[0]
        assert stored.code == "P3"
        assert stored.label == "Prix"
        assert stored.unit_code == "m3"
        assert stored.currency == "EUR"


class TestTheSqlConstraintsAreTheLastResort:
    """Ce qui ne passe pas par le contrat doit quand même être refusé.

    Scripts d'exploitation, migrations futures, correctifs à la main : la
    contrainte SQL ne sait pas dire pourquoi, mais elle rend impossible une
    écriture qu'aucun humain n'a voulue.
    """

    @pytest.mark.parametrize(
        "column,value,constraint",
        [
            ("resource_kind", "arbitrary", "resource_kind"),
            ("status", "arbitrary", "status"),
            ("confidence", "arbitrary", "confidence"),
            ("lead_time_days", 4000, "lead_time"),
            ("lead_time_days", -1, "lead_time"),
        ],
    )
    def test_a_direct_write_bypassing_the_contract_is_refused_by_sql(
        self,
        seeded_client: TestClient,
        headers: dict[str, str],
        version: str,
        column: str,
        value: Any,
        constraint: str,
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        from metreo_api.db import get_session_factory
        from metreo_api.models import PriceBookVersion, PriceItem

        session = get_session_factory()()
        try:
            item = PriceItem(
                organization_id=session.get(PriceBookVersion, version).organization_id,
                price_book_version_id=version,
                code="SQL1",
                label="Écriture directe",
                unit_code="m3",
                unit_price=1,
                **{column: value},
            )
            session.add(item)
            with pytest.raises(IntegrityError) as excinfo:
                session.flush()
            assert constraint in str(excinfo.value)
        finally:
            session.rollback()
            session.close()

    def test_an_inverted_date_range_is_refused_by_sql(
        self, seeded_client: TestClient, headers: dict[str, str], version: str
    ) -> None:
        from datetime import date

        from sqlalchemy.exc import IntegrityError

        from metreo_api.db import get_session_factory
        from metreo_api.models import PriceBookVersion, PriceItem

        session = get_session_factory()()
        try:
            item = PriceItem(
                organization_id=session.get(PriceBookVersion, version).organization_id,
                price_book_version_id=version,
                code="SQL2",
                label="Dates inversées",
                unit_code="m3",
                unit_price=1,
                valid_from=date(2026, 12, 31),
                valid_to=date(2026, 1, 1),
            )
            session.add(item)
            with pytest.raises(IntegrityError) as excinfo:
                session.flush()
            assert "validity_range" in str(excinfo.value)
        finally:
            session.rollback()
            session.close()
