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


# --------------------------------------------------------------------------
# 5. La mise en page : rien ne sort de la feuille, jamais
# --------------------------------------------------------------------------


#: Les blocs de l'en-tête qui ne doivent JAMAIS se recouvrir. Le cartouche du
#: devis est posé à `droite - 210` ; l'identité de l'émetteur vit à sa gauche.
BORD_DU_CARTOUCHE = 595.28 - 48.0 - 210.0


def _coordonnees_dessinees(
    **remplacements: Any,
) -> list[tuple[float, float, float, str, str, int]]:
    """Compose un devis en captant CHAQUE coordonnée réellement dessinée.

    Vérifier la mise en page en lisant le PDF produit demanderait un moteur de
    rendu. Instrumenter le traceur, lui, donne exactement ce qui compte : où
    chaque chaîne a été posée. C'est la seule façon de prouver qu'aucun contenu
    ne tombe hors de la page — et un contenu hors page est invisible, donc
    indétectable autrement.
    """
    from datetime import date, datetime

    from metreo_api.services import pdf as moteur
    from metreo_api.services.quote_pdf import composer_le_devis

    trace: list[tuple[float, float, float, str]] = []
    polices: list[str] = []
    pages: list[int] = []
    texte_origine = moteur.Page.texte
    image_origine = moteur.Page.image

    def texte(self: Any, x: float, y: float, contenu: str, **kw: Any) -> None:
        if contenu:
            # La POLICE est captée, pas seulement la taille : mesurer une
            # capitale grasse avec les largeurs du romain sous-estime, et
            # c'est exactement l'angle mort qui laissait passer un
            # chevauchement.
            trace.append((x, y, float(kw.get("taille", 10.0)), contenu))
            polices.append(str(kw.get("police", moteur.HELVETICA)))
            # La PAGE est captée aussi : seule celle qui porte le cartouche
            # peut le chevaucher. Les pages de continuation n'en ont pas.
            pages.append(id(self))
        texte_origine(self, x, y, contenu, **kw)

    def image(self: Any, nom: str, x: float, y: float, largeur: float, hauteur: float) -> None:
        trace.append((x, y, 0.0, f"[image {largeur:.0f}x{hauteur:.0f}]"))
        polices.append(moteur.HELVETICA)
        pages.append(id(self))
        image_origine(self, nom, x, y, largeur, hauteur)

    parametres: dict[str, Any] = {
        "numero": "DEV-2026-0001",
        "emis_le": datetime(2026, 1, 1, 12, 0),
        "valid_until": date(2026, 12, 31),
        "organisation": dict(PROFIL),
        "client": {
            "name": "Commune de Perwez",
            "billing_address": "Rue Émile 2",
            "postal_code": "1360",
            "city": "Perwez",
        },
        "projet": {"reference": "CH-1", "name": "Chantier", "currency": "EUR"},
        "document": {
            "lines": [],
            "totals": {"total_ht": "1.00", "total_ttc": "1.21", "currency": "EUR", "taxes": []},
        },
        "terms": None,
        "include_internal_costs": False,
        "logo": None,
    }
    parametres.update(remplacements)

    moteur.Page.texte = texte  # type: ignore[method-assign]
    moteur.Page.image = image  # type: ignore[method-assign]
    try:
        composer_le_devis(**parametres)
    finally:
        moteur.Page.texte = texte_origine  # type: ignore[method-assign]
        moteur.Page.image = image_origine  # type: ignore[method-assign]
    # Le pied de page est dessiné sous la marge PAR CONCEPTION : il est exclu.
    return [
        (x, y, taille, contenu, police, page)
        for (x, y, taille, contenu), police, page in zip(trace, polices, pages, strict=True)
        if not (contenu.startswith("Devis DEV") or contenu.startswith("Page "))
    ]


