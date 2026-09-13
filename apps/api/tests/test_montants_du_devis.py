"""Les MONTANTS d'un devis remis, sur les quatre surfaces où le client les lit.

Ce fichier existe parce qu'un devis a été émis, en vrai, avec « Total HT :
0 EUR » et « TOTAL À PAYER TTC : 0 EUR » sous des lignes pourtant chiffrées
juste. Le test qui aurait dû l'attraper vérifiait `"Total" in texte` : le MOT
était là, le montant non.

Deux principes, ici :

1. **Les attendus sont calculés hors du moteur.** Trois lignes, des prix à deux
   décimales, des quantités choisies pour que chaque produit tombe juste. Le
   test pose les six nombres à la main et vérifie d'abord sa propre
   arithmétique. Comparer le moteur à lui-même passerait quoi qu'il arrive.

2. **Un montant est toujours vérifié COLLÉ À SON LIBELLÉ.** Chercher
   « 5620.00 » quelque part dans la page ne prouve rien : ce nombre pourrait
   être le total d'une ligne, un cumul intermédiaire, ou l'écart d'un scénario.
   Ce qui compte est que « Total HT » soit suivi de 5 620,00 et de rien
   d'autre.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.config import get_settings
from metreo_api.services import pdf as moteur_pdf

from . import emission
from .conftest import login

# --------------------------------------------------------------------------
# Le bordereau, et les six nombres posés à la main
# --------------------------------------------------------------------------

#: position, désignation, unité, quantité, prix unitaire, total de la ligne.
LIGNES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("01.10", "Déblai en terrain meuble", "m3", "120", "18.50", "2220.00"),
    ("01.20", "Remblai compacté par couches", "m3", "85", "24.40", "2074.00"),
    ("01.30", "Évacuation en centre agréé", "t", "42.5", "31.20", "1326.00"),
)

TOTAL_HT = Decimal("5620.00")
TAUX_TVA = Decimal("0.21")
MONTANT_TVA = Decimal("1180.20")
TOTAL_TTC = Decimal("6800.20")


def test_0_l_arithmetique_attendue_se_verifie_elle_meme() -> None:
    """Le test se contrôle avant de contrôler le produit.

    Sans ce garde-fou, une faute de frappe dans un attendu rendrait le test
    faux ET vert : il suffirait que le moteur commette la même faute.
    """
    for _, _, _, quantite, prix, total_ligne in LIGNES:
        assert Decimal(quantite) * Decimal(prix) == Decimal(total_ligne)
    assert sum(Decimal(ligne[5]) for ligne in LIGNES) == TOTAL_HT
    assert TOTAL_HT * TAUX_TVA == MONTANT_TVA
    assert TOTAL_HT + MONTANT_TVA == TOTAL_TTC


# --------------------------------------------------------------------------
# Le montage
# --------------------------------------------------------------------------


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def sans_marge(seeded_client: TestClient, admin: dict[str, str]) -> None:
    """Frais et marge à zéro, pour que le prix de vente soit le prix saisi.

    Ce n'est pas un contournement : la chaîne commerciale a ses propres tests.
    Ici, on veut que 120 × 18,50 vaille 2 220,00 et rien d'autre, sans quoi
    l'attendu ne serait plus calculable à la main.
    """
    reponse = seeded_client.patch(
        "/api/v1/organization/settings",
        headers=admin,
        json={
            "site_overheads_rate": "0",
            "general_overheads_rate": "0",
            "contingency_rate": "0",
            "margin_rate": "0",
        },
    )
    assert reponse.status_code == 200, reponse.text


def _chantier_gele(
    seeded_client: TestClient, admin: dict[str, str], reference: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Un chantier neuf à trois postes, chiffré et GELÉ — pas encore émis."""
    livre = seeded_client.get("/api/v1/price-books", headers=admin).json()[0]
    version_prix = seeded_client.get(
        f"/api/v1/price-books/{livre['id']}/versions", headers=admin
    ).json()[0]["id"]

    articles = []
    for position, designation, unite, _, prix, _ in LIGNES:
        cree = seeded_client.post(
            f"/api/v1/price-books/versions/{version_prix}/items",
            headers=admin,
            json={
                "code": f"{reference}-{position}",
                "label": designation,
                "unit_code": unite,
                "unit_price": prix,
                "resource_kind": "subcontract",
            },
        )
        assert cree.status_code == 201, cree.text
        articles.append(cree.json()["id"])

    destinataire = emission.fiche(
        seeded_client, admin, name=f"Commune fictive de Jodoigne ({reference})"
    )
    projet = seeded_client.post(
        "/api/v1/projects",
        headers=admin,
        json={
            "reference": reference,
            "name": "Chantier des montants",
            "client_id": destinataire["id"],
        },
    ).json()
    bordereau = seeded_client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=admin, json={"name": "Métré"}
    ).json()
    for (position, designation, unite, quantite, _, _), article in zip(
        LIGNES, articles, strict=True
    ):
        poste = seeded_client.post(
            f"/api/v1/boqs/{bordereau['id']}/items",
            headers=admin,
            json={
                "position": position,
                "designation": designation,
                "unit_code": unite,
                "quantity": quantite,
                "price_item_id": article,
            },
        )
        assert poste.status_code == 201, poste.text

    estimation = seeded_client.post(
        "/api/v1/estimates",
        headers=admin,
        json={
            "project_id": projet["id"],
            "boq_id": bordereau["id"],
            "price_book_version_id": version_prix,
            "name": "Étude des montants",
        },
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]

    # Le moteur DOIT tomber sur les nombres posés à la main. Si ce contrôle
    # tombe, c'est le calcul qui a changé, pas l'impression — et il faut le
    # savoir ici plutôt que trois assertions plus bas.
    calcul = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions/{version['id']}/computation",
        headers=admin,
    ).json()["result"]
    assert calcul["total_selling_price_ht"] == str(TOTAL_HT)
    assert calcul["total_ttc"] == str(TOTAL_TTC)

    emission.geler(seeded_client, admin, estimation, version)
    return estimation, version


