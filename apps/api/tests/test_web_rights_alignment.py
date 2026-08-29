"""Les refus que l'interface web ignore — côté API, mesurés et fixés.

**Ce fichier ne teste pas le client web.** Il fixe le côté API du constat écrit
dans `docs/DROITS_COTE_WEB.md` : quelles commandes de la page « estimation »
l'API refuse, pour quel rôle, et avec quel code. Le document décrit ce que
l'interface en fait — c'est-à-dire rien — et pose la décision à prendre.

Sans ces tests, le document se périmerait en silence : une permission déplacée
dans `ROLE_PERMISSIONS` changerait le tableau sans que personne le voie.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from .conftest import login

#: Les quatre commandes de la barre d'outils de
#: `apps/web/src/app/estimations/[estimateId]/[versionId]/page.tsx`, et la
#: condition d'affichage réellement écrite en face de chacune.
#:
#: `export interne` est la seule dont la condition vient du serveur
#: (`includes_internal_costs`) — et la seule qui ne produit pas d'écart.
COMMANDES = {
    "export_csv": ("GET", "export.csv", "toujours"),
    "export_interne": ("GET", "export.csv?include_internal=true", "includes_internal_costs"),
    "apercu_devis": ("GET", "quote.html", "toujours"),
    "geler": ("POST", "freeze", "version non gelée"),
}

#: Ce que l'API répond, par rôle semé. `403` = l'interface offre un bouton que
#: l'API refuse. `409` = un refus métier, pas un refus de droit.
ATTENDU = {
    "admin@dubois.demo": {
        "export_csv": 200,
        "export_interne": 200,
        "apercu_devis": 200,
        "geler": 409,
    },
    "metreur@dubois.demo": {
        "export_csv": 200,
        "export_interne": 200,
        "apercu_devis": 200,
        "geler": 403,
    },
    "lecteur@dubois.demo": {
        "export_csv": 403,
        "export_interne": 403,
        "apercu_devis": 403,
        "geler": 403,
    },
}


@pytest.fixture()
def base_version(seeded_client: TestClient) -> str:
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    return f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}"


@pytest.mark.parametrize("email", sorted(ATTENDU))
def test_the_api_refusals_per_role_are_the_ones_the_document_describes(
    seeded_client: TestClient, base_version: str, email: str
) -> None:
    headers = login(seeded_client, email)
    mesure = {}
    for nom, (methode, suffixe, _) in COMMANDES.items():
        corps = {"confirm": True, "label": None} if methode == "POST" else None
        reponse = seeded_client.request(
            methode, f"{base_version}/{suffixe}", headers=headers, json=corps
        )
        mesure[nom] = reponse.status_code

    assert mesure == ATTENDU[email], (
        f"les refus de {email} ont changé : {mesure} au lieu de {ATTENDU[email]}. "
        "Mettez à jour docs/DROITS_COTE_WEB.md avant de corriger ce test."
    )


def test_a_refusal_names_the_permission_the_interface_never_shows(
    seeded_client: TestClient, base_version: str
) -> None:
    """L'API dit exactement ce qui manque. Le téléchargement web jette ce détail.

    Sans ce test, on pourrait « corriger » l'affichage web en supposant que le
    serveur ne fournit pas le motif. Il le fournit.
    """
    headers = login(seeded_client, "lecteur@dubois.demo")
    reponse = seeded_client.get(f"{base_version}/export.csv", headers=headers)

    assert reponse.status_code == 403
    detail = reponse.json()["detail"]
    assert detail["code"] == "permission_denied"
    assert detail["required_permission"] == "export:client"
    assert detail["role"] == "viewer"


def test_only_the_server_driven_condition_hides_a_button(
    seeded_client: TestClient, base_version: str
) -> None:
    """La seule condition d'affichage correcte est celle qui vient du serveur.

    `includes_internal_costs` est faux pour le lecteur : le bouton « export
    interne » est donc masqué, et son 403 n'est jamais atteint. Les trois autres
    conditions sont locales au navigateur, et ne protègent rien.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    calcul = seeded_client.get(f"{base_version}/computation", headers=lecteur)
    assert calcul.status_code == 200, calcul.text
    assert calcul.json()["includes_internal_costs"] is False

    metreur = login(seeded_client, "metreur@dubois.demo")
    calcul = seeded_client.get(f"{base_version}/computation", headers=metreur)
    assert calcul.json()["includes_internal_costs"] is True


def test_an_expired_token_is_named_as_such_on_every_endpoint(
    seeded_client: TestClient,
) -> None:
    """`token_expired` est distinct de `invalid_token`, et le reste partout.

    Le client web peut donc distinguer « votre session a expiré » de « ce jeton
    est faux ». Il ne le fait pas — voir le document — mais l'information est
    là, et doit y rester.
    """
    import jwt

    from metreo_api.config import get_settings

    reglages = get_settings()
    entetes = login(seeded_client, "lecteur@dubois.demo")
    brut = entetes["Authorization"].split(" ", 1)[1]
    revendications = jwt.decode(
        brut, reglages.jwt_secret, algorithms=["HS256"], options={"verify_aud": False}
    )
    perime = {**revendications, "exp": int(time.time()) - 60}
    jeton = jwt.encode(perime, reglages.jwt_secret, algorithm="HS256")
    expire = {"Authorization": f"Bearer {jeton}"}

    for chemin in ("/api/v1/auth/me", "/api/v1/estimates", "/api/v1/audit/events"):
        reponse = seeded_client.get(chemin, headers=expire)
        assert reponse.status_code == 401, f"{chemin} → {reponse.status_code}"
        assert reponse.json()["detail"]["code"] == "token_expired", chemin
