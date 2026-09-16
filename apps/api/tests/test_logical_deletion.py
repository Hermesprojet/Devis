"""Une suppression logique fait vraiment disparaître la ligne des lectures.

`DELETE /projects/{project_id}` ne supprime pas la ligne : il pose `deleted_at`.
Ce qui la fait disparaître ensuite, c'est le filtre `deleted_at IS NULL` de
`owned_query`, unique et partagé par tous les modèles qui portent la colonne —
`Organization`, `Project`, `Document`, `PriceBook`, `BillOfQuantities`,
`Estimate`.

Mesuré, sur `main` : en neutralisant ce filtre, la suite complète reste verte
(API SQLite, domaine, contrats). La seule suppression de projet qu'elle
contenait était `test_tenant_isolation.py::test_another_tenant_cannot_delete_a_project`,
qui vérifie un refus — aucun test ne supprimait pour de bon. Un projet supprimé
serait donc réapparu dans toutes les listes sans que rien ne le signale.

Le dernier test ci-dessous distingue la suppression logique de la suppression
physique : sans lui, la disparition serait aussi bien expliquée par un `DELETE`
SQL, et la garantie de conservation — nécessaire à la piste d'audit — ne serait
pas vérifiée.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def projet_supprime(seeded_client: TestClient, headers: dict[str, str]) -> str:
    """Un projet créé puis supprimé logiquement, dont on rend l'identifiant."""
    cree = seeded_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"reference": "SUPPR-001", "name": "Projet à supprimer"},
    )
    assert cree.status_code == 201, cree.text
    identifiant = cree.json()["id"]

    avant = seeded_client.get("/api/v1/projects", headers=headers).json()
    assert identifiant in {p["id"] for p in avant["items"]}, (
        "le projet doit être visible avant la suppression, sinon les tests "
        "suivants passeraient sans rien prouver"
    )

    efface = seeded_client.delete(f"/api/v1/projects/{identifiant}", headers=headers)
    assert efface.status_code == 204, efface.text
    return identifiant


def test_a_deleted_project_leaves_the_listing(
    seeded_client: TestClient, headers: dict[str, str], projet_supprime: str
) -> None:
    apres = seeded_client.get("/api/v1/projects", headers=headers).json()
    assert projet_supprime not in {p["id"] for p in apres["items"]}


def test_a_deleted_project_reads_as_unknown(
    seeded_client: TestClient, headers: dict[str, str], projet_supprime: str
) -> None:
    lecture = seeded_client.get(f"/api/v1/projects/{projet_supprime}", headers=headers)
    assert lecture.status_code == 404
    assert lecture.json()["detail"]["code"] == "not_found"


def test_a_deleted_project_cannot_be_modified_or_deleted_again(
    seeded_client: TestClient, headers: dict[str, str], projet_supprime: str
) -> None:
    modification = seeded_client.patch(
        f"/api/v1/projects/{projet_supprime}", headers=headers, json={"name": "Ressuscité"}
    )
    assert modification.status_code == 404
    assert (
        seeded_client.delete(f"/api/v1/projects/{projet_supprime}", headers=headers).status_code
        == 404
    )


def test_nothing_hangs_off_a_deleted_project_any_more(
    seeded_client: TestClient, headers: dict[str, str], projet_supprime: str
) -> None:
    """Les routes filles répondent comme si le parent n'existait pas."""
    for chemin in ("boqs", "documents"):
        reponse = seeded_client.get(f"/api/v1/projects/{projet_supprime}/{chemin}", headers=headers)
        assert reponse.status_code == 404, chemin


def test_the_row_is_kept_with_its_deletion_date(
    seeded_client: TestClient, headers: dict[str, str], projet_supprime: str
) -> None:
    """Logique, pas physique : la ligne reste, datée, pour la piste d'audit."""
    from metreo_api.db import get_session_factory
    from metreo_api.models import Project

    session = get_session_factory()()
    try:
        ligne = session.get(Project, projet_supprime)
        assert ligne is not None, "la suppression logique ne doit pas effacer la ligne"
        assert ligne.deleted_at is not None, "la date de suppression doit être posée"
    finally:
        session.close()