def _hors_de_la_page(trace: list[tuple[float, float, float, str, str, int]]) -> list[str]:
    """Ce qui sort de la feuille, OU chevauche le cartouche du devis.

    Les largeurs sont MESURÉES avec les métriques réelles de la police. Les
    mesurer avec la même approximation que le code confirmerait ce que le code
    croit plutôt que ce qu'il dessine — et c'est ainsi qu'un chevauchement
    entre la raison sociale et le numéro du devis est passé inaperçu.
    """
    from metreo_api.services import pdf as moteur

    haut_du_cartouche = moteur.A4[1] - moteur.MARGE - 70
    page_du_cartouche = next((element[5] for element in trace if element[3] == "DEVIS"), None)
    fautifs = []
    for x, y, taille, contenu, police, page in trace:
        if y < moteur.MARGE:
            fautifs.append(f"sous la marge (y={y:.1f}) : {contenu[:40]!r}")
        elif y > moteur.A4[1] - moteur.MARGE + 1:
            fautifs.append(f"au-dessus de la marge (y={y:.1f}) : {contenu[:40]!r}")
        largeur = (
            moteur.largeur_texte(contenu, police, taille) if taille else float(contenu.count("x"))
        )
        if taille and x + largeur > moteur.A4[0] - moteur.MARGE + 2:
            fautifs.append(f"déborde à droite (x={x:.1f}) : {contenu[:40]!r}")
        # Le cartouche « DEVIS / N° … / dates » occupe la bande haute à droite.
        # Rien de ce qui est posé à sa gauche ne doit venir dessus.
        if (
            taille
            and page == page_du_cartouche
            and x < BORD_DU_CARTOUCHE
            and y > haut_du_cartouche
            and x + largeur > BORD_DU_CARTOUCHE + 2
        ):
            fautifs.append(
                f"chevauche le cartouche (x={x:.1f}→{x + largeur:.1f}) : {contenu[:40]!r}"
            )
    return fautifs


LIGNES_ORDINAIRES = [
    {
        "position": f"{i:02d}.10",
        "designation": "Déblai en terrain meuble, évacuation comprise",
        "unit": "m3",
        "quantity": "100",
        "unit_price_ht": "12.50",
        "total_ht": "1250.00",
    }
    for i in range(60)
]

#: Chaque champ rempli jusqu'à la borne que la base autorise. Ce n'est pas un
#: devis vraisemblable : c'est celui qui casserait la mise en page si elle
#: n'était pas bornée.
EMETTEUR_MAXIMAL = {
    "name": "S" * 200,
    "legal_name": "L" * 200,
    "company_number": "BE" + "9" * 48,
    "address": "A" * 255,
    "address_complement": "C" * 255,
    "postal_code": "1" * 20,
    "city": "V" * 120,
    "country_code": "BE",
    "email": "e" * 240 + "@exemple.invalid",
    "phone": "+32 " + "0" * 36,
    "website": "https://" + "w" * 240,
    "currency": "EUR",
}
CLIENT_MAXIMAL = {
    "name": "C" * 200,
    "billing_address": "B" * 255,
    "postal_code": "9999",
    "city": "W" * 120,
    "company_number": "BE0000000000",
    "contact_name": "K" * 200,
    "email": "k@exemple.invalid",
    "phone": "+32 2 000 00 00",
}
PROJET_MAXIMAL = {"reference": "R" * 60, "name": "P" * 200, "currency": "EUR"}


