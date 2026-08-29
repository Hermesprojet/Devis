"""L'aperçu de devis se défend lui-même, faute de pouvoir compter sur son hôte.

Le client web ouvre cet aperçu par `URL.createObjectURL` puis `window.open`
(`apps/web/src/app/estimations/[estimateId]/[versionId]/page.tsx`). Une URL
`blob:` **hérite de l'origine de la page qui la crée** : le devis s'affiche donc
dans l'origine de l'application, où le jeton de session vit en
`sessionStorage`.

L'échappement vérifié par ailleurs est aujourd'hui la seule chose qui empêche
une désignation de poste de devenir du script exécutable. S'il cédait une fois,
la conséquence ne serait pas un affichage abîmé : ce serait l'exfiltration du
jeton. La politique posée dans l'en-tête du document ramène cette conséquence à
un affichage abîmé.

Mesuré dans Chromium 1194, sur deux documents identiques à la politique près,
portant tous deux une balise `<script>` et un gestionnaire `onerror=` :

    sans politique   <title>ONERROR-EXECUTE</title>   <p>DOM-MODIFIE</p>
    avec politique   <title>INTACT</title>            <p>Désignation d'un poste</p>

Les tests ci-dessous ne relancent pas de navigateur — la suite de l'API doit
tourner sans, et le contrôle d'inventaire des tests ignorés refuse un module qui
s'ignorerait selon l'environnement. Ils vérifient ce qui est vérifiable sans
navigateur : que la politique est là, qu'elle interdit le script, et qu'elle
n'autorise rien que le document n'utilise.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_estimating import price_the_missing_line

_META = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', re.IGNORECASE)


@pytest.fixture()
def apercu(seeded_client: TestClient) -> str:
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    reponse = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/quote.html",
        headers=headers,
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.text


def _politique(document: str) -> dict[str, list[str]]:
    trouve = _META.search(document)
    assert trouve is not None, "l'aperçu ne déclare aucune politique de sécurité"
    directives: dict[str, list[str]] = {}
    for morceau in trouve.group(1).split(";"):
        parties = morceau.split()
        if parties:
            directives[parties[0]] = parties[1:]
    return directives


def test_the_quote_forbids_script_by_falling_back_to_none(apercu: str) -> None:
    """Pas de `script-src` : le script retombe sur `default-src 'none'`.

    Déclarer `script-src 'none'` serait équivalent ; ce test accepte les deux
    et refuse tout ce qui autoriserait un script, y compris `'unsafe-inline'`,
    qui rendrait un `onerror=` de nouveau exécutable.
    """
    directives = _politique(apercu)
    assert directives.get("default-src") == ["'none'"], directives

    autorise = directives.get("script-src")
    assert autorise in (None, ["'none'"]), (
        f"le devis autoriserait du script : script-src {autorise}"
    )


def test_the_policy_allows_nothing_the_document_does_not_use(apercu: str) -> None:
    """Une politique plus large que nécessaire n'est plus une politique.

    Le document ne porte qu'un `<style>` en ligne : ni script, ni image, ni
    police, ni feuille externe. Tout le reste doit rester fermé.
    """
    directives = _politique(apercu)
    assert directives.get("style-src") == ["'unsafe-inline'"], directives

    ouvertes = {
        nom: valeurs
        for nom, valeurs in directives.items()
        if nom not in {"default-src", "style-src"} and valeurs not in ([], ["'none'"])
    }
    assert ouvertes == {}, f"directives plus larges que le document : {ouvertes}"


def test_the_document_really_needs_no_more_than_that(apercu: str) -> None:
    """Le contrôle qui rend le précédent vérifiable.

    Si le devis venait à embarquer une image, une police ou un script, la
    politique ci-dessus casserait l'affichage en silence. Ce test le dirait.
    """
    corps = apercu.split("</head>", 1)[1]
    assert "<script" not in corps.lower(), "le corps du devis embarque un script"
    assert "<img" not in corps.lower(), "le corps du devis embarque une image"
    assert "@font-face" not in apercu, "le devis embarque une police"
    assert 'rel="stylesheet"' not in apercu, "le devis charge une feuille externe"
    # Un seul bloc de style, en ligne : c'est ce que `style-src` autorise.
    assert apercu.count("<style>") == 1


def test_the_policy_sits_in_the_head_before_anything_it_protects(apercu: str) -> None:
    """Une politique déclarée après le contenu ne protège pas ce contenu."""
    position_politique = apercu.index("Content-Security-Policy")
    assert position_politique < apercu.index("</head>")
    assert position_politique < apercu.index("<body")
