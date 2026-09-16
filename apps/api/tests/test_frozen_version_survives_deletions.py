"""Une version gelée ne bouge pas quand le bordereau qu'elle a chiffré disparaît.

Un test existant montre qu'un **changement de prix** ne déplace pas un total
gelé. La **suppression d'un poste** n'était couverte nulle part, et les tranches
multi-tenant l'ont rendue pertinente : les PR #8 et #9 ont posé des
`ON DELETE CASCADE` sur les clés composites, dont
`fk_boq_items_parent_tenant` — supprimer une ligne de section emporte désormais
ses lignes filles.

L'invariant métier est simple et il engage : **un devis gelé est une pièce
contractuelle**. S'il changeait après coup, deux exemplaires du même document
porteraient deux montants, et c'est l'application qui aurait tort.

La garantie ne vient pas d'un refus de supprimer — la suppression est acceptée,
204 — mais de l'instantané : la version gelée porte son propre `snapshot` et son
`snapshot_sha256`, et tout ce qui la relit part de là. Ce fichier le vérifie sur
les quatre sorties visibles d'un client : le total rendu par l'API, l'empreinte,
l'export CSV et l'aperçu imprimable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def devis_gele(seeded_client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    """Un devis gelé de deux postes, et les postes qui l'ont nourri."""
    client, entetes = seeded_client, headers
    projet = client.post(
        "/api/v1/projects", headers=entetes, json={"reference": "GEL-SUP", "name": "Gel"}
    ).json()
    boq = client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=entetes, json={"name": "Métré"}
    ).json()
    livre = client.post(
        "/api/v1/price-books", headers=entetes, json={"name": "Prix pour gel"}
    ).json()
    version_prix = client.post(
        f"/api/v1/price-books/{livre['id']}/versions", headers=entetes, params={"label": "v1"}
    ).json()
    prix = client.post(
        f"/api/v1/price-books/versions/{version_prix['id']}/items",
        headers=entetes,
        json={"code": "X", "label": "Poste", "unit_code": "m3", "unit_price": "10"},
    ).json()
    postes = [
        client.post(
            f"/api/v1/boqs/{boq['id']}/items",
            headers=entetes,
            json={
                "position": f"1.{numero}",
                "designation": f"Poste {numero}",
                "unit_code": "m3",
                "quantity": "2",
                "price_item_id": prix["id"],
            },
        ).json()
        for numero in range(2)
    ]
    estimation = client.post(
        "/api/v1/estimates",
        headers=entetes,
        json={
            "project_id": projet["id"],
            "boq_id": boq["id"],
            "price_book_version_id": version_prix["id"],
            "name": "Gel",
        },
    ).json()
    version = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=entetes, json={"label": "v1"}
    ).json()
    gel = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions/{version['id']}/freeze",
        headers=entetes,
        json={"confirm": True, "label": "gelée"},
    )
    assert gel.status_code == 200, gel.text
    assert gel.json()["status"] == "frozen"
    return {"estimation": estimation["id"], "version": version["id"], "postes": postes}


def _sorties(client: TestClient, entetes: dict[str, str], devis: dict[str, object]) -> dict:
    """Les quatre sorties qu'un client peut voir d'une version gelée."""
    estimation, version = devis["estimation"], devis["version"]
    liste = client.get(f"/api/v1/estimates/{estimation}/versions", headers=entetes).json()
    ligne = next(entree for entree in liste if entree["id"] == version)
    return {
        "statut": ligne["status"],
        "total": ligne["total_selling_price_ht"],
        "empreinte": ligne["snapshot_sha256"],
        "csv": client.get(
            f"/api/v1/estimates/{estimation}/versions/{version}/export.csv", headers=entetes
        ).text,
        "html": client.get(
            f"/api/v1/estimates/{estimation}/versions/{version}/quote.html", headers=entetes
        ).text,
    }


class TestDeletingAPricedLineLeavesTheFrozenVersionAlone:
    def test_the_deletion_is_accepted(
        self, seeded_client: TestClient, headers: dict[str, str], devis_gele: dict
    ) -> None:
        """La garantie ne vient pas d'un refus : la suppression passe.

        L'écrire évite qu'on croie l'invariant tenu par un blocage. S'il devenait
        un refus un jour, ce test le dirait — et ce serait une décision, pas un
        effet de bord.
        """
        poste = devis_gele["postes"][0]
        reponse = seeded_client.delete(f"/api/v1/boq-items/{poste['id']}", headers=headers)
        assert reponse.status_code == 204, reponse.text

    def test_none_of_the_four_visible_outputs_moves(
        self, seeded_client: TestClient, headers: dict[str, str], devis_gele: dict
    ) -> None:
        avant = _sorties(seeded_client, headers, devis_gele)
        assert avant["statut"] == "frozen"
        assert len(avant["empreinte"]) == 64

        poste = devis_gele["postes"][0]
        assert (
            seeded_client.delete(f"/api/v1/boq-items/{poste['id']}", headers=headers).status_code
            == 204
        )

        apres = _sorties(seeded_client, headers, devis_gele)
        bouges = sorted(cle for cle in avant if avant[cle] != apres[cle])
        assert bouges == [], (
            f"une version gelée a changé après la suppression d'un poste : {bouges}"
        )

    def test_the_boq_itself_really_lost_the_line(
        self, seeded_client: TestClient, headers: dict[str, str], devis_gele: dict
    ) -> None:
        """Sans ceci, une suppression sans effet ferait passer le test précédent.

        Si la ligne n'était pas réellement partie du bordereau, l'immuabilité du
        devis gelé ne prouverait rien. Le contrôle passe par la LISTE du
        bordereau : il n'existe pas de route de lecture d'une ligne seule, et
        interroger `GET /boq-items/{id}` rendait un code qui ne disait rien de
        la ligne — première version de ce test.
        """
        poste = devis_gele["postes"][0]
        boq_id = poste["boq_id"]
        avant = seeded_client.get(f"/api/v1/boqs/{boq_id}/items", headers=headers).json()
        seeded_client.delete(f"/api/v1/boq-items/{poste['id']}", headers=headers)
        apres = seeded_client.get(f"/api/v1/boqs/{boq_id}/items", headers=headers).json()
        assert len(apres) == len(avant) - 1, (len(avant), len(apres))
        assert poste["id"] not in {ligne["id"] for ligne in apres}