@pytest.fixture()
def devis(seeded_client: TestClient, admin: dict[str, str], sans_marge: None) -> dict[str, Any]:
    estimation, version = _chantier_gele(seeded_client, admin, "MNT-2026-001")
    remis = emission.emettre(seeded_client, admin, estimation, version)
    assert remis.status_code == 201, remis.text
    return dict(remis.json(), estimate=estimation, version=version)


def _texte(octets: bytes) -> str:
    """Le texte imprimé, espaces normalisés — un saut de ligne ne doit pas
    défaire l'adjacence d'un libellé et de son montant."""
    return re.sub(r"\s+", " ", moteur_pdf.extraire_le_texte(octets))


def _suivi_de(texte: str, libelle: str, montant: Decimal | str) -> bool:
    """« libellé » puis son montant, sans autre nombre entre les deux."""
    motif = rf"{re.escape(libelle)}\s*:?\s*{re.escape(str(montant))}\b"
    return re.search(motif, texte) is not None


def _lien(client: TestClient, entetes: dict[str, str], devis: dict[str, Any]) -> str:
    cree = client.post(f"/api/v1/issued-quotes/{devis['id']}/share-links", headers=entetes, json={})
    assert cree.status_code == 201, cree.text
    url: str = cree.json()["url"]
    return url


def _session_publique(client: TestClient, url: str) -> None:
    secret = url.split("#", 1)[1]
    ouverte = client.post("/api/v1/public/quote-sessions", json={"secret": secret})
    assert ouverte.status_code in (200, 201, 204), ouverte.text


# --------------------------------------------------------------------------
# Les quatre surfaces
# --------------------------------------------------------------------------


def test_1_le_pdf_emis_porte_chaque_montant_colle_a_son_libelle(
    seeded_client: TestClient, admin, devis
) -> None:
    octets = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).content
    texte = _texte(octets)

    # Les trois lignes, chacune avec son total.
    for position, designation, _, _, _, total_ligne in LIGNES:
        assert position in texte
        assert designation[:20] in texte
        assert total_ligne in texte, f"le total de la ligne {position} manque"

    # Et les totaux du DOCUMENT, chacun accolé à son intitulé.
    assert _suivi_de(texte, "Total HT", TOTAL_HT), texte[-400:]
    assert _suivi_de(texte, "TVA 21 %", MONTANT_TVA), texte[-400:]
    assert _suivi_de(texte, "TOTAL À PAYER TTC", TOTAL_TTC), texte[-400:]

    # Le défaut d'origine, nommé : le mot était là, le montant valait zéro.
    assert not _suivi_de(texte, "Total HT", "0"), "« Total HT » suivi de zéro"
    assert not _suivi_de(texte, "TOTAL À PAYER TTC", "0"), "« TTC » suivi de zéro"