@pytest.mark.parametrize(
    ("nom", "remplacements"),
    [
        ("une ligne, sans logo", {}),
        ("une ligne, logo carré", {"logo": fixtures.carre()}),
        ("une ligne, logo horizontal", {"logo": fixtures.horizontal()}),
        (
            "soixante lignes, logo carré",
            {
                "logo": fixtures.carre(),
                "document": {
                    "lines": LIGNES_ORDINAIRES,
                    "totals": {
                        "total_ht": "75000.00",
                        "total_ttc": "90750.00",
                        "currency": "EUR",
                        "taxes": [{"label": "TVA 21 %", "rate": "0.21", "amount": "15750.00"}],
                    },
                },
                "terms": "Acompte de 30 % à la commande.",
            },
        ),
        (
            "bordereau vide",
            {"logo": fixtures.horizontal(), "document": {"lines": [], "totals": {}}},
        ),
        # Une ligne « non comptée au total » porte une mention SOUS elle et
        # dépense six points de plus que l'interligne. Une pagination qui
        # diviserait par l'interligne moyen en ferait tenir davantage qu'il n'y
        # a la place, et les dernières se dessineraient sous la marge.
        (
            "soixante lignes toutes non comptées",
            {
                "logo": fixtures.horizontal(),
                "document": {
                    "lines": [dict(ligne, included_in_total=False) for ligne in LIGNES_ORDINAIRES],
                    "totals": {"total_ht": "0.00", "total_ttc": "0.00", "currency": "EUR"},
                },
            },
        ),
        (
            "deux cent quarante lignes non comptées",
            {
                "logo": fixtures.carre(),
                "document": {
                    "lines": [
                        dict(ligne, included_in_total=False) for ligne in LIGNES_ORDINAIRES * 4
                    ],
                    "totals": {"total_ht": "0.00", "total_ttc": "0.00", "currency": "EUR"},
                },
            },
        ),
        # Le cas qui a révélé le chevauchement : une raison sociale en
        # CAPITALES, repliée sur la colonne étroite que laisse un logo. Les
        # capitales d'Helvetica valent jusqu'à 0,944 em ; la chasse moyenne de
        # 0,5 supposée par le repliement en autorisait vingt de trop, et la
        # ligne venait s'imprimer par-dessus le numéro du devis.
        (
            "raison sociale en capitales, avec logo",
            {
                "organisation": dict(
                    PROFIL,
                    name="DUBOIS TRAVAUX PUBLICS",
                    legal_name="SOCIETE ANONYME DES CARRIERES DU HAINAUT",
                ),
                "logo": fixtures.horizontal(),
            },
        ),
        (
            "tous les champs à leur maximum",
            {
                "organisation": EMETTEUR_MAXIMAL,
                "client": CLIENT_MAXIMAL,
                "projet": PROJET_MAXIMAL,
                "logo": fixtures.horizontal(),
                "terms": "T" * 4000,
            },
        ),
        (
            "maximum ET deux cents lignes",
            {
                "organisation": EMETTEUR_MAXIMAL,
                "client": CLIENT_MAXIMAL,
                "projet": PROJET_MAXIMAL,
                "logo": fixtures.carre(),
                "terms": "T" * 4000,
                "document": {
                    "lines": LIGNES_ORDINAIRES * 4,
                    "totals": {
                        "total_ht": "300000.00",
                        "total_ttc": "363000.00",
                        "currency": "EUR",
                        "taxes": [{"label": "TVA 21 %", "rate": "0.21", "amount": "63000.00"}],
                    },
                },
            },
        ),
    ],
)
def test_aucun_contenu_ne_sort_de_la_page(nom: str, remplacements: dict[str, Any]) -> None:
    """Mesuré, pas supposé : chaque coordonnée dessinée est dans la feuille.

    Ce test a trouvé un vrai défaut. L'en-tête réservait un nombre de points
    FIXE, ce qui tenait tant qu'il ne portait qu'un nom ; l'adresse et les
    coordonnées de l'émetteur l'ont fait déborder, et le tableau se dessinait à
    une ordonnée négative — hors de la page, donc invisible, sans que rien ne
    le signale. La pagination se calcule désormais sur la hauteur réellement
    occupée, et l'identité est bornée à douze lignes avec une ellipse visible.
    """
    fautifs = _hors_de_la_page(_coordonnees_dessinees(**remplacements))
    assert fautifs == [], f"{nom} : {len(fautifs)} élément(s) hors page — " + " ; ".join(
        fautifs[:5]
    )


def test_une_identite_demesuree_est_coupee_visiblement() -> None:
    """Coupée, et le disant : l'ellipse est la différence entre borner et mentir.

    Une entreprise qui remplit 255 caractères d'adresse doit voir que son
    en-tête ne dit pas tout — sur son écran d'aperçu et sur son document — au
    lieu de le découvrir chez son client.
    """
    trace = _coordonnees_dessinees(organisation=EMETTEUR_MAXIMAL)
    dessine = [t[3] for t in trace]
    assert any(contenu.endswith("…") for contenu in dessine), (
        "une identité tronquée doit porter une ellipse"
    )


def test_une_identite_ordinaire_n_est_jamais_coupee() -> None:
    """La borne ne doit pas mordre sur un profil réel."""
    trace = _coordonnees_dessinees()
    dessine = [t[3] for t in trace]
    assert not any(contenu.endswith("…") for contenu in dessine), dessine
    for attendu in (PROFIL["address"], PROFIL["address_complement"], PROFIL["website"]):
        assert any(attendu in contenu for contenu in dessine), attendu


