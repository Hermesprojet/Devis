"""Le cycle commercial reste dans son organisation, et dans ses permissions.

La matrice d'autorisation éprouve déjà 401, 403 et 404 route par route. Ce
fichier éprouve ce qu'elle ne regarde pas : qu'un voisin ne voit pas un devis
dans la vue inter-chantiers, qu'une session publique ouverte sur un devis n'en
ouvre aucun autre, et que les boutons de l'écran correspondent aux permissions
que l'API exige vraiment.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from metreo_api.security.roles import ROLE_PERMISSIONS, Permission, Role

from .conftest import login
from .emission import graphe_complet

_COMPTEUR = iter(range(1, 1000))


def _devis(client: TestClient, entete: dict[str, str]) -> dict:
    """Un devis émis pour l'organisation de cet en-tête, quelle qu'elle soit.

    Le jeu de démonstration ne chiffre qu'une des deux : tout monter ici est le
    seul moyen d'éprouver le cloisonnement sur deux devis réels.
    """
    return graphe_complet(client, entete, f"CLOIS-{next(_COMPTEUR):03d}")


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def voisin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@janssens.demo")


def test_la_vue_inter_chantiers_ne_montre_que_ses_propres_devis(
    seeded_client: TestClient, admin, voisin
) -> None:
    mien = _devis(seeded_client, admin)
    sien = _devis(seeded_client, voisin)
    assert mien["id"] != sien["id"]

    a_moi = seeded_client.get("/api/v1/quotes", headers=admin).json()
    assert [d["id"] for d in a_moi["items"]] == [mien["id"]]

    au_voisin = seeded_client.get("/api/v1/quotes", headers=voisin).json()
    assert [d["id"] for d in au_voisin["items"]] == [sien["id"]]


def test_le_journal_et_les_liens_d_un_voisin_sont_introuvables(
    seeded_client: TestClient, admin, voisin
) -> None:
    sien = _devis(seeded_client, voisin)
    lien = seeded_client.post(
        f"/api/v1/issued-quotes/{sien['id']}/share-links", headers=voisin, json={}
    )
    assert lien.status_code == 201, lien.text

    assert (
        seeded_client.get(f"/api/v1/issued-quotes/{sien['id']}", headers=admin).status_code == 404
    )
    refus = seeded_client.delete(
        f"/api/v1/issued-quotes/{sien['id']}/share-links/{lien.json()['link']['id']}",
        headers=admin,
    )
    assert refus.status_code == 404


def test_une_session_publique_n_ouvre_que_le_devis_de_son_lien(
    seeded_client: TestClient, admin, voisin
) -> None:
    """Elle ne porte aucun droit propre : elle désigne UN lien, et rien d'autre."""
    mien = _devis(seeded_client, admin)
    sien = _devis(seeded_client, voisin)
    lien = seeded_client.post(
        f"/api/v1/issued-quotes/{mien['id']}/share-links", headers=admin, json={}
    ).json()
    secret = lien["url"].split("#", 1)[1]
    assert (
        seeded_client.post("/api/v1/public/quote-sessions", json={"secret": secret}).status_code
        == 204
    )

    vue = seeded_client.get("/api/v1/public/quote").json()
    #: Le NUMÉRO ne distingue rien : il est propre à chaque organisation, et
    #: les deux devis portent légitimement « DEV-2026-0001 ». C'est le contenu
    #: qui le fait — chantier et destinataire.
    mien_detail = seeded_client.get(f"/api/v1/issued-quotes/{mien['id']}", headers=admin).json()
    sien_detail = seeded_client.get(f"/api/v1/issued-quotes/{sien['id']}", headers=voisin).json()
    assert vue["project_reference"] == mien_detail["project_reference"]
    #: Il n'existe aucun paramètre par lequel demander un AUTRE devis : la
    #: session désigne le lien, et le lien désigne le devis.
    assert vue["project_reference"] != sien_detail["project_reference"]
    assert vue["client_name"] != sien_detail["client_snapshot"]["name"]


# --------------------------------------------------------------------------
# Les permissions que l'API exige vraiment
# --------------------------------------------------------------------------


def test_partager_un_devis_exige_le_droit_d_exporter_au_client(
    seeded_client: TestClient, admin
) -> None:
    """Mettre le document entre les mains du client, c'est l'exporter."""
    devis = _devis(seeded_client, admin)
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    refus = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/share-links", headers=lecteur, json={}
    )
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "export:client"


def test_enregistrer_une_reponse_exige_le_droit_d_ecrire_une_estimation(
    seeded_client: TestClient, admin
) -> None:
    devis = _devis(seeded_client, admin)
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    refus = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=lecteur,
        json={"kind": "transmitted", "channel": "email"},
    )
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "estimate:write"


def test_un_lecteur_voit_la_fiche_mais_n_y_agit_pas(seeded_client: TestClient, admin) -> None:
    """L'écran peut donc l'afficher sans bouton, et c'est cohérent."""
    devis = _devis(seeded_client, admin)
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    assert (
        seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}", headers=lecteur).status_code
        == 200
    )
    assert seeded_client.get("/api/v1/quotes", headers=lecteur).status_code == 200


def test_les_permissions_exigees_existent_dans_la_matrice(seeded_client: TestClient) -> None:
    """Les deux permissions du cycle sont bien celles de rôles réels.

    Une permission qu'aucun rôle ne porte rendrait la fonction inatteignable ;
    une permission que tous portent ne séparerait rien.
    """
    for permission in (Permission.EXPORT_CLIENT, Permission.ESTIMATE_WRITE):
        porteurs = {r for r, p in ROLE_PERMISSIONS.items() if permission in p}
        assert porteurs, f"aucun rôle ne porte {permission.value}"
        assert porteurs != set(Role), f"{permission.value} ne sépare aucun rôle"
