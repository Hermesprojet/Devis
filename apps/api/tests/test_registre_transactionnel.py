"""Le contrat : aucun 2xx d'écriture avant que l'écriture soit validée.

Deux moitiés, également nécessaires.

La première est un REGISTRE : `metreo_api/transactions.py` classe chaque route
selon ce qu'elle écrit vraiment. Les tests ci-dessous le comparent à ce que
l'application expose, dans les deux sens — une route d'écriture non classée, et
une entrée qui ne correspond plus à rien, sont deux façons de mentir.

La seconde est une OBSERVATION : pour une route de chaque famille, on regarde
la base par une session indépendante à l'instant où la réponse part. Ce que le
client lit dans sa réponse doit déjà exister pour tout le monde.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_api.models import AuditEvent, Document, DocumentRevision, Project, TaxRateRow
from metreo_api.transactions import (
    REGISTRE,
    VERBES_ECRIVANTS,
    Famille,
    RouteTransactionnelle,
    classer_les_routes,
    parcourir,
)

from .observateur import Constat, enveloppe_observatrice


@pytest.fixture()
def application(migrated: None):
    from metreo_api.main import create_app

    return create_app()


# ---------------------------------------------------------------------------
# Le registre contre la réalité
# ---------------------------------------------------------------------------


def test_toute_route_d_ecriture_est_classee(application) -> None:
    """Aucun POST, PUT, PATCH ou DELETE hors du registre.

    `create_app` lève déjà si c'est le cas — ce test le redit en nommant les
    routes, pour que l'échec se lise sans lancer un serveur.
    """
    absentes = [
        f"{methode} {chemin}"
        for methode, chemin, route in parcourir(application.routes)
        if methode in VERBES_ECRIVANTS
        and (methode, chemin) not in REGISTRE
        and getattr(route, "include_in_schema", True)
    ]
    assert absentes == [], (
        "Ces routes écrivent et ne sont pas classées : leur écriture ne serait "
        "pas validée avant la réponse."
    )


def test_le_registre_ne_decrit_aucune_route_disparue(application) -> None:
    """Une entrée qui ne correspond plus à rien fait croire à une garantie."""
    reelles = {(methode, chemin) for methode, chemin, _ in parcourir(application.routes)}
    fantomes = sorted(f"{m} {c}" for (m, c) in REGISTRE if (m, c) not in reelles)
    assert fantomes == []


def test_chaque_routeur_passe_par_la_route_transactionnelle(application) -> None:
    """Un routeur qui oublie `route_class` échappe silencieusement au modèle."""
    etrangeres = sorted(
        f"{methode} {chemin} ({type(route).__name__})"
        for methode, chemin, route in parcourir(application.routes)
        if chemin.startswith("/api/v1") and not isinstance(route, RouteTransactionnelle)
    )
    assert etrangeres == []


def test_l_application_refuse_de_demarrer_sur_une_ecriture_non_classee(application) -> None:
    """Le contrat est tenu au démarrage, pas seulement par cette suite."""
    from fastapi import APIRouter

    routeur = APIRouter(route_class=RouteTransactionnelle)

    @routeur.post("/route-oubliee")
    def _oubliee() -> dict[str, str]:  # pragma: no cover - jamais appelée
        return {}

    application.include_router(routeur, prefix="/api/v1")
    with pytest.raises(RuntimeError, match="route-oubliee"):
        classer_les_routes(application)


def test_les_familles_couvrent_ce_que_les_routes_font_vraiment() -> None:
    """Le registre distingue quatre familles, et les quatre servent."""
    presentes = set(REGISTRE.values())
    assert presentes == set(Famille), (
        "Les quatre familles doivent servir : une famille vide est une famille "
        "que personne ne relit."
    )
    # Une route de LECTURE n'est inscrite que si son VERBE ferait croire à une
    # écriture. Les vraies lectures restent hors registre, et c'est ce défaut
    # qui rend l'oubli d'une écriture visible au démarrage.
    assert all(
        methode in VERBES_ECRIVANTS
        for (methode, _), famille in REGISTRE.items()
        if famille is Famille.LECTURE
    )


# ---------------------------------------------------------------------------
# L'observation, famille par famille
# ---------------------------------------------------------------------------


@pytest.fixture()
def observer(
    seeded: dict[str, str],
) -> Callable[[Callable[[Session], Any]], tuple[TestClient, list[Constat]]]:
    """Rend un client dont chaque réponse est précédée d'un coup d'œil en base."""
    from metreo_api.main import create_app

    fermetures: list[TestClient] = []

    def fabriquer(sonde: Callable[[Session], Any]) -> tuple[TestClient, list[Constat]]:
        enveloppe, constats = enveloppe_observatrice(create_app(), sonde)
        client = TestClient(enveloppe)
        client.__enter__()
        fermetures.append(client)
        return client, constats

    yield fabriquer
    for client in fermetures:
        client.__exit__(None, None, None)


def _entete(client: TestClient, email: str = "admin@dubois.demo") -> dict[str, str]:
    reponse = client.post("/api/v1/auth/dev-login", json={"email": email})
    assert reponse.status_code == 200, reponse.text
    return {"Authorization": f"Bearer {reponse.json()['access_token']}"}


def _dernier(constats: list[Constat], methode: str) -> Constat:
    for constat in reversed(constats):
        if constat.methode == methode:
            return constat
    raise AssertionError(f"aucun {methode} observé")


def test_une_creation_auditee_est_visible_quand_le_201_part(observer) -> None:
    """Famille ÉCRITURE AUDITÉE : la ligne ET son audit, ensemble.

    Le client reçoit un identifiant. S'il le relit tout de suite — c'est ce que
    fait une interface après une création — il doit le trouver.
    """
    client, constats = observer(
        lambda s: {
            "projets": {p.reference: p.id for p in s.scalars(select(Project))},
            "audit": [e.action for e in s.scalars(select(AuditEvent))],
        }
    )
    entete = _entete(client)
    reponse = client.post(
        "/api/v1/projects",
        json={"reference": "PRJ-TRANSAC", "name": "Chantier transactionnel"},
        headers=entete,
    )
    assert reponse.status_code == 201, reponse.text
    identifiant = reponse.json()["id"]

    constat = _dernier(constats, "POST")
    assert constat.code == 201
    assert constat.vu["projets"].get("PRJ-TRANSAC") == identifiant, (
        f"L'identifiant rendu au client ne désigne encore rien pour une autre session : {constat}"
    )
    assert "project.created" in constat.vu["audit"], (
        f"L'événement d'audit n'est pas validé quand le 201 part : {constat}"
    )


def test_une_modification_est_visible_quand_le_200_part(observer) -> None:
    client, constats = observer(lambda s: {p.reference: p.name for p in s.scalars(select(Project))})
    entete = _entete(client)
    cree = client.post(
        "/api/v1/projects",
        json={"reference": "PRJ-MODIF", "name": "Avant"},
        headers=entete,
    ).json()
    reponse = client.patch(f"/api/v1/projects/{cree['id']}", json={"name": "Après"}, headers=entete)
    assert reponse.status_code == 200, reponse.text
    constat = _dernier(constats, "PATCH")
    assert constat.vu.get("PRJ-MODIF") == "Après", f"Modification non validée : {constat}"


def test_une_suppression_logique_est_visible_quand_le_204_part(observer) -> None:
    client, constats = observer(
        lambda s: [p.reference for p in s.scalars(select(Project)) if p.deleted_at is None]
    )
    entete = _entete(client)
    cree = client.post(
        "/api/v1/projects",
        json={"reference": "PRJ-SUPPR", "name": "À supprimer"},
        headers=entete,
    ).json()
    reponse = client.delete(f"/api/v1/projects/{cree['id']}", headers=entete)
    assert reponse.status_code == 204, reponse.text
    constat = _dernier(constats, "DELETE")
    assert "PRJ-SUPPR" not in constat.vu, f"Suppression non validée : {constat}"


def test_la_famille_ecriture_simple_est_le_parcours_de_connexion() -> None:
    """Famille ÉCRITURE : trois routes, et elles sont observées ailleurs.

    Le parcours de connexion écrit sans auditer. Son observation à
    `http.response.start` demande un faux fournisseur d'identité : elle vit
    dans `test_oidc_http_flow.py`, avec le reste du parcours, plutôt que
    recopiée ici. Ce test dit seulement quelles routes composent la famille,
    pour qu'une quatrième ajoutée en douce ne passe pas inaperçue.
    """
    simples = sorted(
        f"{methode} {chemin}"
        for (methode, chemin), famille in REGISTRE.items()
        if famille is Famille.ECRITURE
    )
    assert simples == [
        "GET /api/v1/auth/oidc/callback",
        "GET /api/v1/auth/oidc/start",
        "POST /api/v1/auth/oidc/exchange",
    ]


def test_un_taux_cree_est_utilisable_immediatement(observer) -> None:
    """Le premier enchaînement qui exposait la course dans l'interface."""
    client, constats = observer(lambda s: [t.code for t in s.scalars(select(TaxRateRow))])
    entete = _entete(client)
    reponse = client.post(
        "/api/v1/organization/tax-rates",
        json={
            "code": "TVA-TRANSAC",
            "label": "TVA de contrôle",
            "rate": "0.21",
            "applies_from": "2020-01-01",
        },
        headers=entete,
    )
    assert reponse.status_code == 201, reponse.text
    constat = _dernier(constats, "POST")
    assert "TVA-TRANSAC" in constat.vu, f"Taux non validé quand le 201 part : {constat}"


