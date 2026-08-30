"""Le répertoire des clients : réutilisable, cloisonné, jamais fusionné tout seul.

Ce que ces tests tiennent, et que le champ libre `projects.client_name` ne
tenait pas :

* deux chantiers pour la même entreprise désignent la MÊME fiche ;
* deux fiches de même nom restent deux fiches — l'homonymie est fréquente
  (« Ets Dupont ») et fusionner sur le nom détruirait un client réel ;
* une fiche du voisin est introuvable, y compris quand on connaît son
  identifiant, et y compris en la posant dans un projet ;
* un ancien projet SANS fiche reste lisible et exploitable ; c'est seulement
  la première émission qui exige une sélection explicite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login

FICHE = {
    "name": "SPRL Terrassements Dupont",
    "company_number": "BE 0123.456.749",
    "billing_address": "Chaussée de Namur 44",
    "postal_code": "5030",
    "city": "Gembloux",
    "contact_name": "Marie Dupont",
    "email": "contact@dupont.example",
    "phone": "+32 81 00 00 00",
}


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def voisin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@janssens.demo")


def creer(client: TestClient, headers: dict[str, str], **remplacements: object) -> dict:
    reponse = client.post("/api/v1/clients", headers=headers, json={**FICHE, **remplacements})
    assert reponse.status_code == 201, reponse.text
    resultat: dict = reponse.json()
    return resultat


# --------------------------------------------------------------------------
# Réutilisation
# --------------------------------------------------------------------------


def test_une_fiche_sert_a_plusieurs_chantiers(seeded_client: TestClient, admin) -> None:
    """La raison d'être du répertoire : saisir le client UNE fois."""
    fiche = creer(seeded_client, admin)
    for reference in ("CH-2026-101", "CH-2026-102"):
        projet = seeded_client.post(
            "/api/v1/projects",
            headers=admin,
            json={"reference": reference, "name": reference, "client_id": fiche["id"]},
        )
        assert projet.status_code == 201, projet.text
        assert projet.json()["client_id"] == fiche["id"]


def test_deux_fiches_de_meme_nom_ne_sont_jamais_fusionnees(
    seeded_client: TestClient, admin
) -> None:
    """L'homonymie est courante ; la fusion automatique détruirait un client.

    Deux « Ets Dupont » à deux adresses sont deux entreprises. Le répertoire
    accepte les deux et les garde distinctes : rapprocher deux fiches est une
    décision humaine, pas une déduction sur une chaîne de caractères.
    """
    premiere = creer(seeded_client, admin, city="Gembloux")
    seconde = creer(seeded_client, admin, city="Charleroi", company_number=None)
    assert premiere["id"] != seconde["id"]

    liste = seeded_client.get("/api/v1/clients", headers=admin).json()
    homonymes = [f for f in liste if f["name"] == FICHE["name"]]
    assert len(homonymes) == 2, "Le répertoire a rapproché deux fiches de même nom."
    assert {f["city"] for f in homonymes} == {"Gembloux", "Charleroi"}


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------


def test_la_recherche_ignore_la_casse_du_nom_et_du_numero(
    seeded_client: TestClient, admin
) -> None:
    fiche = creer(seeded_client, admin)
    creer(seeded_client, admin, name="Commune de Wavre", company_number="BE 0207.363.192")

    par_nom = seeded_client.get("/api/v1/clients?q=dupont", headers=admin).json()
    assert [f["id"] for f in par_nom] == [fiche["id"]]

    par_numero = seeded_client.get("/api/v1/clients?q=0123.456", headers=admin).json()
    assert [f["id"] for f in par_numero] == [fiche["id"]]


# --------------------------------------------------------------------------
# Archivage
# --------------------------------------------------------------------------


