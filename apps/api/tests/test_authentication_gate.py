"""Ce qu'un jeton déjà émis ne suffit plus à ouvrir.

`current_context` promet, dans son docstring, que « l'adhésion est relue à
chaque requête : révoquer une adhésion prend effet immédiatement plutôt qu'à
l'expiration du jeton ». Rien ne le vérifiait.

Mesuré, sur `main`, par une campagne de mutation sur la couche sécurité : dix
mutations, quatre tuées, six survivantes — dont les quatre de ce fichier.
Retirer le filtre `Membership.is_active`, le contrôle `user.is_active`,
l'émetteur attendu du jeton ou l'exigence du préfixe `Bearer` laissait la suite
complète verte. Les quatre gardes fonctionnent ; aucune n'était prouvée.

Chaque test vérifie d'abord que le jeton ouvre bien, puis ferme une seule
chose, puis constate le refus : sans le premier appel, un refus pourrait venir
d'un jeton qui n'a jamais marché.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from .conftest import login

UNE_ROUTE_PROTEGEE = "/api/v1/projects"


def _session() -> Any:
    from metreo_api.db import get_session_factory

    return get_session_factory()()


@pytest.fixture()
def jeton_qui_ouvre(seeded_client: TestClient) -> dict[str, str]:
    headers = login(seeded_client, "admin@dubois.demo")
    ouverture = seeded_client.get(UNE_ROUTE_PROTEGEE, headers=headers)
    assert ouverture.status_code == 200, (
        "le jeton doit ouvrir avant qu'on ferme quoi que ce soit, sinon le "
        "refus attendu plus bas ne prouverait rien"
    )
    return headers


def _identifiant_de_ladministrateur() -> str:
    from metreo_api.models import User

    session = _session()
    try:
        return str(session.scalars(select(User).where(User.email == "admin@dubois.demo")).one().id)
    finally:
        session.close()


def test_revoking_a_membership_closes_an_already_issued_token(
    seeded_client: TestClient, jeton_qui_ouvre: dict[str, str]
) -> None:
    """Révoquer prend effet à la requête suivante, pas à l'expiration."""
    from metreo_api.models import Membership

    session = _session()
    try:
        adhesion = session.scalars(
            select(Membership).where(Membership.user_id == _identifiant_de_ladministrateur())
        ).one()
        adhesion.is_active = False
        session.commit()
    finally:
        session.close()

    refus = seeded_client.get(UNE_ROUTE_PROTEGEE, headers=jeton_qui_ouvre)
    assert refus.status_code == 403
    assert refus.json()["detail"]["code"] == "no_membership"


def test_deactivating_the_account_closes_an_already_issued_token(
    seeded_client: TestClient, jeton_qui_ouvre: dict[str, str]
) -> None:
    from metreo_api.models import User

    session = _session()
    try:
        utilisateur = session.get(User, _identifiant_de_ladministrateur())
        assert utilisateur is not None
        utilisateur.is_active = False
        session.commit()
    finally:
        session.close()

    refus = seeded_client.get(UNE_ROUTE_PROTEGEE, headers=jeton_qui_ouvre)
    assert refus.status_code == 403
    assert refus.json()["detail"]["code"] == "inactive_account"


def test_a_token_from_another_issuer_is_refused_even_with_the_right_secret(
    seeded_client: TestClient, seeded: dict[str, str], jeton_qui_ouvre: dict[str, str]
) -> None:
    """Le secret n'est pas le seul verrou : l'émetteur attendu en est un aussi.

    Distinct de `test_tenant_isolation.py::test_a_token_signed_with_another_secret_is_refused`,
    qui change le secret. Ici le secret est le bon.
    """
    import jwt

    from metreo_api.config import get_settings

    settings = get_settings()
    etranger = jwt.encode(
        {
            "sub": _identifiant_de_ladministrateur(),
            "org": seeded["organization_a"],
            "iss": "un-autre-emetteur",
            "exp": 9999999999,
        },
        settings.effective_jwt_secret(),
        algorithm=settings.jwt_algorithm,
    )
    refus = seeded_client.get(UNE_ROUTE_PROTEGEE, headers={"Authorization": f"Bearer {etranger}"})
    assert refus.status_code == 401
    assert refus.json()["detail"]["code"] == "invalid_token"


def test_an_authorization_header_without_the_bearer_prefix_is_refused(
    seeded_client: TestClient, jeton_qui_ouvre: dict[str, str]
) -> None:
    """Et « Bearer » seul est refusé, pas planté.

    Sans l'exigence du préfixe, `authorization.split(" ", 1)[1]` lève
    `IndexError` sur un en-tête sans espace : le refus deviendrait un 500.
    """
    jeton_nu = jeton_qui_ouvre["Authorization"].split(" ", 1)[1]
    assert (
        seeded_client.get(UNE_ROUTE_PROTEGEE, headers={"Authorization": jeton_nu}).status_code
        == 401
    )

    tronque = seeded_client.get(UNE_ROUTE_PROTEGEE, headers={"Authorization": "Bearer"})
    assert tronque.status_code == 401, "un en-tête tronqué doit être refusé, pas provoquer un 500"
