"""Les gestes qui mènent une version au point d'être émise.

Partagés entre la suite portable et la suite PostgreSQL : la seconde éprouve
la MÊME route sous concurrence réelle, et recopier la préparation ferait
diverger deux montages censés décrire le même parcours.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

CLIENT_COMPLET = {
    "name": "Commune de Perwez",
    "company_number": "BE 0207.363.192",
    "billing_address": "Rue Émile de Brabant 2",
    "postal_code": "1360",
    "city": "Perwez",
    "contact_name": "Service Travaux",
    "email": "travaux@perwez.example",
}


def prix_manquant(client: TestClient, headers: dict[str, str], estimate: dict) -> None:
    """Le jeu de démonstration laisse une ligne sans prix, exprès. On la chiffre."""
    livre = client.get("/api/v1/price-books", headers=headers).json()[0]
    version_id = client.get(f"/api/v1/price-books/{livre['id']}/versions", headers=headers).json()[
        0
    ]["id"]
    cree = client.post(
        f"/api/v1/price-books/versions/{version_id}/items",
        headers=headers,
        json={
            "code": "RAC-PART-001",
            "label": "Mise en conformité d'un raccordement particulier",
            "unit_code": "pce",
            "unit_price": "480.00",
            "resource_kind": "subcontract",
        },
    )
    assert cree.status_code == 201, cree.text
    postes = client.get(f"/api/v1/boqs/{estimate['boq_id']}/items", headers=headers).json()
    orphelin = next(
        p
        for p in postes
        if p["price_item_id"] is None and p["composite_price_id"] is None and p["kind"] != "section"
    )
    rattache = client.patch(
        f"/api/v1/boq-items/{orphelin['id']}",
        headers=headers,
        json={"price_item_id": cree.json()["id"]},
    )
    assert rattache.status_code == 200, rattache.text


def fiche(client: TestClient, headers: dict[str, str], **remplacements: Any) -> dict:
    reponse = client.post(
        "/api/v1/clients", headers=headers, json={**CLIENT_COMPLET, **remplacements}
    )
    assert reponse.status_code == 201, reponse.text
    resultat: dict = reponse.json()
    return resultat


def rattacher(client: TestClient, headers: dict[str, str], projet_id: str, fiche_id: str) -> None:
    reponse = client.patch(
        f"/api/v1/projects/{projet_id}", headers=headers, json={"client_id": fiche_id}
    )
    assert reponse.status_code == 200, reponse.text


def geler(client: TestClient, headers: dict[str, str], estimate: dict, version: dict) -> None:
    reponse = client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert reponse.status_code == 200, reponse.text


def version_de_plus(
    client: TestClient, headers: dict[str, str], estimate: dict, etiquette: str
) -> dict:
    reponse = client.post(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers, json={"label": etiquette}
    )
    assert reponse.status_code == 201, reponse.text
    resultat: dict = reponse.json()
    return resultat


def emettre(
    client: TestClient, headers: dict[str, str], estimate: dict, version: dict, **corps: Any
) -> Any:
    return client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/issue",
        headers=headers,
        json=corps,
    )
