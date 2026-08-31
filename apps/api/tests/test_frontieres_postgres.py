"""Les frontières transactionnelles, sur un vrai PostgreSQL.

SQLite prouve la mécanique ; il ne prouve pas les frontières. Il n'a ni
sessions réellement concurrentes, ni verrous de ligne, ni la même façon de
signaler une contrainte violée. Ce fichier ne tourne donc que lorsque la suite
est branchée sur PostgreSQL, et il éprouve ce que l'autre backend ne peut pas :

  - une erreur qui n'existe qu'au FLUSH — deux requêtes concurrentes qui
    insèrent la même référence de projet ;
  - le verrou de numérotation qui sérialise deux dépôts sur un même document ;
  - le fait que la perdante reçoive une erreur, et jamais un 2xx.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from metreo_api.models import Project

from .conftest import running_on_postgresql

pytestmark = pytest.mark.skipif(
    not running_on_postgresql(),
    reason="les frontières transactionnelles se prouvent sur PostgreSQL, pas sur SQLite",
)


@pytest.fixture()
def client_neuf(seeded: dict[str, str]) -> Iterator[TestClient]:
    from metreo_api.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def _entete(client: TestClient) -> dict[str, str]:
    reponse = client.post("/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"})
    return {"Authorization": f"Bearer {reponse.json()['access_token']}"}


def _compter(sonde) -> int:
    from metreo_api.db import get_session_factory

    with get_session_factory()() as session:
        return sonde(session)


def test_une_unicite_violee_au_flush_atteint_le_client_avant_tout_succes(
    client_neuf, monkeypatch
) -> None:
    """Une erreur qui n'existe qu'au FLUSH, et le client l'apprend.

    `uq_project_org_reference` n'est vue ni par le code de la route, ni par son
    `SELECT` préalable : elle l'est quand l'`INSERT` part au serveur. Pour
    l'obtenir à coup sûr plutôt qu'au petit bonheur d'une course, une AUTRE
    connexion insère la référence JUSTE avant ce flush — c'est ce que ferait une
    seconde requête arrivée entre la vérification et l'écriture.

    Le moment est pris sur `before_flush` et non plus tard : une fois la ligne
    de la route insérée, la seconde connexion attendrait sa validation, et les
    deux s'attendraient l'une l'autre.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SessionSQL

    from metreo_api.db import get_session_factory
    from metreo_api.models import new_id

    entete = _entete(client_neuf)
    organisation = client_neuf.get("/api/v1/organization", headers=entete).json()["id"]
    fait: list[str] = []

    def devancer(session, _contexte, _instances):
        if fait or not any(
            isinstance(objet, Project) and objet.reference == "PRJ-FLUSH" for objet in session.new
        ):
            return
        fait.append("oui")
        with get_session_factory()() as autre:
            autre.add(
                Project(
                    id=new_id(),
                    organization_id=organisation,
                    reference="PRJ-FLUSH",
                    name="Insérée par une autre connexion",
                )
            )
            autre.commit()

    event.listen(SessionSQL, "before_flush", devancer)
    try:
        reponse = client_neuf.post(
            "/api/v1/projects",
            json={"reference": "PRJ-FLUSH", "name": "Perdante"},
            headers=entete,
        )
    finally:
        event.remove(SessionSQL, "before_flush", devancer)

    assert fait, "la connexion concurrente n'a pas joué : le test ne prouverait rien"
    assert reponse.status_code >= 400, (
        f"Le client a reçu {reponse.status_code} alors que son INSERT ne pouvait pas "
        "aboutir : la contrainte se constate au flush, après la décision de la route."
    )
    restants = _compter(
        lambda s: [p.name for p in s.scalars(select(Project)) if p.reference == "PRJ-FLUSH"]
    )
    assert restants == ["Insérée par une autre connexion"], restants


