"""L'identité de l'émetteur sur un devis : ce qui s'imprime, ce qui se fige.

Un devis est un document commercial : il doit dire qui l'émet, où lui écrire et
à qui téléphoner. Ce fichier éprouve trois promesses distinctes.

**Ce qui s'imprime.** Le logo, le nom commercial, la raison sociale, le numéro
d'entreprise, l'adresse complète et les coordonnées figurent sur le PDF et sur
la page publique, dans la même forme des deux côtés.

**Ce qui refuse.** Un profil émetteur incomplet ne produit pas un devis
approximatif : il produit un refus qui NOMME les champs à remplir.

**Ce qui ne bouge plus.** Déménager, changer de raison sociale ou remplacer le
logo ne change rien à un devis déjà remis — mêmes octets, même empreinte — et
le devis suivant porte la nouvelle identité sans altérer le premier.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import pdf as moteur

from . import images_fictives as fixtures
from .conftest import login
from .emission import emettre, fiche, geler, prix_manquant, rattacher, version_de_plus

PROFIL = {
    "name": "Terrassements Dubois",
    "legal_name": "Terrassements Dubois SA",
    "company_number": "BE0123456789",
    "address": "Rue Fictive du Chantier 12",
    "address_complement": "Zoning Nord, bâtiment C",
    "postal_code": "5000",
    "city": "Namur",
    "email": "contact@dubois.demo",
    "phone": "+32 81 00 00 00",
    "website": "https://dubois.demo",
}


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def pret(seeded_client: TestClient, admin: dict[str, str]) -> dict[str, Any]:
    """Un chantier au bord de l'émission, profil émetteur complet."""
    seeded_client.patch("/api/v1/organization", headers=admin, json=PROFIL)
    estimation = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]
    prix_manquant(seeded_client, admin, estimation)
    rattacher(seeded_client, admin, estimation["project_id"], fiche(seeded_client, admin)["id"])
    geler(seeded_client, admin, estimation, version)
    return {"estimation": estimation, "version": version}


def _telecharger(client: TestClient, entetes: dict[str, str], devis_id: str) -> bytes:
    reponse = client.get(f"/api/v1/issued-quotes/{devis_id}/document.pdf", headers=entetes)
    assert reponse.status_code == 200, reponse.text
    return reponse.content


# --------------------------------------------------------------------------
# 1. Le refus : nommer ce qui manque, plutôt qu'émettre un document boiteux
# --------------------------------------------------------------------------


def test_un_profil_incomplet_refuse_l_emission_en_nommant_les_champs(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Le refus conduit à l'endroit où corriger, il ne dit pas « incomplet ».

    Les noms de champ sont rendus tels quels : l'écran sait alors quel encadré
    surligner, ce qu'une phrase ne permettrait pas.
    """
    seeded_client.patch(
        "/api/v1/organization", headers=admin, json={"address": "", "postal_code": "", "city": ""}
    )
    refus = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert refus.status_code == 422, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "emitter_incomplete"
    assert detail["context"]["missing"] == ["address", "postal_code", "city"]


def test_le_refus_de_l_emetteur_precede_celui_du_client(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Sans émetteur identifiable, la qualité de la fiche client n'y change rien."""
    seeded_client.patch("/api/v1/organization", headers=admin, json={"address": ""})
    estimation = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]
    prix_manquant(seeded_client, admin, estimation)
    geler(seeded_client, admin, estimation, version)
    refus = emettre(seeded_client, admin, estimation, version)
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "emitter_incomplete"


# --------------------------------------------------------------------------
# 2. Ce que le document imprime
# --------------------------------------------------------------------------


