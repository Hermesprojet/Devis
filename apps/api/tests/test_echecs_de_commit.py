"""Ce qui arrive quand la validation échoue — et ce qui ne doit surtout pas.

Un `commit` peut échouer bien après que la fonction de route a composé sa
réponse : contrainte constatée au flush, connexion perdue, conflit de
sérialisation. La question n'est pas de l'éviter — elle est de savoir qui
l'apprend. Tant que la validation avait lieu après l'envoi, la réponse était
déjà partie : le client gardait un 201 et un identifiant qui ne désignaient
rien.

Ces tests injectent l'échec au moment exact du commit et vérifient les quatre
conséquences : pas de 2xx, pas de donnée métier partielle, pas d'audit
orphelin, et une session suivante qui repart proprement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from metreo_api.models import AuditEvent, DocumentRevision, Project


@pytest.fixture()
def client_neuf(seeded: dict[str, str]) -> Iterator[TestClient]:
    from metreo_api.main import create_app

    # `raise_server_exceptions=False` : on veut voir ce que voit un vrai client
    # HTTP — un code de retour — et non l'exception que `TestClient` relance par
    # commodité. C'est tout le sujet : le client ne doit pas recevoir 2xx.
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def _entete(client: TestClient) -> dict[str, str]:
    reponse = client.post("/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"})
    return {"Authorization": f"Bearer {reponse.json()['access_token']}"}


def _lire(sonde: Callable[[Session], Any]) -> Any:
    """Ce qu'une session indépendante voit, une fois la requête terminée."""
    from metreo_api.db import get_session_factory

    with get_session_factory()() as session:
        return sonde(session)


class _CommitQuiEchoue:
    """Remplace `Session.commit` par une panne, une seule fois.

    Une seule fois, et non pour toujours : la suite du test doit pouvoir
    recommencer proprement, ce qui est précisément ce qu'on veut prouver.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, erreur: BaseException) -> None:
        self.appels = 0
        vrai = Session.commit

        def commit(session: Session) -> None:
            self.appels += 1
            if self.appels == 1:
                raise erreur
            vrai(session)

        monkeypatch.setattr(Session, "commit", commit)


def _erreur_integrite() -> IntegrityError:
    return IntegrityError(
        "INSERT …", {}, Exception("duplicate key value violates unique constraint")
    )


def _erreur_connexion() -> OperationalError:
    return OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))


def _erreur_serialisation() -> OperationalError:
    return OperationalError(
        "COMMIT", {}, Exception("could not serialize access due to concurrent update")
    )


@pytest.mark.parametrize(
    ("nom", "fabriquer"),
    [
        ("contrainte d'intégrité", _erreur_integrite),
        ("connexion perdue", _erreur_connexion),
        ("conflit de sérialisation", _erreur_serialisation),
    ],
)
def test_un_commit_qui_echoue_ne_rend_jamais_un_2xx(
    client_neuf, monkeypatch, nom, fabriquer
) -> None:
    entete = _entete(client_neuf)
    avant = _lire(lambda s: len(list(s.scalars(select(Project)))))
    _CommitQuiEchoue(monkeypatch, fabriquer())

    reponse = client_neuf.post(
        "/api/v1/projects",
        json={"reference": "PRJ-ECHEC", "name": f"Projet — {nom}"},
        headers=entete,
    )

    assert reponse.status_code >= 500, (
        f"Le client a reçu {reponse.status_code} alors que la validation a échoué "
        f"({nom}) : il croit son projet créé."
    )
    apres = _lire(lambda s: [p.reference for p in s.scalars(select(Project))])
    assert "PRJ-ECHEC" not in apres, "Une donnée métier partielle a survécu."
    assert len(apres) == avant


def test_un_commit_qui_echoue_ne_laisse_aucun_audit_orphelin(client_neuf, monkeypatch) -> None:
    """L'audit et la donnée vivent dans la même transaction, ou dans aucune."""
    entete = _entete(client_neuf)
    avant = _lire(lambda s: len(list(s.scalars(select(AuditEvent)))))
    _CommitQuiEchoue(monkeypatch, _erreur_integrite())

    client_neuf.post(
        "/api/v1/projects",
        json={"reference": "PRJ-AUDIT-ORPHELIN", "name": "Sans lendemain"},
        headers=entete,
    )

    apres = _lire(lambda s: [e.summary for e in s.scalars(select(AuditEvent))])
    assert len(apres) == avant, "Un événement d'audit affirme une opération qui a été annulée."
    assert not any("PRJ-AUDIT-ORPHELIN" in (resume or "") for resume in apres)


def test_apres_un_echec_la_tentative_suivante_aboutit(client_neuf, monkeypatch) -> None:
    """Rien ne reste coincé : ni la session, ni le client, ni la référence."""
    entete = _entete(client_neuf)
    _CommitQuiEchoue(monkeypatch, _erreur_connexion())

    premier = client_neuf.post(
        "/api/v1/projects",
        json={"reference": "PRJ-REPRISE", "name": "Première tentative"},
        headers=entete,
    )
    assert premier.status_code >= 500

    second = client_neuf.post(
        "/api/v1/projects",
        json={"reference": "PRJ-REPRISE", "name": "Seconde tentative"},
        headers=entete,
    )
    assert second.status_code == 201, second.text
    assert _lire(
        lambda s: [p.name for p in s.scalars(select(Project)) if p.reference == "PRJ-REPRISE"]
    ) == ["Seconde tentative"]