def test_sur_postgres_une_creation_est_visible_d_une_autre_connexion_au_moment_du_201(
    seeded: dict[str, str],
) -> None:
    """La visibilité, sur un vrai serveur, depuis une CONNEXION distincte.

    Sur SQLite, « une autre session » partage le même fichier ; l'isolation y
    est réelle mais bon marché. Sur PostgreSQL, c'est une seconde connexion en
    `READ COMMITTED` : elle ne voit rigoureusement que ce qui est validé. Si
    elle trouve le projet à l'instant où le 201 part, alors la requête suivante
    du client le trouvera aussi — c'est la garantie, prouvée là où elle compte.
    """
    from fastapi.testclient import TestClient

    from metreo_api.main import create_app

    from .observateur import enveloppe_observatrice

    enveloppe, constats = enveloppe_observatrice(
        create_app(), lambda s: [p.reference for p in s.scalars(select(Project))]
    )
    with TestClient(enveloppe) as client:
        entete = _entete(client)
        reponse = client.post(
            "/api/v1/projects",
            json={"reference": "PRJ-PG-VISIBLE", "name": "Vu d'ailleurs"},
            headers=entete,
        )
    assert reponse.status_code == 201, reponse.text
    dernier = [c for c in constats if c.methode == "POST"][-1]
    assert "PRJ-PG-VISIBLE" in dernier.vu, (
        f"Une autre connexion PostgreSQL ne voit pas encore le projet annoncé : {dernier}"
    )


def test_deux_emissions_simultanees_ne_recoivent_jamais_le_meme_numero(
    client_neuf: TestClient,
) -> None:
    """Deux émissions LANCÉES ENSEMBLE, sur la vraie route, et deux numéros.

    C'est le seul endroit où cette garantie se prouve. SQLite n'a ni
    connexions réellement concurrentes ni `SELECT … FOR UPDATE` : on n'y montre
    que le dernier rempart — `uq_issued_quote_number` rejetant la perdante,
    éprouvé dans `test_devis_emis.py`. Ici, le verrou fait mieux que rejeter :
    la seconde requête ATTEND, relit un maximum désormais validé, et repart
    avec le rang suivant. Les DEUX émissions aboutissent.

    Le verrou porte sur la ligne `Organization`, dans le mode déjà utilisé par
    la séquence d'audit (`FOR NO KEY UPDATE`). Réutiliser la même ligne et le
    même mode est ce qui garantit l'absence de nouvel ordre de verrouillage,
    donc de nouvel interblocage — voir `services/locking.py`.

    Et il faut bien la ROUTE, pas le service : c'est la transaction complète —
    verrou, numéro, ligne insérée, validation — qui rend le numéro visible à
    la suivante. Un test qui n'allouerait qu'un rang sans écrire la ligne
    verrait les deux connexions repartir avec le même, et il aurait raison.
    """
    import threading

    from .emission import emettre, fiche, geler, prix_manquant, rattacher, version_de_plus

    entete = _entete(client_neuf)
    estimation = client_neuf.get("/api/v1/estimates", headers=entete).json()[0]
    versions = client_neuf.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=entete
    ).json()

    prix_manquant(client_neuf, entete, estimation)
    rattacher(client_neuf, entete, estimation["project_id"], fiche(client_neuf, entete)["id"])
    a_emettre = [versions[0], version_de_plus(client_neuf, entete, estimation, "v2")]
    for version in a_emettre:
        geler(client_neuf, entete, estimation, version)

    depart = threading.Barrier(len(a_emettre))
    reponses: list[Any] = []
    verrou = threading.Lock()

    def emettre_en_meme_temps(version: dict) -> None:
        depart.wait(timeout=30)
        reponse = emettre(client_neuf, entete, estimation, version)
        with verrou:
            reponses.append(reponse)

    fils = [threading.Thread(target=emettre_en_meme_temps, args=(v,)) for v in a_emettre]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=120)
        assert not fil.is_alive(), "une émission ne s'est jamais terminée"

    assert len(reponses) == len(a_emettre)
    codes = sorted(r.status_code for r in reponses)
    assert codes == [201, 201], [r.text for r in reponses]
    numeros = [r.json()["number"] for r in reponses]
    assert len(set(numeros)) == 2, f"le même numéro a été servi deux fois : {numeros}"

    #: Et la base porte bien deux devis distincts, vus d'une AUTRE connexion.
    historique = client_neuf.get(
        f"/api/v1/projects/{estimation['project_id']}/issued-quotes", headers=entete
    ).json()
    assert sorted(d["number"] for d in historique) == sorted(numeros)