def test_2_le_tableau_des_devis_porte_le_meme_ttc(seeded_client: TestClient, admin, devis) -> None:
    tableau = seeded_client.get("/api/v1/quotes", headers=admin).json()
    ligne = next(q for q in tableau["items"] if q["id"] == devis["id"])
    assert ligne["total_ttc"] == str(TOTAL_TTC)
    assert ligne["currency"] == "EUR"


def test_3_la_page_publique_porte_les_memes_montants(
    seeded_client: TestClient, admin, devis
) -> None:
    url = _lien(seeded_client, admin, devis)
    _session_publique(seeded_client, url)
    vue = seeded_client.get("/api/v1/public/quote").json()

    assert vue["total_ht"] == str(TOTAL_HT)
    assert vue["total_ttc"] == str(TOTAL_TTC)
    assert vue["currency"] == "EUR"
    taxes = vue["taxes"]
    assert len(taxes) == 1, taxes
    assert taxes[0]["amount"] == str(MONTANT_TVA)
    assert "21" in str(taxes[0]["label"]) or "21" in str(taxes[0]["rate"])

    # Les trois lignes y sont aussi, avec leurs totaux.
    par_position = {ligne["position"]: ligne for ligne in vue["lines"]}
    for position, _, _, quantite, prix, total_ligne in LIGNES:
        ligne = par_position[position]
        assert ligne["total_ht"] == total_ligne, f"ligne {position} : total faux"
        assert ligne["unit_price_ht"] == prix, f"ligne {position} : prix unitaire faux"
        assert Decimal(ligne["quantity"]) == Decimal(quantite)


def test_4_le_document_telecharge_par_le_client_est_le_meme_fichier(
    seeded_client: TestClient, admin, devis
) -> None:
    interne = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).content
    url = _lien(seeded_client, admin, devis)
    _session_publique(seeded_client, url)
    public = seeded_client.get("/api/v1/public/quote/document.pdf").content

    assert hashlib.sha256(public).hexdigest() == hashlib.sha256(interne).hexdigest()
    assert hashlib.sha256(public).hexdigest() == devis["pdf_sha256"]
    # Et il porte bien les montants : un fichier identique à un fichier faux
    # serait identiquement faux.
    texte = _texte(public)
    assert _suivi_de(texte, "Total HT", TOTAL_HT)
    assert _suivi_de(texte, "TOTAL À PAYER TTC", TOTAL_TTC)


# --------------------------------------------------------------------------
# Le refus
# --------------------------------------------------------------------------


def _pdfs_sur_le_volume() -> list[Path]:
    racine = Path(get_settings().storage_root) / "devis"
    return sorted(racine.rglob("*.pdf")) if racine.exists() else []


def test_5_un_calcul_sans_totaux_refuse_sans_laisser_ni_devis_ni_fichier(
    seeded_client: TestClient, admin, sans_marge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un total introuvable arrête l'émission AVANT qu'elle écrive quoi que ce soit.

    Injection de panne plutôt que mise en scène : on remplace la lecture des
    totaux par celle qui avait le défaut — chercher une clé `"totals"` que le
    calcul ne produit pas. C'est exactement l'état d'avant la correction.
    """
    from metreo_api.routers import estimates as routeur
    from metreo_api.services import issuance

    estimation, version = _chantier_gele(seeded_client, admin, "MNT-REFUS-001")

    avant_fichiers = _pdfs_sur_le_volume()
    avant_devis = seeded_client.get(
        f"/api/v1/projects/{estimation['project_id']}/issued-quotes", headers=admin
    ).json()

    lecture_corrigee = issuance.totaux_du_document

    def totaux_de_l_ancien_defaut(payload: dict[str, Any], devise: str) -> dict[str, Any]:
        # La fonction d'origine, telle qu'elle était : elle cherchait les
        # totaux sous une clé `"totals"` que le calcul ne produit pas.
        return lecture_corrigee(payload.get("totals") or {}, devise)

    monkeypatch.setattr(routeur.issuance, "totaux_du_document", totaux_de_l_ancien_defaut)

    refus = emission.emettre(seeded_client, admin, estimation, version)
    assert refus.status_code >= 400, "l'émission a abouti alors que les totaux manquaient"
    assert "total" in refus.text.lower()

    # Rien n'a été écrit : ni ligne de devis, ni fichier sur le volume.
    apres_devis = seeded_client.get(
        f"/api/v1/projects/{estimation['project_id']}/issued-quotes", headers=admin
    ).json()
    assert apres_devis == avant_devis, "un devis a été créé malgré le refus"
    assert _pdfs_sur_le_volume() == avant_fichiers, "un PDF partiel est resté sur le volume"