def test_le_pdf_imprime_l_identite_complete_de_l_emetteur(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    texte = moteur.extraire_le_texte(_telecharger(seeded_client, admin, reponse.json()["id"]))
    for attendu in (
        PROFIL["name"],
        PROFIL["legal_name"],
        PROFIL["address"],
        PROFIL["address_complement"],
        f"{PROFIL['postal_code']} {PROFIL['city']}",
        PROFIL["phone"],
        PROFIL["email"],
        PROFIL["website"],
    ):
        assert attendu in texte, f"« {attendu} » manque au PDF"
    assert f"N° d'entreprise : {PROFIL['company_number']}" in texte


def test_un_logo_voyage_dans_les_octets_du_pdf(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Le PDF porte l'image, il ne la référence pas.

    C'est ce qui rend le fichier autonome : le client l'ouvre hors ligne, deux
    ans plus tard, et le logo est là.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.horizontal(), "image/png")},
    )
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    octets = _telecharger(seeded_client, admin, reponse.json()["id"])
    assert b"/Subtype /Image" in octets
    assert b"/XObject << /Im1" in octets
    # Et le texte reste lisible : les octets de l'image ne le polluent pas.
    texte = moteur.extraire_le_texte(octets)
    assert PROFIL["name"] in texte
    assert len(texte) < 4000, "le texte extrait porte des octets d'image"


def test_sans_logo_le_document_reste_complet(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    octets = _telecharger(seeded_client, admin, reponse.json()["id"])
    assert b"/Subtype /Image" not in octets
    assert PROFIL["address"] in moteur.extraire_le_texte(octets)


@pytest.mark.parametrize(
    ("nom", "logo"),
    [
        ("carré", fixtures.carre()),
        ("horizontal", fixtures.horizontal()),
        ("avec transparence", fixtures.carre(alpha=True)),
        ("en gris", fixtures.gris()),
    ],
)
def test_toute_forme_de_logo_produit_un_document_valide(
    seeded_client: TestClient,
    admin: dict[str, str],
    pret: dict[str, Any],
    nom: str,
    logo: bytes,
) -> None:
    """Carré ou bandeau, opaque ou transparent : le document reste sur une page.

    Le logo s'inscrit dans une boîte de taille fixe en gardant ses
    proportions. Si la place prise dépendait de l'image, un bandeau très large
    repousserait l'identité hors de la page — et rien ne le signalerait.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", logo, "image/png")},
    )
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    octets = _telecharger(seeded_client, admin, reponse.json()["id"])
    assert octets[:5] == b"%PDF-", nom
    assert octets.rstrip().endswith(b"%%EOF"), nom
    texte = moteur.extraire_le_texte(octets)
    assert PROFIL["name"] in texte, nom
    assert "Commune de Perwez" in texte, nom


def test_une_identite_tres_longue_et_accentuee_ne_deborde_pas(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Raison sociale d'intercommunale, adresse à rallonge, accents partout.

    Le repliement du texte est ce qui l'empêche de sortir de la marge. Ce test
    ne « voit » pas la page : il vérifie que tout est IMPRIMÉ — donc replié et
    non tronqué — et que le document reste bien formé.
    """
    long = {
        "name": "Société Intercommunale de Développement Économique du Brabant Wallon",
        "legal_name": (
            "Société Coopérative Intercommunale à Responsabilité Limitée "
            "de Développement Économique et d'Aménagement du Territoire"
        ),
        "address": "Avenue des Anciens Établissements Métallurgiques Réunis 1234, aile Ouest",
        "address_complement": "Bâtiment « Émile Vandervelde », 4ᵉ étage, boîte 12",
        "postal_code": "1300",
        "city": "Wavre-sur-Dyle-et-Orne",
    }
    seeded_client.patch("/api/v1/organization", headers=admin, json=long)
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.horizontal(), "image/png")},
    )
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    octets = _telecharger(seeded_client, admin, reponse.json()["id"])
    texte = moteur.extraire_le_texte(octets)
    # Les accents survivent au codage WinAnsi du PDF.
    assert "Économique" in texte
    assert "Émile Vandervelde" in texte
    # Chaque mot de l'adresse est présent : rien n'a été coupé en silence.
    for mot in ("Métallurgiques", "Wavre-sur-Dyle-et-Orne", "1300"):
        assert mot in texte, mot
    assert moteur.compter_les_pages(octets) >= 1
    assert octets.rstrip().endswith(b"%%EOF")


# --------------------------------------------------------------------------
# 3. Ce qui ne bouge plus
# --------------------------------------------------------------------------