def test_un_texte_qui_ressemble_a_un_objet_image_ne_fait_pas_disparaitre_la_page() -> None:
    """L'excision des flux d'image ne doit pas mordre sur le texte imprimé.

    `extraire_le_texte` saute les flux d'image pour ne pas lire leurs octets
    comme des chaînes. La recherche portait sur la seule marque `/Subtype
    /Image` : une désignation de poste qui contiendrait cette suite de
    caractères aurait fait couper tout ce qui suit jusqu'à la fin du flux de la
    page — une page entière disparue de la lecture, et une assertion d'ABSENCE
    qui passe pour de mauvaises raisons.
    """
    from datetime import date, datetime

    from metreo_api.services.quote_pdf import composer_le_devis

    piege = "Fourniture /Subtype /Image et pose"
    octets = composer_le_devis(
        numero="DEV-2026-0009",
        emis_le=datetime(2026, 1, 1, 12, 0),
        valid_until=date(2026, 12, 31),
        organisation=dict(PROFIL),
        client={"name": "Commune fictive", "billing_address": "Place 1", "city": "Namur"},
        projet={"reference": "CH-9", "name": "Chantier piégé", "currency": "EUR"},
        document={
            "lines": [
                {
                    "position": "01.10",
                    "designation": piege,
                    "unit": "m3",
                    "quantity": "1",
                    "unit_price_ht": "10.00",
                    "total_ht": "10.00",
                }
            ],
            "totals": {"total_ht": "10.00", "total_ttc": "12.10", "currency": "EUR"},
        },
        terms="Une condition qui suit le poste piégé.",
        include_internal_costs=False,
        logo=fixtures.carre(),
    )
    texte = moteur.extraire_le_texte(octets)
    # Le poste piégé est là...
    assert "Fourniture" in texte
    # ...et surtout ce qui vient APRÈS lui n'a pas disparu.
    assert "Une condition qui suit le poste" in texte
    assert PROFIL["name"] in texte


def test_une_adresse_demesuree_ne_chasse_pas_les_coordonnees() -> None:
    """Ce que l'en-tête coupe en dernier, c'est le moyen de répondre.

    Un plafond GLOBAL sur l'identité coupe la fin — donc le téléphone, le
    courriel et le site, précisément ce que cette fonctionnalité existe pour
    imprimer. Une adresse de 255 caractères chasserait du papier le moyen de
    joindre l'entreprise, et personne ne s'en apercevrait avant que le client
    ne cherche à répondre.

    Chaque partie a donc sa part. L'adresse démesurée est coupée, visiblement,
    et les coordonnées restent entières.
    """
    demesure = dict(PROFIL)
    demesure["address"] = "Avenue " + "Interminable " * 18
    demesure["address_complement"] = "Bâtiment " + "Annexe " * 18
    trace = _coordonnees_dessinees(organisation=demesure, logo=fixtures.horizontal())
    dessine = [t[3] for t in trace]
    joint = "\n".join(dessine)

    # L'adresse a bien été coupée, et le dit.
    assert any(contenu.endswith("…") for contenu in dessine)
    # Mais le téléphone, le courriel et le site sont imprimés en entier.
    assert PROFIL["phone"] in joint, "le téléphone a été chassé de l'en-tête"
    assert PROFIL["email"] in joint, "le courriel a été chassé de l'en-tête"
    assert PROFIL["website"] in joint, "le site a été chassé de l'en-tête"
    assert _hors_de_la_page(trace) == []


def test_cinq_taxes_ne_poussent_pas_le_total_ttc_hors_de_la_page() -> None:
    """Le bloc des totaux se mesure sur ce qu'il porte, pas sur une constante.

    Une entreprise qui configure plusieurs taxes — TVA, cocontractant,
    écotaxes — allonge ce bloc. Une hauteur fixe le poussait sous la marge, et
    le pavé « TOTAL TTC » est le chiffre que le client cherche en premier.
    """
    taxes = [{"label": f"Taxe {rang}", "rate": "0.06", "amount": "60.00"} for rang in range(5)]
    trace = _coordonnees_dessinees(
        logo=fixtures.carre(),
        document={
            "lines": LIGNES_ORDINAIRES,
            "totals": {
                "total_ht": "1000.00",
                "total_ttc": "1300.00",
                "currency": "EUR",
                "taxes": taxes,
            },
        },
    )
    assert _hors_de_la_page(trace) == []
    assert any("TOTAL À PAYER TTC" in t[3] for t in trace)
