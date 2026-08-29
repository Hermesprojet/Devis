"""Une version publiée de bibliothèque de prix ne bouge plus.

C'est la promesse sur laquelle repose le gel d'un devis : une estimation
référence une `price_book_version_id`, et si le contenu de cette version peut
encore changer après coup, deux calculs de la même version peuvent différer.

Quatre chemins écrivent dans une version, plus la publication elle-même. Un
seul de ces refus était vérifié — celui de l'import, et seulement sur le
`commit`. Les autres tenaient sans que rien ne le dise :

    POST .../versions/{id}/items          ajout d'un prix à la main
    POST .../versions/{id}/composites     création d'un sous-détail
    POST .../versions/{id}/imports/preview  prévisualisation d'import
    POST .../versions/{id}/imports/{b}/commit  (déjà couvert)
    POST .../versions/{id}/publish        publier deux fois

Une garde retirée de l'un de ces chemins ne ferait rougir aucun test
aujourd'hui, et un devis gelé cesserait d'être reproductible sans que la suite
le signale.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version_id(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    versions = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()
    return versions[0]["id"]


def _publier(client: TestClient, headers: dict[str, str], version_id: str) -> None:
    reponse = client.post(f"/api/v1/price-books/versions/{version_id}/publish", headers=headers)
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["status"] == "published"


UN_PRIX = {
    "code": "TEST-APRES-PUBLICATION",
    "label": "Prix ajouté après publication",
    "unit_code": "m3",
    "unit_price": "42.50",
    "resource_kind": "material",
}

UN_SOUS_DETAIL = {
    "code": "SD-APRES-PUBLICATION",
    "label": "Sous-détail ajouté après publication",
    "unit_code": "m3",
    "components": [
        {
            "component_type": "lump_sum",
            "label": "forfait",
            "resource_kind": "other",
            "lump_sum_amount": "100.00",
        }
    ],
}


def test_a_price_cannot_be_added_by_hand_to_a_published_version(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    avant = seeded_client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["page"]["total"]

    _publier(seeded_client, headers, version_id)
    reponse = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/items", headers=headers, json=UN_PRIX
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["code"] == "version_published"

    # Le refus doit être un refus d'écriture, pas seulement un code de retour :
    # un handler qui écrit puis lève laisserait la ligne derrière lui.
    apres = seeded_client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["page"]["total"]
    assert apres == avant, f"{apres - avant} prix écrit(s) malgré le refus"


def test_a_composite_cannot_be_created_in_a_published_version(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    _publier(seeded_client, headers, version_id)
    reponse = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/composites",
        headers=headers,
        json=UN_SOUS_DETAIL,
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["code"] == "version_published"

    liste = seeded_client.get(
        f"/api/v1/price-books/versions/{version_id}/composites", headers=headers
    )
    assert liste.status_code == 200, liste.text
    codes = [c["code"] for c in liste.json()]
    assert UN_SOUS_DETAIL["code"] not in codes, "le sous-détail a été écrit malgré le refus"


def test_an_import_is_refused_before_the_file_is_even_parsed(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """La prévisualisation est refusée, pas seulement la confirmation.

    Le refus au `commit` seul laisserait un lot en attente contre une version
    publiée : un objet qui ne pourra jamais être confirmé, et qui donne à
    l'utilisateur l'impression que l'import a commencé.
    """
    _publier(seeded_client, headers, version_id)
    reponse = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("prix.csv", io.BytesIO(b"code;label\n"), "text/csv")},
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["code"] == "version_published"


def test_publishing_twice_is_refused_without_touching_the_publication_date(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """La date de publication est une référence : la réécrire la falsifierait.

    Un second `publish` qui repasserait par le chemin nominal remplacerait
    `published_at` par l'instant courant. La version paraîtrait publiée
    aujourd'hui alors qu'elle l'a été il y a six mois, et les devis gelés
    entre-temps sembleraient antérieurs à la bibliothèque qu'ils citent.
    """
    _publier(seeded_client, headers, version_id)
    premiere = seeded_client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=1", headers=headers
    )
    assert premiere.status_code == 200

    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    date_initiale = next(
        v["published_at"]
        for v in seeded_client.get(
            f"/api/v1/price-books/{book['id']}/versions", headers=headers
        ).json()
        if v["id"] == version_id
    )
    assert date_initiale is not None

    seconde = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/publish", headers=headers
    )
    assert seconde.status_code == 409, seconde.text
    assert seconde.json()["detail"]["code"] == "already_published"

    date_apres = next(
        v["published_at"]
        for v in seeded_client.get(
            f"/api/v1/price-books/{book['id']}/versions", headers=headers
        ).json()
        if v["id"] == version_id
    )
    assert date_apres == date_initiale, (
        f"la date de publication est passée de {date_initiale} à {date_apres}"
    )


def test_nothing_returns_a_published_version_to_a_writable_state(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """Aucune route ne dépublie.

    Sans ce balayage, quelqu'un pourrait ajouter un `POST .../unpublish` par
    commodité, et les quatre refus ci-dessus deviendraient contournables en une
    requête.
    """
    _publier(seeded_client, headers, version_id)

    document = seeded_client.app.openapi()  # type: ignore[attr-defined]
    suspectes = [
        chemin
        for chemin in document["paths"]
        if any(mot in chemin for mot in ("unpublish", "reopen", "draft", "unfreeze"))
    ]
    assert not suspectes, f"routes qui pourraient rouvrir une version : {suspectes}"

    # Et aucune méthode d'écriture sur la version elle-même.
    chemin_version = "/api/v1/price-books/versions/{version_id}"
    operations = document["paths"].get(chemin_version, {})
    ecritures = sorted(m for m in operations if m in {"patch", "put", "delete"})
    assert not ecritures, f"{chemin_version} accepte {ecritures}"
