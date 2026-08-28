"""Aucune route ne doit relier une ligne d'un tenant à la ressource d'un autre.

Le modèle Phase 1 ne porte **aucune clé étrangère composite** : 39 clés
simples, zéro `ForeignKeyConstraint`. Mesuré sur PostgreSQL 16, dans une base
créée pour l'expérience : neuf relations croisées entre deux organisations sont
acceptées par des `INSERT` directs — poste pointant le prix d'un autre tenant,
bordereau rattaché au projet d'un autre, estimation figée sur la bibliothèque
d'un autre, et ainsi de suite. La base ne dit rien.

Ce qui les empêche est donc entièrement applicatif : chaque route valide son
parent par `get_owned`, qui filtre l'organisation et répond 404. Ce fichier
tient cette frontière — celle qui est réellement exposée — pendant que la
contrainte SQL reste une dette identifiée et planifiée.

Les tests ci-dessous ne prouvent pas l'absence de chemin : ils prouvent que les
six chemins les plus dangereux sont fermés, et ils tombent si l'un se rouvre.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login

#: Les codes qui signifient « la route a refusé ». 404 est la réponse attendue
#: pour une ressource d'un autre tenant : 403 confirmerait qu'elle existe.
REFUSALS = (403, 404, 422)


def _own(client: TestClient, headers: dict[str, str], tag: str) -> dict[str, str]:
    """Un jeu complet de ressources appartenant à cette organisation."""
    project = client.post(
        "/api/v1/projects", headers=headers, json={"reference": f"P-{tag}", "name": f"Projet {tag}"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    boq = client.post(
        f"/api/v1/projects/{project_id}/boqs", headers=headers, json={"name": f"Métré {tag}"}
    )
    assert boq.status_code == 201, boq.text

    book = client.post("/api/v1/price-books", headers=headers, json={"name": f"Bibliothèque {tag}"})
    assert book.status_code == 201, book.text
    version = client.post(
        f"/api/v1/price-books/{book.json()['id']}/versions", headers=headers, json={}
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]

    item = client.post(
        f"/api/v1/price-books/versions/{version_id}/items",
        headers=headers,
        json={
            "code": f"C-{tag}",
            "label": f"Poste {tag}",
            "unit_code": "m3",
            "currency": "EUR",
            "unit_price": "10.00",
            "resource_kind": "material",
            "confidence": "declared",
        },
    )
    assert item.status_code == 201, item.text

    return {
        "project": project_id,
        "boq": boq.json()["id"],
        "version": version_id,
        "price_item": item.json()["id"],
    }


@pytest.fixture()
def alpha(seeded_client: TestClient) -> dict[str, str]:
    headers = login(seeded_client, "admin@dubois.demo")
    return {"h": headers, **_own(seeded_client, headers, "A")}


@pytest.fixture()
def beta(seeded_client: TestClient) -> dict[str, str]:
    headers = login(seeded_client, "admin@janssens.demo")
    return {"h": headers, **_own(seeded_client, headers, "B")}


class TestNoRouteCreatesACrossTenantLink:
    """Les six croisements que le SQL accepte, refusés par les routes."""

    def test_a_row_cannot_point_at_another_tenants_price(self, seeded_client, alpha, beta):
        """« l'estimation de A valorisée avec les prix de B »."""
        response = seeded_client.post(
            f"/api/v1/boqs/{alpha['boq']}/items",
            headers=alpha["h"],
            json={
                "position": "9.1",
                "designation": "croisé",
                "unit_code": "m3",
                "quantity": "1",
                "price_item_id": beta["price_item"],
            },
        )
        assert response.status_code in REFUSALS, response.text

    def test_a_row_cannot_be_added_to_another_tenants_bill(self, seeded_client, alpha, beta):
        response = seeded_client.post(
            f"/api/v1/boqs/{beta['boq']}/items",
            headers=alpha["h"],
            json={"position": "9.2", "designation": "croisé", "unit_code": "m3", "quantity": "1"},
        )
        assert response.status_code in REFUSALS, response.text

    def test_a_bill_cannot_be_attached_to_another_tenants_project(self, seeded_client, alpha, beta):
        response = seeded_client.post(
            f"/api/v1/projects/{beta['project']}/boqs", headers=alpha["h"], json={"name": "croisé"}
        )
        assert response.status_code in REFUSALS, response.text

    def test_an_estimate_cannot_use_another_tenants_bill(self, seeded_client, alpha, beta):
        response = seeded_client.post(
            "/api/v1/estimates",
            headers=alpha["h"],
            json={
                "project_id": alpha["project"],
                "boq_id": beta["boq"],
                "price_book_version_id": alpha["version"],
                "name": "croisé",
            },
        )
        assert response.status_code in REFUSALS, response.text

    def test_an_estimate_cannot_freeze_on_another_tenants_library(self, seeded_client, alpha, beta):
        """« le devis de A se fige sur les tarifs de B » — le pire des six."""
        response = seeded_client.post(
            "/api/v1/estimates",
            headers=alpha["h"],
            json={
                "project_id": alpha["project"],
                "boq_id": alpha["boq"],
                "price_book_version_id": beta["version"],
                "name": "croisé",
            },
        )
        assert response.status_code in REFUSALS, response.text

    def test_a_price_cannot_be_written_into_another_tenants_version(
        self, seeded_client, alpha, beta
    ):
        response = seeded_client.post(
            f"/api/v1/price-books/versions/{beta['version']}/items",
            headers=alpha["h"],
            json={
                "code": "CROISE",
                "label": "croisé",
                "unit_code": "m3",
                "currency": "EUR",
                "unit_price": "1.00",
                "resource_kind": "material",
                "confidence": "declared",
            },
        )
        assert response.status_code in REFUSALS, response.text


class TestTheDatabaseItselfOffersNoProtection:
    """La dette est nommée, pas oubliée.

    Ce contrôle échoue le jour où des clés composites sont posées sur les
    tables de la Phase 1 — et c'est le signal attendu pour retirer la dette du
    dossier, pas un test à contourner.
    """

    def test_phase_one_tables_still_carry_no_composite_foreign_key(self) -> None:
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "models.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        phase_two = {
            "Document",
            "DocumentRevision",
            "DocumentStepRun",
            "SourceCitation",
            "ExtractionProposal",
            "ValidationDecision",
        }
        protected = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(inner, ast.Call)
                and getattr(inner.func, "id", "") == "ForeignKeyConstraint"
                for inner in ast.walk(node)
            )
        }
        assert protected <= phase_two, (
            f"des tables de la Phase 1 portent désormais une clé composite : "
            f"{sorted(protected - phase_two)} — la frontière n'est plus seulement "
            "applicative, et le dossier doit être mis à jour"
        )
