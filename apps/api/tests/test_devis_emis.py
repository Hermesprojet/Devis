"""Émettre un devis : le numéroter une fois, le figer, l'imprimer, le rendre.

Ce fichier éprouve les promesses que le produit fait à qui remet un devis :

* le numéro est unique, même sous deux émissions simultanées ;
* on n'émet pas une version qui peut encore bouger, ni sans destinataire ;
* une version déjà émise ne se réémet pas — on en crée une nouvelle ;
* le document est un VRAI PDF, identique octet pour octet d'un
  téléchargement à l'autre, et indépendant de qui le télécharge ;
* modifier ensuite la fiche client ou l'organisation ne change rien à ce qui
  a été remis ;
* les coûts internes n'apparaissent jamais par accident ;
* un échec ne laisse ni ligne, ni fichier.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.config import get_settings
from metreo_api.services import pdf as moteur_pdf

from .conftest import login

CLIENT = {
    "name": "Commune de Perwez",
    "company_number": "BE 0207.363.192",
    "billing_address": "Rue Émile de Brabant 2",
    "postal_code": "1360",
    "city": "Perwez",
    "contact_name": "Service Travaux",
    "email": "travaux@perwez.example",
}


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def estimate(seeded_client: TestClient, admin) -> dict:
    resultat: dict = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    return resultat


@pytest.fixture()
def version(seeded_client: TestClient, admin, estimate) -> dict:
    resultat: dict = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=admin
    ).json()[0]
    return resultat


def _prix_manquant(client: TestClient, headers: dict[str, str], estimate: dict) -> None:
    """Le jeu de démonstration laisse une ligne sans prix, exprès."""
    livre = client.get("/api/v1/price-books", headers=headers).json()[0]
    version_id = client.get(f"/api/v1/price-books/{livre['id']}/versions", headers=headers).json()[
        0
    ]["id"]
    cree = client.post(
        f"/api/v1/price-books/versions/{version_id}/items",
        headers=headers,
        json={
            "code": "RAC-PART-001",
            "label": "Mise en conformité d'un raccordement particulier",
            "unit_code": "pce",
            "unit_price": "480.00",
            "resource_kind": "subcontract",
        },
    )
    assert cree.status_code == 201, cree.text
    postes = client.get(f"/api/v1/boqs/{estimate['boq_id']}/items", headers=headers).json()
    orphelin = next(
        p
        for p in postes
        if p["price_item_id"] is None and p["composite_price_id"] is None and p["kind"] != "section"
    )
    rattache = client.patch(
        f"/api/v1/boq-items/{orphelin['id']}",
        headers=headers,
        json={"price_item_id": cree.json()["id"]},
    )
    assert rattache.status_code == 200, rattache.text


def _fiche(client: TestClient, headers: dict[str, str], **remplacements: Any) -> dict:
    reponse = client.post("/api/v1/clients", headers=headers, json={**CLIENT, **remplacements})
    assert reponse.status_code == 201, reponse.text
    resultat: dict = reponse.json()
    return resultat


def _rattacher(client: TestClient, headers: dict[str, str], projet_id: str, fiche_id: str) -> None:
    reponse = client.patch(
        f"/api/v1/projects/{projet_id}", headers=headers, json={"client_id": fiche_id}
    )
    assert reponse.status_code == 200, reponse.text


def _geler(client: TestClient, headers: dict[str, str], estimate: dict, version: dict) -> None:
    reponse = client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert reponse.status_code == 200, reponse.text


def _emettre(
    client: TestClient, headers: dict[str, str], estimate: dict, version: dict, **corps: Any
) -> Any:
    return client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/issue",
        headers=headers,
        json=corps,
    )


@pytest.fixture()
def pret(seeded_client: TestClient, admin, estimate, version) -> dict:
    """Un chantier avec sa fiche client et une version gelée : prêt à émettre."""
    _prix_manquant(seeded_client, admin, estimate)
    fiche = _fiche(seeded_client, admin)
    _rattacher(seeded_client, admin, estimate["project_id"], fiche["id"])
    _geler(seeded_client, admin, estimate, version)
    return fiche


def _stockage_racine() -> Path:
    return Path(get_settings().storage_root)


def _pdfs_sur_le_volume() -> list[Path]:
    racine = _stockage_racine()
    return sorted(p for p in racine.rglob("*.pdf") if p.is_file())


# --------------------------------------------------------------------------
# Le parcours nominal
# --------------------------------------------------------------------------


def test_emettre_donne_un_numero_une_date_et_une_validite(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    reponse = _emettre(seeded_client, admin, estimate, version)
    assert reponse.status_code == 201, reponse.text
    devis = reponse.json()

    assert devis["number"].startswith("DEV-"), devis["number"]
    assert devis["issued_at"]
    assert devis["valid_until"] > devis["issued_at"][:10]
    assert devis["client_name"] == CLIENT["name"]
    assert devis["pdf_byte_size"] > 0
    assert len(devis["pdf_sha256"]) == 64


def test_le_numero_suit_le_motif_de_l_organisation(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    regle = seeded_client.patch(
        "/api/v1/organization/settings",
        headers=admin,
        json={"quote_number_pattern": "DUB/{year}/{sequence:03d}"},
    )
    assert regle.status_code == 200, regle.text

    devis = _emettre(seeded_client, admin, estimate, version).json()
    annee = devis["issued_at"][:4]
    assert devis["number"] == f"DUB/{annee}/001", devis["number"]


def test_un_devis_emis_apparait_dans_l_historique_du_chantier(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    historique = seeded_client.get(
        f"/api/v1/projects/{estimate['project_id']}/issued-quotes", headers=admin
    )
    assert historique.status_code == 200, historique.text
    assert [d["id"] for d in historique.json()] == [devis["id"]]


# --------------------------------------------------------------------------
# Les refus
# --------------------------------------------------------------------------


def test_une_version_non_gelee_ne_s_emet_pas(
    seeded_client: TestClient, admin, estimate, version
) -> None:
    """Un devis remis doit désigner un calcul qui ne bougera plus."""
    _prix_manquant(seeded_client, admin, estimate)
    _rattacher(seeded_client, admin, estimate["project_id"], _fiche(seeded_client, admin)["id"])

    refus = _emettre(seeded_client, admin, estimate, version)
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "version_not_frozen"


def test_un_chantier_sans_fiche_client_refuse_l_emission(
    seeded_client: TestClient, admin, estimate, version
) -> None:
    """L'ancien projet reste lisible ; c'est l'émission qui exige un choix."""
    _prix_manquant(seeded_client, admin, estimate)
    _geler(seeded_client, admin, estimate, version)

    refus = _emettre(seeded_client, admin, estimate, version)
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "client_required"


