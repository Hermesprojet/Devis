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


def graphe_complet(
    client: TestClient, headers: dict[str, str], reference: str, **corps_emission: Any
) -> dict:
    """Un chantier entier, du client au devis émis, sans rien devoir au seed.

    Le jeu de démonstration ne chiffre qu'une organisation : tout ce qui doit
    éprouver DEUX organisations — cloisonnement, vue inter-chantiers — ne peut
    donc pas s'appuyer dessus. Ce montage part de la bibliothèque de prix, la
    seule chose que les deux organisations partagent déjà.
    """
    livres = client.get("/api/v1/price-books", headers=headers).json()
    if livres:
        livre_id = livres[0]["id"]
    else:
        cree = client.post("/api/v1/price-books", headers=headers, json={"name": "Prix"})
        assert cree.status_code == 201, cree.text
        livre_id = cree.json()["id"]

    versions = client.get(f"/api/v1/price-books/{livre_id}/versions", headers=headers).json()
    if versions:
        version_prix = versions[0]["id"]
    else:
        creee = client.post(
            f"/api/v1/price-books/{livre_id}/versions", headers=headers, json={"label": "v1"}
        )
        assert creee.status_code == 201, creee.text
        version_prix = creee.json()["id"]

    article = client.post(
        f"/api/v1/price-books/versions/{version_prix}/items",
        headers=headers,
        json={
            "code": f"TER-{reference}",
            "label": "Déblai en terrain meuble",
            "unit_code": "m3",
            "unit_price": "18.50",
            "resource_kind": "subcontract",
        },
    )
    assert article.status_code == 201, article.text

    destinataire = fiche(client, headers, name=f"Client {reference}")
    projet = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "reference": reference,
            "name": f"Chantier {reference}",
            "client_id": destinataire["id"],
        },
    )
    assert projet.status_code == 201, projet.text
    projet_id = projet.json()["id"]

    bordereau = client.post(
        f"/api/v1/projects/{projet_id}/boqs", headers=headers, json={"name": "Métré"}
    )
    assert bordereau.status_code == 201, bordereau.text
    poste = client.post(
        f"/api/v1/boqs/{bordereau.json()['id']}/items",
        headers=headers,
        json={
            "position": "01.10",
            "designation": "Déblai en terrain meuble",
            "unit_code": "m3",
            "quantity": "100",
            "price_item_id": article.json()["id"],
        },
    )
    assert poste.status_code == 201, poste.text

    estimation = client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": projet_id,
            "boq_id": bordereau.json()["id"],
            "price_book_version_id": version_prix,
            "name": "Étude de prix",
        },
    )
    assert estimation.status_code == 201, estimation.text
    estimation_json = estimation.json()
    version = client.get(
        f"/api/v1/estimates/{estimation_json['id']}/versions", headers=headers
    ).json()[0]

    geler(client, headers, estimation_json, version)
    devis = emettre(client, headers, estimation_json, version, **corps_emission)
    assert devis.status_code == 201, devis.text
    return {**devis.json(), "project_id": projet_id, "estimate_id": estimation_json["id"]}
