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


def test_le_verrou_de_sequence_serialise_deux_numerotations_concurrentes(
    seeded: dict[str, str],
) -> None:
    """Deux connexions numérotent EN MÊME TEMPS et repartent avec deux numéros.

    C'est le seul endroit où cette garantie se prouve. SQLite n'a pas de
    `SELECT … FOR UPDATE` : il n'y montrerait que le dernier rempart —
    `uq_issued_quote_number` rejetant la perdante, éprouvé dans
    `test_devis_emis.py`. Ici, le verrou fait mieux que rejeter : la seconde
    connexion ATTEND, relit un maximum désormais validé, et obtient le rang
    suivant. Les deux émissions aboutissent, avec deux numéros distincts.

    Le verrou porte sur la ligne `Organization`, dans le mode déjà utilisé par
    la séquence d'audit (`FOR NO KEY UPDATE`). Réutiliser la même ligne et le
    même mode est ce qui garantit l'absence de nouvel ordre de verrouillage,
    donc de nouvel interblocage — voir `services/locking.py`.
    """
    import threading

    from metreo_api.db import get_session_factory
    from metreo_api.models import Organization
    from metreo_api.services import issuance

    with get_session_factory()() as lecture:
        organisation = lecture.scalars(select(Organization.id)).first()
    assert organisation

    quand = issuance.maintenant()
    motif = "DEV-{year}-{sequence:04d}"
    #: `a_pris_le_verrou` sépare les deux étapes : la seconde connexion ne
    #: démarre qu'une fois la première DÉJÀ sous verrou. Sans cela, les deux
    #: pourraient s'exécuter l'une après l'autre et le test passerait sans
    #: jamais avoir mis le verrou à l'épreuve.
    a_pris_le_verrou = threading.Event()
    resultats: dict[str, tuple[str, int, int]] = {}
    erreurs: list[BaseException] = []

    def premiere() -> None:
        try:
            with get_session_factory()() as session:
                resultats["a"] = issuance.numeroter(
                    session, organization_id=organisation, motif=motif, quand=quand
                )
                a_pris_le_verrou.set()
                # Le verrou tient jusqu'à la validation : la seconde connexion
                # attend ici, et non pas « peut-être ».
                threading.Event().wait(0.5)
                session.commit()
        except BaseException as erreur:  # remonté par le fil principal
            erreurs.append(erreur)
            a_pris_le_verrou.set()

    def seconde() -> None:
        try:
            assert a_pris_le_verrou.wait(timeout=30)
            with get_session_factory()() as session:
                resultats["b"] = issuance.numeroter(
                    session, organization_id=organisation, motif=motif, quand=quand
                )
                session.commit()
        except BaseException as erreur:
            erreurs.append(erreur)

    fils = [threading.Thread(target=premiere), threading.Thread(target=seconde)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=60)
        assert not fil.is_alive(), "une numérotation ne s'est jamais terminée"

    assert not erreurs, erreurs
    assert set(resultats) == {"a", "b"}
    numeros = {resultats["a"][0], resultats["b"][0]}
    assert len(numeros) == 2, f"le même numéro a été servi deux fois : {resultats}"
