"""Le parcours complet par HTTP : /start, /callback, /exchange.

Ces tests montent l'application en mode OIDC contre le faux fournisseur, et
suivent le même chemin qu'un navigateur. Ils vérifient en particulier deux
choses que les tests unitaires ne peuvent pas montrer :

  - **le jeton n'apparaît jamais dans une URL** ;
  - **le code de connexion ne sert qu'une fois.**
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from metreo_api.config import get_settings
from metreo_api.models import LoginTransaction

from .fake_oidc import FakeProvider

REDIRECT = "https://app.example.invalid/connexion"


@pytest.fixture()
def oidc_client(seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """L'application en mode OIDC, câblée sur le faux fournisseur."""
    provider = FakeProvider()
    reglages = get_settings()
    for nom, valeur in (
        ("auth_mode", "oidc"),
        ("oidc_issuer", provider.issuer),
        ("oidc_client_id", provider.client_id),
        ("oidc_client_secret", provider.client_secret),
        ("oidc_redirect_uri", REDIRECT),
    ):
        monkeypatch.setattr(reglages, nom, valeur, raising=False)

    # Tout appel HTTP sortant de l'API passe par le transport simulé.
    transport = provider.transport()
    vrai_client = httpx.Client

    def client_simule(*args, **kwargs):
        kwargs.setdefault("timeout", 10.0)
        kwargs["transport"] = transport
        return vrai_client(*args, **kwargs)

    monkeypatch.setattr("metreo_api.services.oidc.httpx.Client", client_simule)
    monkeypatch.setattr(
        "metreo_api.services.oidc.PyJWKClient",
        lambda *a, **k: provider.jwk_client(),
    )
    return seeded_client, provider


def _etat_courant(nom: str = "state") -> str:
    from metreo_api.db import get_session_factory

    with get_session_factory()() as session:
        transaction = session.scalars(
            select(LoginTransaction).order_by(LoginTransaction.created_at.desc())
        ).first()
        assert transaction is not None
        return getattr(transaction, nom)


def test_start_returns_the_provider_url(oidc_client) -> None:
    client, provider = oidc_client
    reponse = client.get("/api/v1/auth/oidc/start")
    assert reponse.status_code == 200, reponse.text
    url = reponse.json()["authorization_url"]
    assert url.startswith(f"{provider.issuer}/authorize")
    assert "code_challenge_method=S256" in url


def test_start_refuses_an_external_return_to(oidc_client) -> None:
    """Pas de redirection ouverte : seul un chemin interne est accepté."""
    client, _ = oidc_client
    client.get("/api/v1/auth/oidc/start", params={"return_to": "https://ailleurs.invalid/x"})
    from metreo_api.db import get_session_factory

    with get_session_factory()() as session:
        transaction = session.scalars(
            select(LoginTransaction).order_by(LoginTransaction.created_at.desc())
        ).first()
    assert transaction.return_to is None

    client.get("/api/v1/auth/oidc/start", params={"return_to": "/projets"})
    with get_session_factory()() as session:
        transaction = session.scalars(
            select(LoginTransaction).order_by(LoginTransaction.created_at.desc())
        ).first()
    assert transaction.return_to == "/projets"


def test_the_full_flow_never_puts_a_token_in_a_url(oidc_client) -> None:
    """Le point qui compte : l'URL de retour ne porte qu'un code opaque.

    Un JWT dans une URL se retrouve dans l'historique du navigateur, les
    journaux du proxy et l'en-tête `Referer` de la page suivante.
    """
    client, provider = oidc_client
    client.get("/api/v1/auth/oidc/start")
    state, nonce = _etat_courant("state"), _etat_courant("nonce")
    provider.authorize(
        "code-http", subject="s-http", email="admin@dubois.demo", email_verified=True, nonce=nonce
    )

    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "code-http", "state": state},
        follow_redirects=False,
    )
    assert retour.status_code == 303, retour.text
    destination = retour.headers["location"]
    parametres = parse_qs(urlsplit(destination).query)

    code_de_connexion = parametres["login_code"][0]
    assert destination.startswith(REDIRECT)
    # Un JWT porte deux points ; le code de connexion, aucun.
    assert code_de_connexion.count(".") == 0
    assert "access_token" not in destination and "id_token" not in destination

    echange = client.post("/api/v1/auth/oidc/exchange", json={"login_code": code_de_connexion})
    assert echange.status_code == 200, echange.text
    jeton = echange.json()["access_token"]
    assert jeton.count(".") == 2

    # Et il fonctionne réellement.
    profil = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {jeton}"})
    assert profil.status_code == 200
    assert profil.json()["email"] == "admin@dubois.demo"


