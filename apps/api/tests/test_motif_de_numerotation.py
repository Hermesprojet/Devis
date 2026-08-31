"""Le motif de numérotation : refusé à la saisie, jamais contourné à l'émission.

La première version l'appliquait dans un `try` et retombait en silence sur
`DEV-{year}-{sequence:04d}`. Une entreprise qui saisit `FACT-{sequenc}` — une
lettre de trop — croyait émettre sous son format et recevait des numéros qui
ne lui ressemblaient pas. Le devis partait chez le client avec ce numéro-là,
et aucun écran ne disait pourquoi.

Trois moments, éprouvés séparément : la configuration refuse, l'écran
prévisualise, et une configuration historique illisible fait refuser
l'émission au lieu de servir un numéro de secours.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from metreo_api.db import get_session_factory
from metreo_api.services import numerotation

from .conftest import login
from .emission import emettre, fiche, geler, prix_manquant, rattacher


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _regler(client: TestClient, admin: dict[str, str], motif: str):
    return client.patch(
        "/api/v1/organization/settings", headers=admin, json={"quote_number_pattern": motif}
    )


# --------------------------------------------------------------------------
# Le validateur
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("motif", "attendu"),
    [
        (None, "DEV-2026-0007"),
        ("", "DEV-2026-0007"),
        ("   ", "DEV-2026-0007"),
        ("DEV-{year}-{sequence:04d}", "DEV-2026-0007"),
        ("DUB/{year}/{sequence:03d}", "DUB/2026/007"),
        ("{year}{sequence:05d}", "202600007"),
    ],
)
def test_un_motif_utilisable_rend_le_numero_attendu(motif, attendu: str) -> None:
    assert numerotation.apercu(motif) == attendu


@pytest.mark.parametrize(
    ("motif", "raison"),
    [
        ("FACT-{sequenc}", "il nomme {sequenc}"),
        ("{}-{sequence}", "champ sans nom"),
        ("{0}-{sequence}", "champ sans nom"),
        ("DEV-{year", "mal formé"),
        ("SANS-RANG-{year}", "ne contient pas {sequence}"),
        ("{sequence:!!}", "ne s'applique pas"),
        ("X" * 70 + "{sequence}", "au-delà des 60"),
    ],
)
def test_un_motif_inutilisable_est_refuse_en_disant_pourquoi(motif: str, raison: str) -> None:
    with pytest.raises(numerotation.MotifInvalide) as refus:
        numerotation.verifier(motif)
    assert raison in refus.value.message
    assert refus.value.code == "quote_number_pattern_invalid"
    assert refus.value.context["pattern"] == motif


def test_effacer_le_motif_revient_au_defaut_plutot_qu_a_un_refus() -> None:
    """Vider un champ est une intention légitime, pas une faute de saisie."""
    assert numerotation.verifier("") == numerotation.MOTIF_PAR_DEFAUT
    assert numerotation.verifier(None) == numerotation.MOTIF_PAR_DEFAUT


# --------------------------------------------------------------------------
# La configuration
# --------------------------------------------------------------------------


def test_les_reglages_refusent_un_motif_inutilisable_et_ne_l_ecrivent_pas(
    seeded_client: TestClient, admin
) -> None:
    avant = seeded_client.get("/api/v1/organization/settings", headers=admin).json()

    refus = _regler(seeded_client, admin, "FACT-{sequenc}")
    assert refus.status_code == 422, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "quote_number_pattern_invalid"
    assert "{sequenc}" in detail["message"]

    apres = seeded_client.get("/api/v1/organization/settings", headers=admin).json()
    assert apres["quote_number_pattern"] == avant["quote_number_pattern"], (
        "un motif refusé a tout de même été écrit"
    )


def test_les_reglages_rendent_un_apercu_du_numero(seeded_client: TestClient, admin) -> None:
    """L'écran montre ce que le motif produit AVANT de s'en servir."""
    lecture = seeded_client.get("/api/v1/organization/settings", headers=admin).json()
    assert lecture["quote_number_preview"] == "DEV-2026-0007"

    accepte = _regler(seeded_client, admin, "DUB/{year}/{sequence:03d}")
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["quote_number_preview"] == "DUB/2026/007"


def test_effacer_le_motif_par_l_api_retablit_le_defaut(seeded_client: TestClient, admin) -> None:
    assert _regler(seeded_client, admin, "DUB/{year}/{sequence:03d}").status_code == 200
    remis = _regler(seeded_client, admin, "")
    assert remis.status_code == 200, remis.text
    assert remis.json()["quote_number_pattern"] == numerotation.MOTIF_PAR_DEFAUT


# --------------------------------------------------------------------------
# L'émission
# --------------------------------------------------------------------------


@pytest.fixture()
def pret(seeded_client: TestClient, admin) -> dict:
    estimation = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]
    prix_manquant(seeded_client, admin, estimation)
    rattacher(seeded_client, admin, estimation["project_id"], fiche(seeded_client, admin)["id"])
    geler(seeded_client, admin, estimation, version)
    return {"estimation": estimation, "version": version}


def test_une_configuration_historique_illisible_fait_refuser_l_emission(
    seeded_client: TestClient, admin, pret
) -> None:
    """Le cas que le repli silencieux cachait, et le seul qui compte vraiment.

    Le motif est posé DIRECTEMENT en base : c'est ainsi qu'il a pu arriver là,
    avant que les réglages ne le contrôlent. L'API le refuserait aujourd'hui.
    """
    identite = seeded_client.get("/api/v1/auth/me", headers=admin).json()
    with get_session_factory()() as session:
        session.execute(
            text(
                "UPDATE organization_settings SET quote_number_pattern = :m "
                "WHERE organization_id = :o"
            ),
            {"m": "FACT-{sequenc}", "o": identite["organization_id"]},
        )
        session.commit()

    refus = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert refus.status_code == 409, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "quote_number_pattern_invalid"
    assert "{sequenc}" in detail["message"]
    assert "réglages" in detail["message"]

    historique = seeded_client.get(
        f"/api/v1/projects/{pret['estimation']['project_id']}/issued-quotes", headers=admin
    ).json()
    assert historique == [], "un numéro de secours a tout de même été servi"


def test_un_motif_valide_configure_est_bien_celui_qui_est_imprime(
    seeded_client: TestClient, admin, pret
) -> None:
    assert _regler(seeded_client, admin, "DUB/{year}/{sequence:03d}").status_code == 200
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert devis.status_code == 201, devis.text
    annee = devis.json()["issued_at"][:4]
    assert devis.json()["number"] == f"DUB/{annee}/001"