def test_un_audit_qui_echoue_annule_l_ecriture_metier(client_neuf, monkeypatch) -> None:
    """L'audit n'est pas un à-côté : sans lui, l'écriture n'a pas eu lieu."""
    from metreo_api.services import audit

    entete = _entete(client_neuf)
    avant = _lire(lambda s: len(list(s.scalars(select(Project)))))

    def refuser(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("chaîne d'audit indisponible")

    monkeypatch.setattr(audit, "record", refuser)
    reponse = client_neuf.post(
        "/api/v1/projects",
        json={"reference": "PRJ-SANS-AUDIT", "name": "Non tracé"},
        headers=entete,
    )

    assert reponse.status_code >= 500
    assert _lire(lambda s: len(list(s.scalars(select(Project))))) == avant


# ---------------------------------------------------------------------------
# Base et système de fichiers : deux ressources, une seule vérité
# ---------------------------------------------------------------------------


def _document(client: TestClient, entete: dict[str, str], reference: str) -> str:
    projet = client.post(
        "/api/v1/projects", json={"reference": reference, "name": "Chantier"}, headers=entete
    ).json()
    return client.post(
        f"/api/v1/projects/{projet['id']}/documents", json={"title": "CCTP"}, headers=entete
    ).json()["id"]


def _originaux(volume) -> list[str]:
    return sorted(str(f.relative_to(volume)) for f in volume.rglob("*") if f.is_file())


PDF = b"%PDF-1.7\nun original\n%%EOF\n"


def test_un_commit_qui_echoue_ne_laisse_pas_l_original_sur_le_volume(
    client_neuf, monkeypatch, tmp_path
) -> None:
    """Le cas « fichier écrit, puis commit en échec ».

    PostgreSQL et le volume ne partagent aucune transaction : c'est la
    compensation enregistrée au moment de l'écriture qui retire les octets.
    """
    from metreo_api.config import get_settings

    volume = tmp_path / "volume"
    monkeypatch.setattr(get_settings(), "storage_root", str(volume), raising=False)

    entete = _entete(client_neuf)
    document = _document(client_neuf, entete, "PRJ-FICHIER-ECHEC")
    _CommitQuiEchoue(monkeypatch, _erreur_integrite())

    reponse = client_neuf.post(
        f"/api/v1/documents/{document}/revisions",
        files={"file": ("cctp.pdf", PDF, "application/pdf")},
        headers=entete,
    )

    assert reponse.status_code >= 500, reponse.text
    assert _originaux(volume) == [], (
        "Un original est resté sur le volume sans révision pour le nommer : "
        "la sauvegarde l'emporterait, et personne ne pourrait plus le retrouver."
    )
    assert _lire(lambda s: list(s.scalars(select(DocumentRevision)))) == []


def test_un_refus_metier_ne_laisse_pas_l_original_sur_le_volume(
    client_neuf, monkeypatch, tmp_path
) -> None:
    """Le cas « ligne refusée après écriture du fichier » : le doublon."""
    from metreo_api.config import get_settings

    volume = tmp_path / "volume"
    monkeypatch.setattr(get_settings(), "storage_root", str(volume), raising=False)

    entete = _entete(client_neuf)
    document = _document(client_neuf, entete, "PRJ-DOUBLON")

    premier = client_neuf.post(
        f"/api/v1/documents/{document}/revisions",
        files={"file": ("cctp.pdf", PDF, "application/pdf")},
        headers=entete,
    )
    assert premier.status_code == 201, premier.text
    apres_premier = _originaux(volume)
    assert len(apres_premier) == 1

    second = client_neuf.post(
        f"/api/v1/documents/{document}/revisions",
        files={"file": ("cctp.pdf", PDF, "application/pdf")},
        headers=entete,
    )
    assert second.status_code == 409, second.text
    assert _originaux(volume) == apres_premier, (
        "Le second dépôt, refusé, a laissé ses octets derrière lui."
    )


def test_un_audit_qui_echoue_retire_aussi_l_original(client_neuf, monkeypatch, tmp_path) -> None:
    """Le cas « audit en échec après écriture du fichier »."""
    from metreo_api.config import get_settings
    from metreo_api.services import audit

    volume = tmp_path / "volume"
    monkeypatch.setattr(get_settings(), "storage_root", str(volume), raising=False)

    entete = _entete(client_neuf)
    document = _document(client_neuf, entete, "PRJ-AUDIT-FICHIER")

    def refuser(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("chaîne d'audit indisponible")

    monkeypatch.setattr(audit, "record", refuser)
    reponse = client_neuf.post(
        f"/api/v1/documents/{document}/revisions",
        files={"file": ("cctp.pdf", PDF, "application/pdf")},
        headers=entete,
    )

    assert reponse.status_code >= 500
    assert _originaux(volume) == []
    assert _lire(lambda s: list(s.scalars(select(DocumentRevision)))) == []


def test_aucun_temporaire_ne_survit_a_un_echec(client_neuf, monkeypatch, tmp_path) -> None:
    """Ni original publié, ni fichier `.depot-…` en cours de route."""
    from metreo_api.config import get_settings

    volume = tmp_path / "volume"
    monkeypatch.setattr(get_settings(), "storage_root", str(volume), raising=False)

    entete = _entete(client_neuf)
    document = _document(client_neuf, entete, "PRJ-TEMPORAIRE")
    _CommitQuiEchoue(monkeypatch, _erreur_connexion())

    client_neuf.post(
        f"/api/v1/documents/{document}/revisions",
        files={"file": ("cctp.pdf", PDF, "application/pdf")},
        headers=entete,
    )

    restes = [str(f) for f in volume.rglob(".depot-*")]
    assert restes == [], f"Temporaires abandonnés : {restes}"
    assert _originaux(volume) == []
