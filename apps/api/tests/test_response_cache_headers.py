"""Une réponse de l'API ne doit pas rester sur le disque du poste client.

Mesuré avec Chromium 1194 en mode headless, sur un serveur qui renvoie
exactement les en-têtes de `export.csv` (`text/csv; charset=utf-8` et un
`Content-Disposition: attachment`) :

    sans en-tête de cache   le corps du devis se retrouve en clair dans
                            `Default/Cache/Cache_Data/` du profil
    Cache-Control: no-store le corps est absent du profil

Le navigateur ne *réutilise* pas cette réponse — sans validateur ni durée de
fraîcheur, il refait la requête. Ce n'est pas la réutilisation qui est en
cause : c'est le stockage. Le devis reste sur le disque après la déconnexion,
et sur un poste partagé le suivant le relit.

Le client web protège déjà ses lectures JSON en posant `cache: 'no-store'` sur
la requête — mais pas son téléchargement d'export, qui est justement celui qui
porte le devis complet, coûts internes compris. Et un autre client (curl, une
intégration, une application mobile) ne pose rien du tout. La garantie doit
donc venir du serveur.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login

#: Les réponses qui portent des données du tenant. La liste sert à *mesurer*,
#: pas à protéger : la protection est posée pour toutes les réponses.
CHEMINS_SENSIBLES = (
    "export.csv",
    "export.csv?include_internal=true",
    "quote.html",
    "computation",
)


@pytest.fixture()
def version_chiffree(seeded_client: TestClient) -> tuple[dict[str, str], str]:
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    return headers, f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}"


@pytest.mark.parametrize("suffixe", CHEMINS_SENSIBLES)
def test_a_quote_is_never_written_to_the_client_disk(
    seeded_client: TestClient, version_chiffree: tuple[dict[str, str], str], suffixe: str
) -> None:
    headers, base = version_chiffree
    reponse = seeded_client.get(f"{base}/{suffixe}", headers=headers)

    assert reponse.status_code == 200, reponse.text
    assert reponse.headers.get("Cache-Control") == "no-store", (
        f"{suffixe} renvoie {reponse.headers.get('Cache-Control')!r} : le corps "
        "de cette réponse peut être écrit sur le disque du poste client."
    )


def test_the_guarantee_does_not_depend_on_a_list_of_endpoints(
    seeded_client: TestClient,
) -> None:
    """Un endpoint ajouté demain est protégé sans qu'on y pense.

    Protéger endpoint par endpoint, c'est la liste de refus que la PR sur les
    coûts internes a déjà montrée fragile : ce qui n'y figure pas est exposé
    par défaut. Ce balayage part donc des routes que l'application déclare —
    pas d'une liste tenue à la main, qui se périmerait au premier ajout.
    """
    headers = login(seeded_client, "admin@dubois.demo")

    # La surface que l'API *déclare*, pas une liste tenue à la main. On garde
    # les GET sans paramètre de chemin : ceux qu'une requête nue atteint.
    document = seeded_client.app.openapi()  # type: ignore[attr-defined]
    chemins = sorted(
        chemin
        for chemin, operations in document["paths"].items()
        if "get" in operations and "{" not in chemin
    )
    assert len(chemins) >= 8, (
        f"seulement {len(chemins)} routes balayées : le balayage ne mesure plus rien"
    )

    sans_garantie = []
    atteintes = 0
    for chemin in chemins:
        reponse = seeded_client.get(chemin, headers=headers)
        if reponse.status_code >= 400:
            continue
        atteintes += 1
        if reponse.headers.get("Cache-Control") != "no-store":
            sans_garantie.append((chemin, reponse.headers.get("Cache-Control")))

    assert atteintes >= 8, f"seulement {atteintes} routes atteintes sur {len(chemins)}"
    assert not sans_garantie, f"réponses sans no-store : {sans_garantie}"


def test_an_endpoint_may_still_choose_its_own_caching(seeded_client: TestClient) -> None:
    """La garantie est un défaut, pas une contrainte.

    Sans ce test, quelqu'un pourrait durcir le middleware en écrasement, et un
    endpoint légitimement cacheable — un fichier statique, une ressource
    publique — perdrait silencieusement son en-tête.
    """
    from starlette.responses import PlainTextResponse

    application = seeded_client.app
    application.add_api_route(  # type: ignore[attr-defined]
        "/_essai_cache",
        lambda: PlainTextResponse("x", headers={"Cache-Control": "public, max-age=60"}),
        methods=["GET"],
    )
    try:
        reponse = seeded_client.get("/_essai_cache")
        assert reponse.headers["Cache-Control"] == "public, max-age=60"
    finally:
        application.router.routes = [  # type: ignore[attr-defined]
            r for r in application.router.routes if getattr(r, "path", "") != "/_essai_cache"
        ]