def test_a_login_code_cannot_be_exchanged_twice(oidc_client) -> None:
    client, provider = oidc_client
    client.get("/api/v1/auth/oidc/start")
    state, nonce = _etat_courant("state"), _etat_courant("nonce")
    provider.authorize(
        "code-rejeu", subject="s-rejeu", email="admin@dubois.demo", email_verified=True, nonce=nonce
    )
    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "code-rejeu", "state": state},
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(retour.headers["location"]).query)["login_code"][0]

    assert client.post("/api/v1/auth/oidc/exchange", json={"login_code": code}).status_code == 200
    second = client.post("/api/v1/auth/oidc/exchange", json={"login_code": code})
    assert second.status_code == 401
    assert second.json()["detail"]["code"] == "invalid_login_code"


def test_a_tampered_state_comes_back_with_an_error_code(oidc_client) -> None:
    client, provider = oidc_client
    client.get("/api/v1/auth/oidc/start")
    provider.authorize("code-x", nonce=_etat_courant("nonce"))
    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "code-x", "state": "un-state-fabrique"},
        follow_redirects=False,
    )
    assert retour.status_code == 303
    parametres = parse_qs(urlsplit(retour.headers["location"]).query)
    assert parametres["login_error"] == ["invalid_state"]
    assert "login_code" not in parametres


def test_a_provider_refusal_comes_back_as_a_refusal(oidc_client) -> None:
    client, _ = oidc_client
    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"error": "access_denied", "state": "x"},
        follow_redirects=False,
    )
    parametres = parse_qs(urlsplit(retour.headers["location"]).query)
    assert parametres["login_error"] == ["provider_refused"]


def test_an_unknown_user_never_obtains_a_login_code(oidc_client) -> None:
    client, provider = oidc_client
    client.get("/api/v1/auth/oidc/start")
    state, nonce = _etat_courant("state"), _etat_courant("nonce")
    provider.authorize(
        "code-inconnu",
        subject="s-inconnu",
        email="personne@example.invalid",
        email_verified=True,
        nonce=nonce,
    )
    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "code-inconnu", "state": state},
        follow_redirects=False,
    )
    parametres = parse_qs(urlsplit(retour.headers["location"]).query)
    assert parametres["login_error"] == ["unknown_user"]
    assert "login_code" not in parametres


def test_a_user_in_several_organisations_must_choose(oidc_client) -> None:
    """Le choix reste explicite : personne ne travaille dans la mauvaise."""
    from metreo_api.db import get_session_factory
    from metreo_api.models import Membership, Organization, User

    client, provider = oidc_client
    with get_session_factory()() as session:
        utilisateur = session.scalars(select(User).where(User.email == "admin@dubois.demo")).one()
        autre = session.scalars(
            select(Organization).where(Organization.id != None)  # noqa: E711
        ).all()
        seconde = next(
            o for o in autre if o.id not in {m.organization_id for m in utilisateur.memberships}
        )
        session.add(
            Membership(
                user_id=utilisateur.id, organization_id=seconde.id, role="viewer", is_active=True
            )
        )
        session.commit()
        ids = {seconde.id}

    client.get("/api/v1/auth/oidc/start")
    state, nonce = _etat_courant("state"), _etat_courant("nonce")
    provider.authorize(
        "code-multi", subject="s-multi", email="admin@dubois.demo", email_verified=True, nonce=nonce
    )
    retour = client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "code-multi", "state": state},
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(retour.headers["location"]).query)["login_code"][0]

    sans_choix = client.post("/api/v1/auth/oidc/exchange", json={"login_code": code})
    assert sans_choix.status_code == 400
    assert sans_choix.json()["detail"]["code"] == "organization_required"

    avec_choix = client.post(
        "/api/v1/auth/oidc/exchange",
        json={"login_code": code, "organization_id": next(iter(ids))},
    )
    assert avec_choix.status_code == 200, avec_choix.text
    assert avec_choix.json()["organization_id"] in ids