def test_modifier_le_profil_ne_change_pas_un_devis_deja_emis(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Mêmes octets, même empreinte, après un déménagement et un logo neuf."""
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("avant.png", fixtures.carre(), "image/png")},
    )
    reponse = emettre(seeded_client, admin, pret["estimation"], pret["version"])
    assert reponse.status_code == 201, reponse.text
    devis = reponse.json()
    avant = _telecharger(seeded_client, admin, devis["id"])

    seeded_client.patch(
        "/api/v1/organization",
        headers=admin,
        json={
            "name": "Dubois Travaux Publics",
            "legal_name": "Dubois Travaux Publics SRL",
            "address": "Chaussée Inventée 300",
            "city": "Charleroi",
            "postal_code": "6000",
        },
    )
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("apres.png", fixtures.horizontal(), "image/png")},
    )

    apres = _telecharger(seeded_client, admin, devis["id"])
    assert apres == avant, "le devis remis a changé après une modification du profil"
    texte = moteur.extraire_le_texte(apres)
    assert PROFIL["name"] in texte
    assert "Dubois Travaux Publics" not in texte
    assert "Charleroi" not in texte


def test_le_devis_suivant_porte_la_nouvelle_identite(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Une nouvelle version, un nouveau devis : la correction s'applique ENSUITE."""
    premier = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    avant = _telecharger(seeded_client, admin, premier["id"])

    seeded_client.patch(
        "/api/v1/organization",
        headers=admin,
        json={"name": "Dubois Travaux Publics", "city": "Charleroi", "postal_code": "6000"},
    )
    suivante = version_de_plus(seeded_client, admin, pret["estimation"], "v2")
    geler(seeded_client, admin, pret["estimation"], suivante)
    second = emettre(seeded_client, admin, pret["estimation"], suivante)
    assert second.status_code == 201, second.text

    texte_second = moteur.extraire_le_texte(_telecharger(seeded_client, admin, second.json()["id"]))
    assert "Dubois Travaux Publics" in texte_second
    assert "Charleroi" in texte_second
    # Et le premier n'a pas bougé d'un octet.
    assert _telecharger(seeded_client, admin, premier["id"]) == avant


def test_retirer_le_logo_ne_le_retire_pas_des_devis_deja_emis(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Le devis porte SA copie : effacer le logo courant ne la touche pas.

    C'est la raison d'être de la copie faite à l'émission. Sans elle, retirer
    le logo laisserait un trou dans un document déjà remis à un client.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    avant = _telecharger(seeded_client, admin, devis["id"])
    assert b"/Subtype /Image" in avant

    seeded_client.delete("/api/v1/organization/logo", headers=admin)
    apres = _telecharger(seeded_client, admin, devis["id"])
    assert apres == avant
    assert b"/Subtype /Image" in apres


def test_un_instantane_d_avant_la_migration_s_imprime_encore() -> None:
    """Un devis émis avant que ces champs n'existent n'en porte aucun.

    Son instantané ne connaît ni adresse, ni logo, ni site. Le composeur le lit
    avec `.get` : rien ne lève, et le document se réimprime exactement tel
    qu'il a été émis. C'est la compatibilité qu'une migration non destructive
    doit tenir — et elle se vérifie ici, sur la seule chose qui compte : les
    octets produits.
    """
    from datetime import date, datetime

    from metreo_api.services.quote_pdf import composer_le_devis

    ancien = {
        "name": "Ancienne Entreprise",
        "legal_name": "Ancienne Entreprise SA",
        "company_number": "BE0000000000",
        "country_code": "BE",
        "region_code": "BE-WAL",
        "currency": "EUR",
        "locale": "fr-BE",
    }
    octets = composer_le_devis(
        numero="DEV-2024-0001",
        emis_le=datetime(2024, 5, 4, 10, 30),
        valid_until=date(2024, 6, 3),
        organisation=ancien,
        client={"name": "Commune Fictive", "billing_address": "Place 1", "city": "Namur"},
        projet={"reference": "CH-1", "name": "Chantier", "currency": "EUR"},
        document={"lines": [], "totals": {"total_ht": "100.00", "total_ttc": "121.00"}},
        terms=None,
        include_internal_costs=False,
    )
    assert octets[:5] == b"%PDF-"
    texte = moteur.extraire_le_texte(octets)
    assert "Ancienne Entreprise" in texte
    assert "N° d'entreprise : BE0000000000" in texte
    # Aucune image : il n'y avait pas de logo à cette époque.
    assert b"/Subtype /Image" not in octets


# --------------------------------------------------------------------------
# 4. La page publique : la même identité, et rien d'interne
# --------------------------------------------------------------------------


def _partager(client: TestClient, entetes: dict[str, str], devis_id: str) -> str:
    lien = client.post(f"/api/v1/issued-quotes/{devis_id}/share-links", headers=entetes, json={})
    assert lien.status_code == 201, lien.text
    secret = lien.json()["url"].split("#", 1)[1]
    ouverture = client.post("/api/v1/public/quote-sessions", json={"secret": secret})
    assert ouverture.status_code == 204, ouverture.text
    return secret


def test_la_page_publique_montre_la_meme_identite_que_le_pdf(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Le client lit à l'écran l'adresse qu'il lira sur le papier.

    Les deux sont construites par la MÊME fonction : deux constructions
    séparées divergeraient au premier champ ajouté, et le client verrait une
    adresse à l'écran et une autre sur le document, pour un seul devis.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    _partager(seeded_client, admin, devis["id"])

    vue = seeded_client.get("/api/v1/public/quote")
    assert vue.status_code == 200, vue.text
    charge = vue.json()
    assert charge["organization_name"] == PROFIL["name"]
    assert charge["organization_legal_name"] == PROFIL["legal_name"]
    assert charge["organization_company_number"] == PROFIL["company_number"]
    assert charge["organization_email"] == PROFIL["email"]
    assert charge["organization_phone"] == PROFIL["phone"]
    assert charge["organization_website"] == PROFIL["website"]
    assert charge["has_logo"] is True
    assert charge["organization_address_lines"] == [
        PROFIL["address"],
        PROFIL["address_complement"],
        f"{PROFIL['postal_code']} {PROFIL['city']}",
        "BE",
    ]
    # Les mêmes lignes que le PDF imprime.
    texte = moteur.extraire_le_texte(_telecharger(seeded_client, admin, devis["id"]))
    for ligne in charge["organization_address_lines"]:
        assert ligne in texte, ligne


def test_le_logo_public_est_la_copie_figee_du_devis(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Remplacer le logo courant ne change pas celui d'un devis déjà remis.

    C'est toute la raison de la copie faite à l'émission. Servir le logo vivant
    serait plus simple d'un fichier, et ferait changer chez un client l'en-tête
    d'un devis reçu l'an dernier le jour où l'entreprise change de charte.
    """
    origine = fixtures.carre()
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("origine.png", origine, "image/png")},
    )
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    _partager(seeded_client, admin, devis["id"])

    avant = seeded_client.get("/api/v1/public/quote/logo")
    assert avant.status_code == 200, avant.text
    assert avant.content == origine
    assert avant.headers["content-type"] == "image/png"

    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("neuf.png", fixtures.horizontal(), "image/png")},
    )
    apres = seeded_client.get("/api/v1/public/quote/logo")
    assert apres.content == origine, "le logo d'un devis remis a changé"

    seeded_client.delete("/api/v1/organization/logo", headers=admin)
    assert seeded_client.get("/api/v1/public/quote/logo").content == origine


def test_sans_logo_la_route_publique_repond_404(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    _partager(seeded_client, admin, devis["id"])
    assert seeded_client.get("/api/v1/public/quote").json()["has_logo"] is False
    absent = seeded_client.get("/api/v1/public/quote/logo")
    assert absent.status_code == 404
    assert absent.json()["detail"]["code"] == "no_logo"


def test_sans_session_publique_le_logo_reste_fermé(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """Le lien signé est ce qui autorise — pas la connaissance de la route."""
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    emettre(seeded_client, admin, pret["estimation"], pret["version"])
    seeded_client.cookies.clear()
    refus = seeded_client.get("/api/v1/public/quote/logo")
    assert refus.status_code in {401, 403, 404}, refus.text


def test_la_page_publique_ne_porte_aucun_cout_interne(
    seeded_client: TestClient, admin: dict[str, str], pret: dict[str, Any]
) -> None:
    """L'identité s'ajoute ; la confidentialité ne bouge pas."""
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    devis = emettre(seeded_client, admin, pret["estimation"], pret["version"]).json()
    _partager(seeded_client, admin, devis["id"])
    charge = seeded_client.get("/api/v1/public/quote").text
    for interne in ("Déboursé", "déboursé", "Revient", "revient", "Marge", "marge"):
        assert interne not in charge, interne
