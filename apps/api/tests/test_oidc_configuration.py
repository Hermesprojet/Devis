"""La configuration décide, et elle refuse fermé.

Une application déployée sans parcours de connexion démarre, répond aux
contrôles de santé, et se révèle inutilisable au premier utilisateur. Ces
tests font de cette situation une erreur visible au démarrage.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from metreo_api.config import Settings


def _reglages(**surcharge) -> Settings:
    base = {
        "environment": "staging",
        "auth_mode": "oidc",
        "jwt_secret": "secret-de-test-sans-valeur-0123456789",
        "database_url": "postgresql+psycopg://x:y@localhost:5432/z",
        "oidc_issuer": "https://issuer.example.invalid",
        "oidc_client_id": "metreo-staging",
        "oidc_client_secret": "secret-de-test",
        "oidc_redirect_uri": "https://app.example.invalid/connexion",
    }
    base.update(surcharge)
    return Settings(**base)


def test_a_complete_staging_configuration_reports_no_problem() -> None:
    assert _reglages().validate_startup() == []


@pytest.mark.parametrize(
    "manquant",
    ["oidc_issuer", "oidc_client_id", "oidc_client_secret", "oidc_redirect_uri"],
)
def test_each_missing_oidc_value_is_named(manquant: str) -> None:
    """Le message nomme ce qui manque : chercher soi-même coûte une soirée."""
    problemes = _reglages(**{manquant: ""}).validate_startup()
    assert any(manquant in probleme for probleme in problemes), problemes


def test_a_jwt_only_deployment_is_legal_but_says_it_has_no_browser_login() -> None:
    """`jwt` reste légitime, et l'absence de parcours devient visible.

    Une intégration machine à machine présente ses jetons sans passer par un
    navigateur : interdire ce mode aurait été une décision de trop. Ce qui
    manquait, c'est que la différence se voie — un déploiement destiné à des
    humains et configuré en `jwt` démarrait sans que rien ne signale que
    personne ne pourrait s'y connecter.
    """
    reglages = _reglages(auth_mode="jwt")
    assert reglages.validate_startup() == []
    assert reglages.browser_login_available is False

    assert _reglages().browser_login_available is True


def test_development_has_a_browser_login_through_dev_mode() -> None:
    assert Settings(environment="development", auth_mode="dev").browser_login_available is True


def test_dev_mode_offers_no_browser_login_outside_development() -> None:
    """Il est refusé de toute façon : il ne compte pas comme un parcours."""
    reglages = _reglages(auth_mode="dev", jwt_secret="x" * 40)
    assert reglages.browser_login_available is False


def test_staging_still_refuses_dev_mode_and_sqlite() -> None:
    problemes = _reglages(
        auth_mode="dev", database_url="sqlite+pysqlite:///./var/x.sqlite3", jwt_secret=""
    ).validate_startup()
    assert any("auth_mode=dev is forbidden" in p for p in problemes)
    assert any("sqlite is not supported" in p for p in problemes)
    assert any("jwt_secret must be set" in p for p in problemes)


def test_development_stays_usable_without_any_oidc_configuration() -> None:
    """Le mode local ne demande rien : c'est ce qui le rend utile."""
    assert Settings(environment="development", auth_mode="dev").validate_startup() == []


def test_oidc_mode_without_configuration_is_refused_even_in_development() -> None:
    """Demander OIDC sans le configurer est une erreur partout.

    Le refus n'est pas propre à la production : un développeur qui bascule en
    OIDC sans les valeurs doit l'apprendre au démarrage, pas au premier clic.
    """
    problemes = Settings(environment="development", auth_mode="oidc").validate_startup()
    assert any("auth_mode=oidc requires" in probleme for probleme in problemes), problemes


# -- les routes suivent la configuration -----------------------------------


def test_the_oidc_routes_do_not_exist_without_configuration(
    seeded_client: TestClient,
) -> None:
    """Sans configuration, ces routes n'existent pas — 404, pas 500.

    Annoncer « mal configuré » à un visiteur anonyme le renseigne sur
    l'installation sans rien lui apporter de légitime.
    """
    for chemin in ("/api/v1/auth/oidc/start", "/api/v1/auth/oidc/callback"):
        reponse = seeded_client.get(chemin)
        assert reponse.status_code == 404, chemin
        assert reponse.json()["detail"]["code"] == "oidc_disabled"

    echange = seeded_client.post("/api/v1/auth/oidc/exchange", json={"login_code": "x" * 32})
    assert echange.status_code == 404
    assert echange.json()["detail"]["code"] == "oidc_disabled"


def test_dev_login_is_unreachable_outside_development(seeded_client: TestClient) -> None:
    """Le seul parcours restant hors développement doit être OIDC."""
    from metreo_api.config import get_settings

    reglages = get_settings()
    ancien = reglages.environment
    try:
        object.__setattr__(reglages, "environment", "staging")
        reponse = seeded_client.post("/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"})
        assert reponse.status_code == 404
        assert reponse.json()["detail"]["code"] == "dev_login_disabled"
    finally:
        object.__setattr__(reglages, "environment", ancien)


OIDC_COMPLET = {
    "auth_mode": "oidc",
    "oidc_issuer": "https://issuer.example.invalid",
    "oidc_client_id": "metreo-staging",
    "oidc_client_secret": "secret-de-test",
    "oidc_redirect_uri": "https://app.example.invalid/connexion",
}


def _annonce(client: TestClient, monkeypatch: pytest.MonkeyPatch, **surcharge: object) -> list[str]:
    """Ce que /health annonce comme moyens de connexion, réglages donnés."""
    from metreo_api.config import get_settings

    reglages = get_settings()
    for nom, valeur in surcharge.items():
        monkeypatch.setattr(reglages, nom, valeur, raising=False)
    reponse = client.get("/api/v1/health")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["login_methods"]


def test_health_announces_oidc_when_the_provider_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'écran de connexion doit savoir quoi proposer avant d'afficher.

    Sans cette annonce, la page devine — et une page qui devine finit par
    offrir un formulaire de développement sur un déploiement qui le refuse.
    """
    assert _annonce(client, monkeypatch, **OIDC_COMPLET) == ["oidc"]


def test_health_announces_nothing_when_oidc_is_incomplete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuration partielle : rien d'annoncé, cohérent avec le refus fermé.

    Annoncer `oidc` ici enverrait l'utilisateur sur un parcours qui répond 404.
    """
    partiel = {**OIDC_COMPLET, "oidc_client_secret": ""}
    assert _annonce(client, monkeypatch, **partiel) == []


def test_health_announces_nothing_on_a_machine_to_machine_deployment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`jwt` accepte des jetons sans en émettre : aucun parcours à proposer."""
    assert _annonce(client, monkeypatch, **{**OIDC_COMPLET, "auth_mode": "jwt"}) == []


def test_health_announces_dev_only_outside_production(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La connexion de développement ne s'annonce jamais en production.

    C'est la même règle que le refus au démarrage, vue depuis l'écran : le
    bouton ne doit pas exister là où la route refuserait.
    """
    assert _annonce(client, monkeypatch, auth_mode="dev", environment="development") == ["dev"]
    assert _annonce(client, monkeypatch, auth_mode="dev", environment="production") == []
