"""Les deux portes qui séparent un devis client des coûts internes.

Le dossier de menaces affirme que ``quote.html`` ne montre le déboursé sec,
le prix de revient et la marge que si deux conditions sont réunies :

* l'entreprise a explicitement activé ``show_internal_costs_in_client_pdf`` ;
* l'appelant possède ``cost:read``.

Avant ce fichier, aucun test ne passait le réglage à vrai. Deux mutations
distinctes survivaient donc à la suite complète : remplacer la décision par
``False`` (la fonction ne peut plus être activée), ou retirer le contrôle de
permission (le réglage suffit alors à divulguer les coûts).

La matrice actuelle accorde par coïncidence ``cost:read`` à tous les rôles qui
peuvent exporter un devis client. Le second test retire cette seule permission
du rôle estimateur, sans lui retirer ``export:client`` : chaque moitié du
``and`` devient ainsi observable indépendamment, comme elle devra l'être si la
matrice évolue.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from metreo_api.security.roles import ROLE_PERMISSIONS, Permission, Role

from .conftest import login

INTERNAL_HEADERS = ("Déboursé sec", "Prix de revient", "Marge")


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def quote_url(seeded_client: TestClient, admin: dict[str, str]) -> str:
    estimate = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=admin
    ).json()[0]
    return f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/quote.html"


def _set_internal_costs(client: TestClient, admin: dict[str, str], *, enabled: bool) -> None:
    response = client.patch(
        "/api/v1/organization/settings",
        headers=admin,
        json={"show_internal_costs_in_client_pdf": enabled},
    )
    assert response.status_code == 200, response.text
    assert response.json()["show_internal_costs_in_client_pdf"] is enabled


def _assert_internal_headers(html: str, *, present: bool) -> None:
    for header in INTERNAL_HEADERS:
        assert (header in html) is present, header


def test_the_company_switch_really_adds_and_removes_internal_costs(
    seeded_client: TestClient, admin: dict[str, str], quote_url: str
) -> None:
    """Les deux états sont contrôlés : une fonction toujours éteinte échoue aussi."""
    by_default = seeded_client.get(quote_url, headers=admin)
    assert by_default.status_code == 200, by_default.text
    _assert_internal_headers(by_default.text, present=False)

    _set_internal_costs(seeded_client, admin, enabled=True)
    enabled = seeded_client.get(quote_url, headers=admin)
    assert enabled.status_code == 200, enabled.text
    _assert_internal_headers(enabled.text, present=True)

    _set_internal_costs(seeded_client, admin, enabled=False)
    disabled_again = seeded_client.get(quote_url, headers=admin)
    assert disabled_again.status_code == 200, disabled_again.text
    _assert_internal_headers(disabled_again.text, present=False)


def test_the_company_switch_never_overrides_the_cost_permission(
    seeded_client: TestClient,
    admin: dict[str, str],
    quote_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'export reste utilisable, mais ses trois colonnes sensibles disparaissent."""
    _set_internal_costs(seeded_client, admin, enabled=True)

    limited_estimator = ROLE_PERMISSIONS[Role.ESTIMATOR] - {Permission.COST_READ}
    assert Permission.EXPORT_CLIENT in limited_estimator
    assert Permission.COST_READ not in limited_estimator
    monkeypatch.setitem(ROLE_PERMISSIONS, Role.ESTIMATOR, limited_estimator)

    estimator = login(seeded_client, "metreur@dubois.demo")
    response = seeded_client.get(quote_url, headers=estimator)
    assert response.status_code == 200, response.text
    _assert_internal_headers(response.text, present=False)