def test_un_document_et_sa_revision_sont_visibles_avec_leur_fichier(observer, tmp_path) -> None:
    """Famille ÉCRITURE ET FICHIER : la ligne, l'audit, et les octets.

    Le client reçoit l'identifiant d'une révision et peut demander son contenu
    dans la foulée. Si la révision n'est pas encore validée, ce téléchargement
    rend 404 sur un document qui existe pourtant.
    """
    client, constats = observer(
        lambda s: {
            "documents": [d.id for d in s.scalars(select(Document))],
            "revisions": {r.id: r.storage_key for r in s.scalars(select(DocumentRevision))},
            "audit": [e.action for e in s.scalars(select(AuditEvent))],
        }
    )
    entete = _entete(client)
    projet = client.post(
        "/api/v1/projects",
        json={"reference": "PRJ-DOC-TRANSAC", "name": "Chantier avec pièce"},
        headers=entete,
    ).json()
    document = client.post(
        f"/api/v1/projects/{projet['id']}/documents",
        json={"title": "CCTP"},
        headers=entete,
    ).json()

    reponse = client.post(
        f"/api/v1/documents/{document['id']}/revisions",
        files={
            "file": ("cctp.pdf", b"%PDF-1.7\ncontrat transactionnel\n%%EOF\n", "application/pdf")
        },
        headers=entete,
    )
    assert reponse.status_code == 201, reponse.text
    revision = reponse.json()

    constat = _dernier(constats, "POST")
    assert constat.code == 201
    assert revision["id"] in constat.vu["revisions"], (
        f"La révision annoncée n'existe pour personne d'autre : {constat}"
    )
    assert "document.revision_added" in constat.vu["audit"]

    # Et la conséquence, immédiatement : le contenu se télécharge.
    contenu = client.get(
        f"/api/v1/documents/{document['id']}/revisions/{revision['id']}/content",
        headers=entete,
    )
    assert contenu.status_code == 200, contenu.text


def test_l_audit_d_un_telechargement_est_valide_avant_le_premier_octet(observer) -> None:
    """Une lecture qui écrit : la trace part avant le corps, ou elle ment."""
    client, constats = observer(lambda s: [e.action for e in s.scalars(select(AuditEvent))])
    entete = _entete(client)
    # Le jeu de démonstration porte déjà un devis complet : ce test regarde
    # l'audit d'un export, pas la fabrication d'un devis.
    estimation = client.get("/api/v1/estimates", headers=entete).json()[0]
    version = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions",
        json={"label": "v-export"},
        headers=entete,
    ).json()

    reponse = client.get(
        f"/api/v1/estimates/{estimation['id']}/versions/{version['id']}/export.csv",
        headers=entete,
    )
    assert reponse.status_code == 200, reponse.text
    constat = _dernier(constats, "GET")
    assert any("export" in action for action in constat.vu), (
        f"L'audit de l'export n'est pas validé quand la réponse part : {constat}"
    )