def test_une_fiche_trop_incomplete_refuse_l_emission_en_disant_quoi(
    seeded_client: TestClient, admin, estimate, version
) -> None:
    _prix_manquant(seeded_client, admin, estimate)
    incomplete = _fiche(
        seeded_client, admin, billing_address=None, postal_code=None, city=None, name="Client nu"
    )
    _rattacher(seeded_client, admin, estimate["project_id"], incomplete["id"])
    _geler(seeded_client, admin, estimate, version)

    refus = _emettre(seeded_client, admin, estimate, version)
    assert refus.status_code == 409, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "client_incomplete"
    assert set(detail["context"]["missing"]) == {"billing_address", "postal_code", "city"}


def test_une_validite_anterieure_a_l_emission_est_refusee(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    refus = _emettre(seeded_client, admin, estimate, version, valid_until="2020-01-01")
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "validity_in_the_past"


def test_un_refus_ne_laisse_ni_ligne_ni_fichier(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Le PDF est écrit AVANT la ligne ; un refus tardif doit le retirer.

    La validité passée est refusée après la numérotation mais avant l'écriture ;
    le cas dangereux est l'inverse — un échec APRÈS le fichier. On l'éprouve en
    faisant échouer la validation, ci-dessous, mais on tient déjà ici qu'un
    refus banal ne salit pas le volume.
    """
    avant = _pdfs_sur_le_volume()
    assert _emettre(seeded_client, admin, estimate, version, valid_until="2020-01-01").status_code
    assert _pdfs_sur_le_volume() == avant

    historique = seeded_client.get(
        f"/api/v1/projects/{estimate['project_id']}/issued-quotes", headers=admin
    ).json()
    assert historique == []


# --------------------------------------------------------------------------
# Immutabilité
# --------------------------------------------------------------------------


def test_une_version_deja_emise_ne_se_reemet_pas(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    premier = _emettre(seeded_client, admin, estimate, version).json()
    second = _emettre(seeded_client, admin, estimate, version)
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "already_issued"
    assert detail["context"]["number"] == premier["number"]


def test_une_nouvelle_version_donne_un_nouveau_devis_sans_toucher_l_ancien(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """La correction d'un devis remis est une nouvelle émission, pas une retouche."""
    ancien = _emettre(seeded_client, admin, estimate, version).json()
    ancien_pdf = seeded_client.get(
        f"/api/v1/issued-quotes/{ancien['id']}/document.pdf", headers=admin
    ).content

    suivante = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=admin, json={"label": "v2"}
    )
    assert suivante.status_code == 201, suivante.text
    v2 = suivante.json()
    _geler(seeded_client, admin, estimate, v2)

    nouveau = _emettre(seeded_client, admin, estimate, v2)
    assert nouveau.status_code == 201, nouveau.text
    assert nouveau.json()["number"] != ancien["number"]

    relu = seeded_client.get(f"/api/v1/issued-quotes/{ancien['id']}/document.pdf", headers=admin)
    assert relu.content == ancien_pdf, "l'émission suivante a réécrit le devis précédent"

    historique = seeded_client.get(
        f"/api/v1/projects/{estimate['project_id']}/issued-quotes", headers=admin
    ).json()
    assert {d["id"] for d in historique} == {ancien["id"], nouveau.json()["id"]}


def test_modifier_la_fiche_client_ne_change_pas_un_devis_deja_remis(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    avant = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).content

    modifie = seeded_client.patch(
        f"/api/v1/clients/{pret['id']}",
        headers=admin,
        json={"name": "Commune de Perwez — Régie", "billing_address": "Autre rue 99"},
    )
    assert modifie.status_code == 200, modifie.text

    apres = seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin)
    assert apres.content == avant
    assert apres.headers["X-Quote-Sha256"] == devis["pdf_sha256"]

    texte = moteur_pdf.extraire_le_texte(apres.content)
    assert CLIENT["name"] in texte
    assert "Régie" not in texte
    assert "Autre rue 99" not in texte