def test_une_fiche_archivee_sort_de_la_liste_mais_reste_lisible(
    seeded_client: TestClient, admin
) -> None:
    """Archiver n'est pas effacer : le devis émis en porte l'instantané."""
    fiche = creer(seeded_client, admin)
    archive = seeded_client.patch(
        f"/api/v1/clients/{fiche['id']}", headers=admin, json={"status": "archived"}
    )
    assert archive.status_code == 200, archive.text

    courante = seeded_client.get("/api/v1/clients", headers=admin).json()
    assert fiche["id"] not in {f["id"] for f in courante}

    complete = seeded_client.get("/api/v1/clients?include_archived=true", headers=admin).json()
    assert fiche["id"] in {f["id"] for f in complete}

    detail = seeded_client.get(f"/api/v1/clients/{fiche['id']}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["status"] == "archived"


def test_une_fiche_utilisee_par_un_chantier_ne_se_supprime_pas(
    seeded_client: TestClient, admin
) -> None:
    fiche = creer(seeded_client, admin)
    projet = seeded_client.post(
        "/api/v1/projects",
        headers=admin,
        json={"reference": "CH-2026-201", "name": "Voirie", "client_id": fiche["id"]},
    )
    assert projet.status_code == 201, projet.text

    refus = seeded_client.delete(f"/api/v1/clients/{fiche['id']}", headers=admin)
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "client_referenced"

    toujours_la = seeded_client.get(f"/api/v1/clients/{fiche['id']}", headers=admin)
    assert toujours_la.status_code == 200


def test_supprimer_une_fiche_libre_l_archive_au_lieu_de_la_detruire(
    seeded_client: TestClient, admin
) -> None:
    """`DELETE` archive. Détruire laisserait un devis désignant un néant.

    La fiche sort de la liste de travail — c'est ce que l'utilisateur demande —
    mais elle reste lisible, et un devis émis qui la cite reste explicable.
    """
    fiche = creer(seeded_client, admin)
    supprime = seeded_client.delete(f"/api/v1/clients/{fiche['id']}", headers=admin)
    assert supprime.status_code == 204, supprime.text

    apres = seeded_client.get(f"/api/v1/clients/{fiche['id']}", headers=admin)
    assert apres.status_code == 200
    assert apres.json()["status"] == "archived"
    assert fiche["id"] not in {
        f["id"] for f in seeded_client.get("/api/v1/clients", headers=admin).json()
    }


# --------------------------------------------------------------------------
# Cloisonnement
# --------------------------------------------------------------------------


def test_la_fiche_du_voisin_est_invisible_et_inutilisable(
    seeded_client: TestClient, admin, voisin
) -> None:
    """Ni dans la liste, ni par identifiant, ni comme client d'un projet."""
    etrangere = creer(seeded_client, voisin, name="Client du voisin")

    liste = seeded_client.get("/api/v1/clients?include_archived=true", headers=admin).json()
    assert etrangere["id"] not in {f["id"] for f in liste}

    assert seeded_client.get(f"/api/v1/clients/{etrangere['id']}", headers=admin).status_code == 404

    refus = seeded_client.post(
        "/api/v1/projects",
        headers=admin,
        json={"reference": "CH-2026-301", "name": "Tentative", "client_id": etrangere["id"]},
    )
    assert refus.status_code == 404, refus.text
    assert refus.json()["detail"]["code"] != "duplicate_reference"


def test_rattacher_un_projet_a_une_fiche_etrangere_est_refuse_en_404(
    seeded_client: TestClient, admin, voisin
) -> None:
    etrangere = creer(seeded_client, voisin, name="Client du voisin")
    projet = seeded_client.post(
        "/api/v1/projects", headers=admin, json={"reference": "CH-2026-302", "name": "Chantier"}
    ).json()

    refus = seeded_client.patch(
        f"/api/v1/projects/{projet['id']}", headers=admin, json={"client_id": etrangere["id"]}
    )
    assert refus.status_code == 404, refus.text

    inchange = seeded_client.get(f"/api/v1/projects/{projet['id']}", headers=admin).json()
    assert inchange["client_id"] is None


# --------------------------------------------------------------------------
# Les anciens projets
# --------------------------------------------------------------------------


def test_un_projet_anterieur_reste_lisible_sans_fiche(seeded_client: TestClient, admin) -> None:
    """La migration n'a converti personne : elle a ajouté une colonne nulle.

    Un projet du jeu de démonstration date d'avant le répertoire. Il se lit, se
    liste et se modifie ; ce qui exigera une décision, c'est d'émettre.
    """
    projets = seeded_client.get("/api/v1/projects", headers=admin).json()["items"]
    assert projets, "le jeu de démonstration ne porte aucun projet"
    anciens = [p for p in projets if p["client_id"] is None]
    assert anciens, "la migration a rattaché d'office un ancien projet à une fiche"

    detail = seeded_client.get(f"/api/v1/projects/{anciens[0]['id']}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["client_name"], "le nom de client historique a été perdu"


def test_un_projet_se_rattache_apres_coup_a_une_fiche(seeded_client: TestClient, admin) -> None:
    """La conversion existe, mais elle est explicite."""
    ancien = next(
        p
        for p in seeded_client.get("/api/v1/projects", headers=admin).json()["items"]
        if p["client_id"] is None
    )
    fiche = creer(seeded_client, admin)
    rattache = seeded_client.patch(
        f"/api/v1/projects/{ancien['id']}", headers=admin, json={"client_id": fiche["id"]}
    )
    assert rattache.status_code == 200, rattache.text
    assert rattache.json()["client_id"] == fiche["id"]


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------


def test_creation_et_modification_sont_journalisees(seeded_client: TestClient, admin) -> None:
    fiche = creer(seeded_client, admin)
    seeded_client.patch(f"/api/v1/clients/{fiche['id']}", headers=admin, json={"city": "Jodoigne"})

    evenements = seeded_client.get("/api/v1/audit/events?limit=100", headers=admin).json()["items"]
    actions = [e["action"] for e in evenements if e["object_id"] == fiche["id"]]
    assert "client.created" in actions
    assert "client.updated" in actions
