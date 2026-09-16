"""L'aperçu imprimable est du HTML servi par l'API : il doit échapper ses entrées.

`export.csv` est neutralisé cellule par cellule, et six tests le tiennent.
`quote.html` sort par la même route famille, avec `media_type: text/html`, et
**rien n'affirmait qu'il échappe quoi que ce soit**. Les tests d'export
existants ne le touchent que pour le refus 422 et le journal d'audit.

L'écart compte, parce que les deux formats n'ont pas la même conséquence. Une
cellule interprétée par un tableur s'exécute chez le destinataire du devis, à
l'ouverture du fichier. Un `<script>` dans une page servie par l'API s'exécute
**dans l'origine de l'API**, sur la session de qui l'ouvre — et le libellé d'un
poste, le nom d'un client ou la référence d'un projet viennent d'un tiers.

Ce fichier injecte à travers l'API réelle, dans chaque champ que la page
reprend, et lit la page servie. Il ne relit pas le code : un `escape()` présent
dans une f-string ne prouve pas qu'il couvre le champ voisin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login

#: Ce qu'on essaie de faire passer. Chaque charge est inoffensive et se repère
#: sans ambiguïté dans la page ; aucune ne dépend d'un navigateur pour être
#: jugée — c'est la présence de la forme NON échappée qui condamne.
CHARGES: list[pytest.param] = [
    pytest.param("<script>alert(1)</script>", id="balise-script"),
    pytest.param('"><img src=x onerror=alert(1)>', id="sortie-d-attribut"),
    pytest.param("</td></tr><tr><td>injecté", id="sortie-de-cellule"),
    pytest.param("<style>body{display:none}</style>", id="balise-style"),
]

#: `javascript:alert(1)` a été retiré des charges : il ne contient aucun
#: caractère qu'`escape()` transforme, et il n'est dangereux que dans un
#: attribut `href` ou `src`. La page n'en construit aucun à partir d'une entrée
#: utilisateur. L'y chercher aurait fait échouer le test sur une chaîne
#: parfaitement inoffensive — première version de ce fichier.

#: Les fragments qui ne doivent JAMAIS apparaître dans la page. Ils commencent
#: tous par `<`, que `escape()` transforme en `&lt;` : leur présence brute
#: signifie qu'un champ a traversé sans passer par lui.
#:
#: Deux entrées ont été retirées, et les deux étaient des erreurs de critère,
#: pas des trouvailles :
#:
#: * `<style` — la page porte sa propre feuille de style. L'interdire
#:   condamnait l'application pour son propre balisage.
#: * `onerror=` — une charge échappée le contient encore, en TEXTE :
#:   `img src=x onerror=alert(1)&gt;`. Le `&gt;` est la preuve que l'échappement
#:   a fonctionné. Chercher le mot plutôt que la structure aurait déclaré
#:   vulnérable une page correcte.
#:
#: Ce qui est cherché, c'est donc une balise réellement ouverte — et, plus haut,
#: l'absence de la charge sous sa forme brute.
INTERDITS = ("<script", "<img ", "</td></tr><tr>")


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _devis_gele(
    client: TestClient,
    headers: dict[str, str],
    *,
    reference: str,
    nom_projet: str,
    designation: str,
    client_nom: str = "Client",
    ville: str = "Bruxelles",
) -> tuple[str, str]:
    """Un devis complet, gelé, dont chaque champ visible porte ce qu'on veut."""
    projet_reponse = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "reference": reference,
            "name": nom_projet,
            "client_name": client_nom,
            "client_reference": client_nom,
            "city": ville,
            "address": ville,
        },
    ).json()
    assert "id" in projet_reponse, projet_reponse
    projet = projet_reponse
    boq = client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=headers, json={"name": "Métré"}
    ).json()
    # Un nom par appel : `uq_pricebook_org_name` refuse deux bibliothèques
    # homonymes dans la même organisation, et la deuxième création rendait une
    # erreur dont l'assistant lisait `['id']` — première version de ce fichier.
    livre = client.post(
        "/api/v1/price-books", headers=headers, json={"name": f"Prix {reference}"}
    ).json()
    assert "id" in livre, livre
    version = client.post(
        f"/api/v1/price-books/{livre['id']}/versions", headers=headers, params={"label": "v1"}
    ).json()
    prix = client.post(
        f"/api/v1/price-books/versions/{version['id']}/items",
        headers=headers,
        json={"code": "X", "label": "Prix", "unit_code": "m3", "unit_price": "10"},
    ).json()
    client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=headers,
        json={
            "position": "1.1",
            "designation": designation,
            "unit_code": "m3",
            "quantity": "1",
            "price_item_id": prix["id"],
        },
    )
    estimation = client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": projet["id"],
            "boq_id": boq["id"],
            "price_book_version_id": version["id"],
            "name": "Aperçu",
        },
    ).json()
    version_devis = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=headers, json={"label": "v1"}
    ).json()
    return estimation["id"], version_devis["id"]