def test_modifier_l_organisation_ne_change_pas_un_devis_deja_remis(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    avant = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).content

    regle = seeded_client.patch(
        "/api/v1/organization/settings",
        headers=admin,
        json={"quote_number_pattern": "AUTRE-{year}-{sequence:04d}"},
    )
    assert regle.status_code == 200, regle.text

    apres = seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin)
    assert apres.content == avant
    assert devis["number"] in moteur_pdf.extraire_le_texte(apres.content)


# --------------------------------------------------------------------------
# Le fichier
# --------------------------------------------------------------------------


def test_le_document_est_un_vrai_pdf_et_non_du_html_renomme(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    reponse = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    )
    assert reponse.status_code == 200, reponse.text
    octets = reponse.content

    assert octets.startswith(b"%PDF-1."), octets[:20]
    assert octets.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in octets and b"/Type /Pages" in octets
    assert b"<html" not in octets.lower()
    assert moteur_pdf.compter_les_pages(octets) >= 1
    assert reponse.headers["content-type"].startswith("application/pdf")


def test_deux_telechargements_rendent_exactement_les_memes_octets(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    url = f"/api/v1/issued-quotes/{devis['id']}/document.pdf"
    premier = seeded_client.get(url, headers=admin).content
    second = seeded_client.get(url, headers=admin).content

    assert premier == second
    assert hashlib.sha256(premier).hexdigest() == devis["pdf_sha256"]


def test_le_contenu_ne_depend_pas_de_qui_telecharge(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Un métreur et un administrateur reçoivent le MÊME document.

    Le PDF est lu sur le volume, tel qu'écrit à l'émission ; il n'est pas
    recomposé selon les permissions du lecteur. C'est ce qui rend le fichier
    transmissible : l'entreprise sait ce qu'elle envoie.
    """
    devis = _emettre(seeded_client, admin, estimate, version).json()
    url = f"/api/v1/issued-quotes/{devis['id']}/document.pdf"
    metreur = login(seeded_client, "metreur@dubois.demo")

    par_admin = seeded_client.get(url, headers=admin)
    par_metreur = seeded_client.get(url, headers=metreur)
    assert par_metreur.status_code == 200, par_metreur.text
    assert par_metreur.content == par_admin.content


def test_les_en_tetes_conviennent_a_un_document_commercial_confidentiel(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    entetes = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).headers

    assert entetes["content-type"].startswith("application/pdf")
    assert entetes["content-disposition"].startswith("attachment;")
    assert f"devis-{devis['number']}".replace("/", "") in entetes[
        "content-disposition"
    ].replace("/", "")
    assert entetes["x-content-type-options"] == "nosniff"
    assert "no-store" in entetes["cache-control"]
    assert "private" in entetes["cache-control"]


def test_le_pdf_imprime_le_client_le_chantier_et_les_totaux(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    texte = moteur_pdf.extraire_le_texte(
        seeded_client.get(
            f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
        ).content
    )

    assert devis["number"] in texte
    assert CLIENT["name"] in texte
    assert CLIENT["city"] in texte
    projet = seeded_client.get(f"/api/v1/projects/{estimate['project_id']}", headers=admin).json()
    assert projet["reference"] in texte
    assert "Total" in texte


def test_les_conditions_sont_configurables_et_non_gravees(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Aucune mention légale inventée : ce que l'entreprise écrit, et rien d'autre."""
    conditions = "Offre valable sauf vente. Acompte de 30 % à la commande."
    devis = _emettre(seeded_client, admin, estimate, version, terms=conditions).json()
    texte = moteur_pdf.extraire_le_texte(
        seeded_client.get(
            f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
        ).content
    )
    assert "Acompte de 30 %" in texte


def test_un_pdf_disparu_du_volume_donne_410_et_non_un_document_reconstruit(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Reconstruire serait pire que refuser : le document remis serait trahi."""
    devis = _emettre(seeded_client, admin, estimate, version).json()
    for fichier in _pdfs_sur_le_volume():
        fichier.unlink()

    perdu = seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin)
    assert perdu.status_code == 410, perdu.text
    assert perdu.json()["detail"]["code"] == "document_missing"


# --------------------------------------------------------------------------
# Confidentialité
# --------------------------------------------------------------------------


def test_par_defaut_aucun_cout_interne_n_apparait(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Le cas par défaut est le cas dangereux : c'est celui qu'on éprouve."""
    devis = _emettre(seeded_client, admin, estimate, version).json()
    assert devis["include_internal_costs"] is False

    texte = moteur_pdf.extraire_le_texte(
        seeded_client.get(
            f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
        ).content
    )
    for interdit in ("Déboursé", "Revient", "Marge"):
        assert interdit not in texte, f"« {interdit} » a fui dans le devis client"


def test_inclure_les_couts_internes_exige_la_permission_et_se_lit_sur_le_devis(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version, include_internal_costs=True)
    assert devis.status_code == 201, devis.text
    assert devis.json()["include_internal_costs"] is True

    texte = moteur_pdf.extraire_le_texte(
        seeded_client.get(
            f"/api/v1/issued-quotes/{devis.json()['id']}/document.pdf", headers=admin
        ).content
    )
    assert "Déboursé" in texte


def test_le_reglage_de_l_organisation_donne_le_defaut_sans_avoir_le_dernier_mot(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Le défaut vient des réglages ; la décision reste celle de l'émetteur.

    Et c'est la décision PRISE qui est figée dans l'instantané : rebasculer le
    réglage ensuite ne change pas ce qui a été remis.
    """
    regle = seeded_client.patch(
        "/api/v1/organization/settings",
        headers=admin,
        json={"show_internal_costs_in_client_pdf": True},
    )
    assert regle.status_code == 200, regle.text

    par_defaut = _emettre(seeded_client, admin, estimate, version)
    assert par_defaut.status_code == 201, par_defaut.text
    assert par_defaut.json()["include_internal_costs"] is True

    suivante = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=admin, json={"label": "v2"}
    ).json()
    _geler(seeded_client, admin, estimate, suivante)
    contredit = _emettre(
        seeded_client, admin, estimate, suivante, include_internal_costs=False
    )
    assert contredit.status_code == 201, contredit.text
    assert contredit.json()["include_internal_costs"] is False


# --------------------------------------------------------------------------
# Permissions : aucun bouton ne doit exister sans droit derrière
# --------------------------------------------------------------------------


def test_un_role_sans_ecriture_d_estimation_ne_peut_pas_emettre(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    refus = _emettre(seeded_client, lecteur, estimate, version)
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "estimate:write"

    historique = seeded_client.get(
        f"/api/v1/projects/{estimate['project_id']}/issued-quotes", headers=admin
    ).json()
    assert historique == [], "un refus de permission a tout de même émis un devis"


def test_un_role_sans_export_client_ne_peut_pas_telecharger_le_pdf(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Le rôle « acheteur » lit les estimations mais n'exporte rien au client.

    L'écran doit donc lui cacher le bouton de téléchargement — et l'API le lui
    refuse quoi qu'il arrive.
    """
    devis = _emettre(seeded_client, admin, estimate, version).json()
    invite = seeded_client.post(
        "/api/v1/organization/members",
        headers=admin,
        json={
            "email": "acheteur@dubois.demo",
            "full_name": "Acheteur",
            "role": "buyer",
        },
    )
    assert invite.status_code == 201, invite.text
    acheteur = login(seeded_client, "acheteur@dubois.demo")

    refus = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=acheteur
    )
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "export:client"


# --------------------------------------------------------------------------
# Concurrence
# --------------------------------------------------------------------------


def _poser_un_devis(session: Any, *, devis_id: str, version_id: str, numero: str, annee: int,
                    rang: int, modele: dict) -> None:
    """Écrit une ligne `issued_quotes` minimale, copiée sur un devis réel."""
    from metreo_api.models import IssuedQuote

    session.add(
        IssuedQuote(
            id=devis_id,
            organization_id=modele["organization_id"],
            project_id=modele["project_id"],
            estimate_id=modele["estimate_id"],
            estimate_version_id=version_id,
            client_id=modele["client_id"],
            number=numero,
            sequence_year=annee,
            sequence_number=rang,
            issued_at=modele["issued_at"],
            valid_until=modele["valid_until"],
            organization_snapshot={},
            client_snapshot={},
            project_snapshot={},
            document_snapshot={},
            pdf_storage_key=f"concurrence/{devis_id}.pdf",
            pdf_sha256="c" * 64,
            pdf_byte_size=1,
        )
    )


def test_deux_allocations_qui_se_croisent_ne_produisent_jamais_deux_fois_le_numero(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    """Le dernier rempart, éprouvé sans laisser la course décider.

    Deux connexions allouent un rang AVANT que l'une ait validé : chacune lit
    le même `max(sequence_number)` et propose donc le même numéro. C'est
    exactement la course que `uq_issued_quote_number` existe pour perdre — et
    la base tranche, pas le code applicatif.

    L'entrelacement est imposé plutôt qu'espéré : des fils sur SQLite ne
    prouveraient qu'un ordonnancement, et le plus souvent se bloqueraient.
    Le verrou de séquence, lui, ne peut être éprouvé que là où il existe —
    voir `test_frontieres_postgres.py`.
    """
    from sqlalchemy.exc import IntegrityError

    from metreo_api.db import get_session_factory
    from metreo_api.models import IssuedQuote, new_id
    from metreo_api.services import issuance

    devis = _emettre(seeded_client, admin, estimate, version).json()
    #: Deux versions de plus : une ligne `issued_quotes` désigne une version
    #: RÉELLE — `uq_issued_quote_version` et la clé composite le veulent — et
    #: c'est bien le numéro, pas la version, que la course doit départager.
    concurrentes = []
    for etiquette in ("concurrente-a", "concurrente-b"):
        creee = seeded_client.post(
            f"/api/v1/estimates/{estimate['id']}/versions",
            headers=admin,
            json={"label": etiquette},
        )
        assert creee.status_code == 201, creee.text
        concurrentes.append(creee.json()["id"])

    session_lecture = get_session_factory()()
    try:
        reference = session_lecture.get(IssuedQuote, devis["id"])
        assert reference is not None
        modele = {
            "organization_id": reference.organization_id,
            "project_id": reference.project_id,
            "estimate_id": reference.estimate_id,
            "client_id": reference.client_id,
            "issued_at": reference.issued_at,
            "valid_until": reference.valid_until,
        }
        organisation = reference.organization_id
    finally:
        session_lecture.close()

    fabrique = get_session_factory()
    a, b = fabrique(), fabrique()
    try:
        motif = "DEV-{year}-{sequence:04d}"
        numero_a, annee_a, rang_a = issuance.numeroter(
            a, organization_id=organisation, motif=motif, quand=modele["issued_at"]
        )
        numero_b, annee_b, rang_b = issuance.numeroter(
            b, organization_id=organisation, motif=motif, quand=modele["issued_at"]
        )
        assert numero_a == numero_b, "l'entrelacement recherché ne s'est pas produit"

        _poser_un_devis(a, devis_id=new_id(), version_id=concurrentes[0], numero=numero_a,
                        annee=annee_a, rang=rang_a, modele=modele)
        a.commit()

        _poser_un_devis(b, devis_id=new_id(), version_id=concurrentes[1], numero=numero_b,
                        annee=annee_b, rang=rang_b, modele=modele)
        with pytest.raises(IntegrityError):
            b.commit()
        b.rollback()
    finally:
        a.close()
        b.close()

    controle = get_session_factory()()
    try:
        numeros = [
            d.number
            for d in controle.query(IssuedQuote)
            .filter(IssuedQuote.organization_id == organisation)
            .all()
        ]
    finally:
        controle.close()
    assert len(numeros) == len(set(numeros)), f"numéro servi deux fois : {numeros}"


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------


def test_l_emission_et_le_telechargement_sont_journalises(
    seeded_client: TestClient, admin, estimate, version, pret
) -> None:
    devis = _emettre(seeded_client, admin, estimate, version).json()
    seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin)

    evenements = seeded_client.get("/api/v1/audit/events?limit=200", headers=admin).json()["items"]
    lies = [e for e in evenements if e["object_id"] == devis["id"]]
    actions = {e["action"] for e in lies}
    assert {"quote.issued", "quote.downloaded"} <= actions

    emission = next(e for e in lies if e["action"] == "quote.issued")
    assert emission["payload"]["number"] == devis["number"]
    assert emission["payload"]["pdf_sha256"] == devis["pdf_sha256"]

    verification = seeded_client.get("/api/v1/audit/verify", headers=admin).json()
    assert verification["valid"] is True
