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

from typing import ClassVar

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


class TestTheDebtIsNamedAndBounded:
    """Neuf relations sont tenues par la base ; les autres restent une dette.

    Ce contrôle affirmait, jusqu'à ce travail, qu'AUCUNE table de la Phase 1 ne
    portait de clé composite — il devait tomber le jour où l'une en recevrait
    une, et c'est ce qui s'est produit. Il dit maintenant l'inverse et borne ce
    qui reste : les tables couvertes doivent l'être, et toute nouvelle
    couverture doit être déclarée ici plutôt que d'arriver en silence.
    """

    #: Tables de la Phase 1 dont au moins une relation est tenue par la base.
    #:
    #: Seconde tranche : `CompositePriceRow`, `EstimateVersion` et `ImportBatch`
    #: rejoignent la liste. Les seize relations couvertes le sont désormais
    #: toutes ; les sept qui restent hors de cette liste pointent `users`, et
    #: font l'objet d'une note de décision séparée — elles ne recevront pas de
    #: clé composite vers `users`, qui n'a pas d'organisation.
    COVERED: ClassVar[set[str]] = {
        "BillOfQuantities",
        "BoqItem",
        "CompositeComponentRow",
        "CompositePriceRow",
        "Estimate",
        "EstimateVersion",
        "ImportBatch",
        "PriceBookVersion",
        "PriceItem",
    }

    #: Phase 2A, hors périmètre de ce travail.
    PHASE_TWO: ClassVar[set[str]] = {
        "Document",
        "DocumentRevision",
        "DocumentStepRun",
        "SourceCitation",
        "ExtractionProposal",
        "ValidationDecision",
    }

    def _tables_with_composite_keys(self) -> set[str]:
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "models.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        return {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(inner, ast.Call)
                and getattr(inner.func, "id", "") == "ForeignKeyConstraint"
                for inner in ast.walk(node)
            )
        }

    def test_the_covered_tables_are_still_covered(self) -> None:
        found = self._tables_with_composite_keys()
        assert found >= self.COVERED, (
            f"des tables ont perdu leur clé composite : {sorted(self.COVERED - found)}"
        )

    def test_no_table_gains_a_composite_key_without_being_declared(self) -> None:
        found = self._tables_with_composite_keys()
        undeclared = found - self.COVERED - self.PHASE_TWO
        assert undeclared == set(), (
            f"tables portant une clé composite non déclarée ici : {sorted(undeclared)} — "
            "la couverture doit être tenue à jour, pas découverte après coup"
        )


class TestTheSortIndexNeverReadsAnotherTenant:
    """`create_item` calculait l'index de tri sans filtrer l'organisation.

    `select(BoqItem.sort_index).where(BoqItem.boq_id == boq_id)` : le bordereau
    était bien possédé — `get_owned` juste avant — mais la lecture des index de
    tri, elle, ne l'était pas. Tant que la base ne tenait pas la cohérence entre
    une ligne et son bordereau, une ligne d'une autre organisation portant le
    même `boq_id` décalait l'index calculé.

    La contrainte composite ferme désormais ce chemin en base. Le filtre reste :
    une défense applicative ne coûte rien et ne dépend pas de la migration.
    """

    def test_the_next_index_is_computed_from_the_callers_rows_only(
        self, seeded_client: TestClient, alpha: dict[str, str]
    ) -> None:
        first = seeded_client.post(
            f"/api/v1/boqs/{alpha['boq']}/items",
            headers=alpha["h"],
            json={"position": "7.1", "designation": "un", "unit_code": "m3", "quantity": "1"},
        )
        assert first.status_code == 201, first.text
        second = seeded_client.post(
            f"/api/v1/boqs/{alpha['boq']}/items",
            headers=alpha["h"],
            json={"position": "7.2", "designation": "deux", "unit_code": "m3", "quantity": "1"},
        )
        assert second.status_code == 201, second.text
        assert second.json()["sort_index"] == first.json()["sort_index"] + 10

    def test_both_queries_filter_the_organisation(self) -> None:
        """Retirer le filtre rend ce contrôle rouge.

        Statique et non contournable par un jeu de données : c'est la lecture
        elle-même qui doit porter la restriction, qu'il existe ou non une ligne
        étrangère au moment du test.
        """
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "routers" / "boq.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        unfiltered = []
        for node in ast.walk(tree):
            # L'INSTRUCTION entière, pas l'appel `select(...)` isolé : le filtre
            # vit dans le `.where(...)` chaîné, qui n'est pas un enfant du
            # `select`. Lire l'appel seul déclarait fautif un code correct.
            if not isinstance(node, ast.stmt):
                continue
            rendered = ast.unparse(node)
            if "select(BoqItem.sort_index)" not in rendered:
                continue
            if "BoqItem.organization_id" not in rendered:
                unfiltered.append(f"ligne {node.lineno}: {rendered[:90]}")
        assert unfiltered == [], (
            f"lectures d'index de tri sans filtre d'organisation : {unfiltered}"
        )