def _page(client: TestClient, headers: dict[str, str], estimation: str, version: str) -> str:
    reponse = client.get(
        f"/api/v1/estimates/{estimation}/versions/{version}/quote.html", headers=headers
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.headers["content-type"].startswith("text/html")
    return reponse.text


class TestNoUserFieldReachesThePageUnescaped:
    """Un champ par test : une charge qui passe doit nommer son champ."""

    @pytest.mark.parametrize("charge", CHARGES)
    def test_the_line_designation(
        self, seeded_client: TestClient, headers: dict[str, str], charge: str
    ) -> None:
        estimation, version = _devis_gele(
            seeded_client,
            headers,
            reference="XSS-1",
            nom_projet="Aperçu",
            designation=charge,
        )
        page = _page(seeded_client, headers, estimation, version)
        assert charge not in page, "la désignation traverse telle quelle"
        for interdit in INTERDITS:
            assert interdit not in page.lower(), f"fragment brut dans la page : {interdit!r}"

    @pytest.mark.parametrize("charge", CHARGES)
    def test_the_project_name_and_client(
        self, seeded_client: TestClient, headers: dict[str, str], charge: str
    ) -> None:
        estimation, version = _devis_gele(
            seeded_client,
            headers,
            reference="XSS-2",
            nom_projet=charge,
            designation="Poste",
            client_nom=charge,
            ville=charge,
        )
        page = _page(seeded_client, headers, estimation, version)
        assert charge not in page, "un champ de projet traverse tel quel"
        for interdit in INTERDITS:
            assert interdit not in page.lower(), f"fragment brut dans la page : {interdit!r}"

    def test_the_project_reference(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        """La référence sert aussi de nom de fichier : elle est doublement exposée."""
        charge = "<script>alert(1)</script>"
        estimation, version = _devis_gele(
            seeded_client, headers, reference=charge, nom_projet="Aperçu", designation="Poste"
        )
        page = _page(seeded_client, headers, estimation, version)
        assert charge not in page
        assert page.lower().count("<script") == 0, "une balise script figure dans la page"


class TestTheEscapingIsVisibleAndNotAccidental:
    """Une page qui ne contiendrait pas la charge du tout ne prouverait rien."""

    def test_the_payload_is_present_but_escaped(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        """Le libellé doit rester LISIBLE : échapper n'est pas supprimer.

        Sans ce contrôle, une régression qui viderait la désignation ferait
        passer tous les tests ci-dessus au vert en n'affichant plus rien.
        """
        estimation, version = _devis_gele(
            seeded_client,
            headers,
            reference="XSS-3",
            nom_projet="Aperçu",
            designation="<script>alert(1)</script>",
        )
        page = _page(seeded_client, headers, estimation, version)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page, (
            "la désignation doit apparaître échappée, pas disparaître"
        )

    def test_a_bounded_amount_of_markup_is_the_applications_own(
        self, seeded_client: TestClient, headers: dict[str, str]
    ) -> None:
        """Et le nombre de lignes du tableau ne dépend pas de la charge.

        `</td></tr><tr><td>` cherche à fabriquer une ligne supplémentaire ; si
        elle y parvenait, le compte de `<tr` changerait.
        """
        estimation, version = _devis_gele(
            seeded_client, headers, reference="XSS-4", nom_projet="Aperçu", designation="Poste sain"
        )
        temoin = _page(seeded_client, headers, estimation, version).lower().count("<tr")

        estimation, version = _devis_gele(
            seeded_client,
            headers,
            reference="XSS-5",
            nom_projet="Aperçu",
            designation="</td></tr><tr><td>injecté",
        )
        attaque = _page(seeded_client, headers, estimation, version).lower().count("<tr")
        assert attaque == temoin, (
            f"la charge a fabriqué {attaque - temoin} ligne(s) de tableau supplémentaire(s)"
        )
