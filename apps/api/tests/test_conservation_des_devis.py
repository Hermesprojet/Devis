"""Un devis émis ne disparaît pas, et son PDF ne se retrouve jamais seul.

Ces cinq suppressions ont d'abord été REPRODUITES sur une base jetable, avant
toute correction. Quatre d'entre elles emportaient le devis sans un mot et
laissaient son fichier sur le volume :

    suppression physique du projet    → DEVIS PERDU, PDF ORPHELIN
    suppression de l'estimation       → DEVIS PERDU, PDF ORPHELIN
    suppression de la version gelée   → DEVIS PERDU, PDF ORPHELIN
    suppression du client             → refusée
    suppression directe du devis      → DEVIS PERDU, PDF ORPHELIN

Un devis remis est la trace de ce qu'une entreprise a envoyé à un client.
Le perdre en supprimant un chantier n'est pas un effet de bord acceptable : ni
l'entreprise ni le client ne peuvent plus dire ce qui a été proposé, alors que
l'audit continue d'affirmer l'émission.

La révision `e3f4a5b6c7d8` pose donc `RESTRICT` sur les trois parents et un
déclencheur sur la table. Ce fichier vérifie les cinq cas, plus les deux
frontières : les suppressions MÉTIER continuent de fonctionner, et la purge
d'une organisation entière est refusée elle aussi depuis `a5b6c7d8e9fa` —
elle ne passe plus que par le registre de `services/conservation.py`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from metreo_api.config import get_settings
from metreo_api.db import get_session_factory

from .conftest import login
from .emission import emettre, fiche, geler, prix_manquant, rattacher


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def devis(seeded_client: TestClient, admin) -> dict:
    """Un devis émis, avec son chantier, sa version gelée et sa fiche client."""
    estimation = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]
    prix_manquant(seeded_client, admin, estimation)
    destinataire = fiche(seeded_client, admin)
    rattacher(seeded_client, admin, estimation["project_id"], destinataire["id"])
    geler(seeded_client, admin, estimation, version)
    reponse = emettre(seeded_client, admin, estimation, version)
    assert reponse.status_code == 201, reponse.text
    return {
        **reponse.json(),
        "project_id": estimation["project_id"],
        "estimate_id": estimation["id"],
        "version_id": version["id"],
        "client_id": destinataire["id"],
    }


def _supprimer(sql: str, **parametres: str) -> str | None:
    """Rend le nom de l'erreur si la base refuse, `None` si elle accepte."""
    with get_session_factory()() as session:
        try:
            session.execute(text(sql), parametres)
            session.commit()
        except DatabaseError as erreur:
            session.rollback()
            return type(erreur.orig).__name__ if erreur.orig else type(erreur).__name__
    return None


def _intact(devis: dict) -> None:
    """La ligne, le fichier et l'empreinte, tous les trois et ensemble."""
    with get_session_factory()() as session:
        ligne = session.execute(
            text("SELECT pdf_storage_key, pdf_sha256 FROM issued_quotes WHERE id = :i"),
            {"i": devis["id"]},
        ).first()
    assert ligne is not None, "la ligne du devis émis a disparu"

    fichier = Path(get_settings().storage_root) / ligne.pdf_storage_key
    assert fichier.is_file(), f"le PDF du devis a disparu du volume : {ligne.pdf_storage_key}"
    assert hashlib.sha256(fichier.read_bytes()).hexdigest() == devis["pdf_sha256"], (
        "le PDF conservé n'est plus celui qui a été émis"
    )


def _pdfs_du_volume() -> list[Path]:
    return sorted(p for p in Path(get_settings().storage_root).rglob("*.pdf") if p.is_file())


# --------------------------------------------------------------------------
# Les cinq suppressions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cas", "sql", "cle"),
    [
        ("le chantier", "DELETE FROM projects WHERE id = :i", "project_id"),
        ("l'estimation", "DELETE FROM estimates WHERE id = :i", "estimate_id"),
        ("la version gelée", "DELETE FROM estimate_versions WHERE id = :i", "version_id"),
        ("la fiche client", "DELETE FROM clients WHERE id = :i", "client_id"),
        ("le devis lui-même", "DELETE FROM issued_quotes WHERE id = :i", "id"),
    ],
)
def test_supprimer_un_parent_est_refuse_et_ne_perd_ni_ligne_ni_fichier(
    seeded_client: TestClient, admin, devis, cas: str, sql: str, cle: str
) -> None:
    avant = _pdfs_du_volume()
    erreur = _supprimer(sql, i=devis[cle])
    assert erreur is not None, f"supprimer {cas} a été ACCEPTÉ : le devis émis est perdu"
    _intact(devis)
    assert _pdfs_du_volume() == avant, "le volume a changé alors que la suppression a échoué"


def test_le_refus_de_supprimer_le_devis_porte_un_motif_lisible(
    seeded_client: TestClient, admin, devis
) -> None:
    """Le message vient de la base : c'est lui que l'API traduira en 409."""
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError) as capture:
            session.execute(text("DELETE FROM issued_quotes WHERE id = :i"), {"i": devis["id"]})
            session.commit()
        session.rollback()
    assert "issued_quote_conserve" in str(capture.value)


# --------------------------------------------------------------------------
# Ce qui doit continuer de marcher
# --------------------------------------------------------------------------


def test_la_suppression_metier_du_chantier_reste_logique(
    seeded_client: TestClient, admin, devis
) -> None:
    """`DELETE /projects/{id}` marque `deleted_at` : il n'atteint pas la base.

    C'est la condition qui rend la garantie tenable sans rien casser : ce que
    l'utilisateur appelle « supprimer un chantier » n'a jamais été une
    suppression physique, et le devis remis reste lisible.
    """
    reponse = seeded_client.delete(f"/api/v1/projects/{devis['project_id']}", headers=admin)
    assert reponse.status_code == 204, reponse.text
    _intact(devis)


def test_archiver_la_fiche_client_reste_refuse_tant_qu_elle_sert(
    seeded_client: TestClient, admin, devis
) -> None:
    refus = seeded_client.delete(f"/api/v1/clients/{devis['client_id']}", headers=admin)
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "client_referenced"
    assert refus.json()["detail"]["issued_quotes"] >= 1
    _intact(devis)


def test_purger_l_organisation_entiere_est_desormais_refuse_aussi(
    seeded_client: TestClient, admin, devis
) -> None:
    """La dernière porte s'est fermée, et ce test dit dans quel sens.

    Il affirmait l'inverse : « la purge d'une organisation reste possible,
    parce que la politique de conservation n'est pas décidée ». Elle l'est
    depuis `a5b6c7d8e9fa`, et elle interdit précisément ce que ce test
    constatait — un devis effacé sans un mot et un PDF laissé sur le volume.

    La destruction d'une organisation reste possible, mais plus par ce
    chemin-là : elle passe par `services/conservation.py`, qui inscrit ce
    qu'elle va détruire avant de le détruire. Ce parcours est éprouvé par
    `test_purge_encadree.py` ; ici on vérifie seulement que la porte dérobée
    est bien condamnée.
    """
    identite = seeded_client.get("/api/v1/auth/me", headers=admin).json()
    erreur = _supprimer("DELETE FROM organizations WHERE id = :i", i=identite["organization_id"])
    assert erreur is not None, "la cascade silencieuse est revenue"
    with get_session_factory()() as session:
        reste = session.execute(
            text("SELECT COUNT(*) FROM issued_quotes WHERE id = :i"), {"i": devis["id"]}
        ).scalar()
    assert reste == 1
