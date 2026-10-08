"""Une mesure tranchée devient une ligne de bordereau, puis un montant de devis.

**Le trou que ces tests ferment.** `calibration_de_plan.reprenables()` disait
depuis plusieurs livraisons quelles mesures un bordereau a le droit de
reprendre. Rien ne les reprenait : la règle existait sans point d'application,
et une quantité mesurée sur un plan ne pouvait arriver dans un devis que
recopiée à la main — donc sans provenance.

Ce fichier éprouve le chemin complet, et dans cet ordre :

    mesure pointée → décision humaine → ligne de bordereau
      → calcul du devis → PDF remis au client

**Ce qu'il vérifie, et que rien d'autre ne vérifie.**

1. la quantité reprise est la valeur **RETENUE**, jamais la proposition brute ;
2. l'unité est conservée, ou convertie **exactement** si la personne le demande ;
3. la **provenance** survit : le lien vers la proposition ET l'empreinte figée ;
4. une mesure **rejetée** ou **non tranchée** est refusée, chacune avec son motif ;
5. la même mesure ne se reprend **pas deux fois** dans un bordereau ;
6. la quantité reprise se retrouve dans le **calcul** puis dans le **PDF**.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import lisible
from metreo_api.services import pdf as moteur_pdf

from . import emission
from .conftest import login
from .test_calibration_de_plan_api import _calibrer, _mesurer, _relire

pytest.importorskip(
    "pypdfium2",
    reason="l'extra « pdf » n'est pas installé : pip install './apps/api[pdf]'",
)

try:
    from scripts import fabriquer_pdf_de_test as pdf_fixtures
except ImportError:  # pragma: no cover - chemin de secours, comme ailleurs
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import fabriquer_pdf_de_test as pdf_fixtures  # type: ignore[no-redef]

ADMIN = "admin@dubois.demo"


# ---------------------------------------------------------------------------
# Le montage : un plan mesuré DANS le chantier déjà chiffré du jeu de démo
# ---------------------------------------------------------------------------
#
# Dans le chantier du jeu de démonstration, et non dans un projet à part : la
# question posée est « une mesure de plan peut-elle alimenter un devis », et un
# bordereau fabriqué pour l'occasion n'y répondrait qu'à moitié. Celui-ci porte
# déjà des postes, une bibliothèque de prix et une version d'étude.


def _mesure_tranchee(
    client: TestClient,
    entetes: dict[str, str],
    projet_id: str,
    *,
    decision: str | None,
    corrigee: str | None = None,
    type_de_mesure: str = "segment",
    libelle: str = "Façade sud",
) -> dict:
    """Un plan déposé sur ce projet, calibré, mesuré, puis tranché.

    `decision=None` laisse la mesure sans décision : c'est l'état de départ de
    toute mesure, et l'un des deux refus que la reprise doit opposer.
    """
    document = client.post(
        f"/api/v1/projects/{projet_id}/documents", headers=entetes, json={"title": libelle}
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]

    revision = client.post(
        f"/api/v1/documents/{document_id}/revisions",
        headers=entetes,
        files={"file": ("plan.pdf", pdf_fixtures.page_avec_plusieurs_textes(), "application/pdf")},
    )
    assert revision.status_code == 201, revision.text
    revision_id = revision.json()["id"]

    analyse = client.post(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/analyse", headers=entetes
    )
    assert analyse.status_code == 200, analyse.text

    assert _calibrer(client, entetes, document_id, revision_id).status_code == 201
    points = (
        [(0.0, 0.5), (0.25, 0.5)]
        if type_de_mesure == "segment"
        else [(0.1, 0.4), (0.4, 0.4), (0.4, 0.6), (0.1, 0.6)]
    )
    mesure = _mesurer(
        client,
        entetes,
        document_id,
        revision_id,
        type_de_mesure=type_de_mesure,
        points=points,
        libelle=libelle,
    )
    assert mesure.status_code == 201, mesure.text
    proposal_id = mesure.json()["proposal_id"]

    if decision is not None:
        corps: dict[str, object] = {"decision": decision, "reason": "Relevé sur place."}
        if corrigee is not None:
            # `before_value` ET `after_value` : une correction qui ne porterait
            # que la valeur d'arrivée ne dirait pas ce qu'elle a remplacé, et
            # la route la refuse — à juste titre.
            corps["before_value"] = {
                "valeur": mesure.json()["valeur"],
                "unite": mesure.json()["unite"],
            }
            corps["after_value"] = {"valeur": corrigee, "unite": mesure.json()["unite"]}
        tranchee = client.post(
            f"/api/v1/extraction-proposals/{proposal_id}/decisions", headers=entetes, json=corps
        )
        assert tranchee.status_code == 201, tranchee.text

    relue = _relire(client, entetes, document_id, revision_id)
    assert relue.status_code == 200, relue.text
    return next(m for m in relue.json()["mesures"] if m["proposal_id"] == proposal_id)


def _reprendre(
    client: TestClient,
    entetes: dict[str, str],
    boq_id: str,
    proposal_id: str,
    *,
    position: str = "90.10",
    designation: str = "Façade sud, mesurée sur le plan",
    unite_cible: str | None = None,
    quantite_retenue: str | None = None,
):
    corps: dict[str, object] = {
        "proposal_id": proposal_id,
        "position": position,
        "designation": designation,
    }
    if unite_cible is not None:
        corps["unite_cible"] = unite_cible
    if quantite_retenue is not None:
        corps["quantite_retenue"] = quantite_retenue
    return client.post(
        f"/api/v1/boqs/{boq_id}/items:depuis-une-mesure", headers=entetes, json=corps
    )


@pytest.fixture()
def chantier(seeded_client: TestClient) -> dict[str, str]:
    """Le chantier chiffré du jeu de démonstration, et son bordereau."""
    entetes = login(seeded_client, ADMIN)
    estimation = seeded_client.get("/api/v1/estimates", headers=entetes).json()[0]
    return {
        "entetes": entetes,  # type: ignore[dict-item]
        "estimate_id": estimation["id"],
        "boq_id": estimation["boq_id"],
        "project_id": estimation["project_id"],
        "price_book_version_id": estimation["price_book_version_id"],
    }


# ---------------------------------------------------------------------------
# 1. Ce qui se reprend, et avec quoi
# ---------------------------------------------------------------------------


def test_une_mesure_confirmee_devient_une_ligne_de_bordereau(
    seeded_client: TestClient, chantier
) -> None:
    """La quantité vient de la mesure, pas de l'appelant.

    C'est la propriété centrale : la route n'accepte AUCUNE quantité. Une ligne
    qui annoncerait une provenance et porterait un autre nombre serait pire
    qu'une ligne sans provenance.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")
    assert mesure["reprenable"] is True

    reponse = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 201, reponse.text
    ligne = reponse.json()

    assert Decimal(ligne["quantity"]) == Decimal(mesure["valeur_retenue"])
    assert ligne["unit_code"] == mesure["unite"]
    assert ligne["source_proposal_id"] == mesure["proposal_id"]
    # `proposed`, et non `approved` : la décision prise sur la mesure dit ce que
    # le DESSIN porte. Approuver un poste est une autre décision, et elle exige
    # une autre permission.
    assert ligne["status"] == "proposed"


def test_la_ligne_reprise_garde_de_quoi_rouvrir_le_plan(
    seeded_client: TestClient, chantier
) -> None:
    """L'empreinte, et pourquoi elle est plus qu'un doublon du lien.

    Le lien suffit tant que le plan existe. La purge d'une révision publiée
    détruit ses citations et donc ses propositions : la clé passe à `NULL`, et
    sans empreinte le montant du devis perdrait toute justification.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")
    ligne = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()

    empreinte = ligne["source_mesure"]
    assert empreinte["origine"] == "mesure_pdf"
    assert empreinte["citation_id"] == mesure["citation_id"]
    assert empreinte["page"] == mesure["page"]
    assert empreinte["decision"] == "accepted"
    assert empreinte["motif_de_la_decision"] == "Relevé sur place."
    # La mesure brute ET la valeur retenue : après une correction, les deux
    # diffèrent, et le dossier n'est auditable que s'il porte les deux.
    assert empreinte["valeur_mesuree"] == mesure["valeur"]
    assert empreinte["valeur_retenue"] == mesure["valeur_retenue"]
    # L'incertitude est conservée, et elle n'est PAS devenue une quantité :
    # majorer un métré de son incertitude serait une marge déguisée.
    assert empreinte["incertitude"] == mesure["incertitude"]
    assert Decimal(ligne["quantity"]) == Decimal(empreinte["valeur_retenue"])


def test_une_mesure_corrigee_reprend_la_valeur_de_la_personne(
    seeded_client: TestClient, chantier
) -> None:
    """Et pas celle que la machine avait proposée.

    Si quelqu'un a relevé 3 800,50 là où le calcul annonçait 4 180,68, c'est
    3 800,50 qui part au devis. Reprendre la proposition reviendrait à reprendre
    exactement le nombre qui vient d'être écarté.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="corrected",
        corrigee="3800.50",
    )
    ligne = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()

    assert Decimal(ligne["quantity"]) == Decimal("3800.50")
    assert Decimal(ligne["quantity"]) != Decimal(mesure["valeur"])
    assert ligne["source_mesure"]["decision"] == "corrected"


# ---------------------------------------------------------------------------
# 2. L'unité : conservée, ou convertie exactement
# ---------------------------------------------------------------------------


def test_sans_unite_cible_l_unite_de_la_mesure_est_conservee(
    seeded_client: TestClient, chantier
) -> None:
    """Ne rien convertir est le seul défaut qui ne devine rien."""
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")
    ligne = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()

    assert ligne["unit_code"] == "mm"
    assert Decimal(ligne["quantity"]) == Decimal(mesure["valeur_retenue"])


def test_une_conversion_demandee_est_exacte_et_tracee(seeded_client: TestClient, chantier) -> None:
    """Millimètres vers mètres : un facteur mille, et rien d'approché.

    L'empreinte garde les deux unités — celle de la mesure et celle de la
    reprise — pour qu'un relecteur puisse refaire la division.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")
    ligne = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
    ).json()

    assert ligne["unit_code"] == "m"
    assert Decimal(ligne["quantity"]) == Decimal(mesure["valeur_retenue"]) / Decimal(1000)
    assert ligne["source_mesure"]["unite_mesuree"] == "mm"
    assert ligne["source_mesure"]["unite_reprise"] == "m"


def test_reprendre_une_surface_en_metres_lineaires_est_refuse(
    seeded_client: TestClient, chantier
) -> None:
    """Un facteur silencieux produirait un métré plausible et faux.

    C'est le pire des résultats : une erreur de dimension ne se voit pas sur le
    nombre, elle se voit sur le total, des semaines plus tard.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="accepted",
        type_de_mesure="surface",
        libelle="Dalle du séjour",
    )
    assert mesure["unite"] == "m2"

    reponse = _reprendre(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], unite_cible="m"
    )
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == "unite_cible_incompatible"
    # Les deux unités sont NOMMÉES dans la phrase : « incompatible » seul
    # laisserait chercher laquelle des deux est en cause.
    message = reponse.json()["detail"]["message"]
    assert "m²" in message
    assert "en m." in message


# ---------------------------------------------------------------------------
# 3. Ce qui ne se reprend pas — et le motif exact du refus
# ---------------------------------------------------------------------------


def test_une_mesure_sans_decision_est_refusee_et_dit_quoi_faire(
    seeded_client: TestClient, chantier
) -> None:
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision=None)
    assert mesure["reprenable"] is False

    reponse = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert detail["code"] == "mesure_sans_decision_retenue"
    assert "confirmez-la ou corrigez-la" in detail["message"]


def test_une_mesure_rejetee_est_refusee_et_le_dit_autrement(
    seeded_client: TestClient, chantier
) -> None:
    """Le même code, une autre phrase — parce que l'action n'est pas la même.

    Une mesure non tranchée attend une décision ; une mesure rejetée a déjà été
    écartée et ne reviendra pas. Confondre les deux ferait chercher un bouton
    qui n'existe pas.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="rejected")
    assert mesure["reprenable"] is False

    reponse = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert detail["code"] == "mesure_sans_decision_retenue"
    assert "rejetée" in detail["message"]
    assert "n'alimentera aucun bordereau" in detail["message"]


def test_aucune_ligne_n_est_creee_quand_la_reprise_est_refusee(
    seeded_client: TestClient, chantier
) -> None:
    """Le refus ne laisse rien derrière lui : vérifié en base, pas à l'écran."""
    entetes = chantier["entetes"]
    avant = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="rejected")

    assert (
        _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).status_code
        == 422
    )

    apres = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    assert len(apres) == len(avant)


def test_la_meme_mesure_ne_se_reprend_pas_deux_fois_dans_un_bordereau(
    seeded_client: TestClient, chantier
) -> None:
    """Le double comptage est l'erreur la plus coûteuse d'un métré.

    Elle ne se voit pas sur la ligne — chacune est juste — mais sur le total,
    et personne ne la retrouve en relisant les quantités une par une.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    premiere = _reprendre(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], position="90.10"
    )
    assert premiere.status_code == 201, premiere.text

    seconde = _reprendre(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], position="90.20"
    )
    assert seconde.status_code == 409, seconde.text
    assert seconde.json()["detail"]["code"] == "mesure_deja_reprise"


def test_une_mesure_d_une_autre_organisation_rend_404(seeded_client: TestClient, chantier) -> None:
    """404 et non 403 : répondre 403 confirmerait que l'identifiant existe."""
    entetes = chantier["entetes"]
    reponse = _reprendre(
        seeded_client, entetes, chantier["boq_id"], "00000000-0000-0000-0000-000000000000"
    )
    assert reponse.status_code == 404, reponse.text


# ---------------------------------------------------------------------------
# 4. La quantité reprise arrive dans le devis, puis dans le PDF
# ---------------------------------------------------------------------------


def test_la_quantite_reprise_est_chiffree_dans_le_devis(
    seeded_client: TestClient, chantier
) -> None:
    """Le calcul la voit : c'est ce qui fait de la reprise autre chose qu'un champ.

    La ligne est tarifée par un prix de bibliothèque au mètre, et le montant
    attendu se recalcule de tête : quantité × prix unitaire.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    prix = seeded_client.post(
        f"/api/v1/price-books/versions/{chantier['price_book_version_id']}/items",
        headers=entetes,
        json={
            "code": "FAC-ML",
            "label": "Façade au mètre linéaire",
            "unit_code": "m",
            "unit_price": "25.00",
            "resource_kind": "subcontract",
        },
    )
    assert prix.status_code == 201, prix.text

    ligne = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
    ).json()
    rattache = seeded_client.patch(
        f"/api/v1/boq-items/{ligne['id']}",
        headers=entetes,
        json={"price_item_id": prix.json()["id"]},
    )
    assert rattache.status_code == 200, rattache.text

    version = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions", headers=entetes
    ).json()[0]
    calcul = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions/{version['id']}/computation",
        headers=entetes,
    )
    assert calcul.status_code == 200, calcul.text

    postes = calcul.json()["result"]["lines"]
    chiffree = next(poste for poste in postes if poste["line_id"] == ligne["id"])
    assert Decimal(str(chiffree["quantity"])) == Decimal(ligne["quantity"])
    assert chiffree["unit"] == "m"
    assert chiffree["missing_price"] is False

    # Le déboursé est arrondi par la politique d'arrondi de l'organisation ;
    # la tolérance d'un centime porte sur CET arrondi, et sur rien d'autre.
    attendu = Decimal(ligne["quantity"]) * Decimal("25.00")
    assert abs(Decimal(chiffree["price"]["direct_cost"]) - attendu) <= Decimal("0.01")


def test_la_ligne_reprise_figure_dans_le_pdf_remis_au_client(
    seeded_client: TestClient, chantier
) -> None:
    """Le bout du chemin : le dessin pointé devient une ligne imprimée.

    Le PDF ne porte pas la provenance — un client n'a pas à lire nos décisions
    internes — mais il porte la ligne, sa désignation et son unité. La
    provenance, elle, reste en base et dans le journal d'audit.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    prix = seeded_client.post(
        f"/api/v1/price-books/versions/{chantier['price_book_version_id']}/items",
        headers=entetes,
        json={
            "code": "FAC-ML-PDF",
            "label": "Façade au mètre linéaire",
            "unit_code": "m",
            "unit_price": "25.00",
            "resource_kind": "subcontract",
        },
    )
    assert prix.status_code == 201, prix.text

    designation = "Facade sud reprise du plan"
    ligne = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        designation=designation,
        unite_cible="m",
    ).json()
    assert (
        seeded_client.patch(
            f"/api/v1/boq-items/{ligne['id']}",
            headers=entetes,
            json={"price_item_id": prix.json()["id"]},
        ).status_code
        == 200
    )

    estimation = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}", headers=entetes
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions", headers=entetes
    ).json()[0]

    emission.prix_manquant(seeded_client, entetes, estimation)
    fiche = emission.fiche(seeded_client, entetes)
    emission.rattacher(seeded_client, entetes, estimation["project_id"], fiche["id"])
    emission.geler(seeded_client, entetes, estimation, version)
    devis = emission.emettre(seeded_client, entetes, estimation, version)
    assert devis.status_code == 201, devis.text

    fichier = seeded_client.get(
        f"/api/v1/issued-quotes/{devis.json()['id']}/document.pdf", headers=entetes
    )
    assert fichier.status_code == 200, fichier.text
    texte = moteur_pdf.extraire_le_texte(fichier.content)
    assert designation in texte


# ---------------------------------------------------------------------------
# 5. Si le lien tombe, la ligne reste — ce qui est déclaré, et ce qui est atteint
# ---------------------------------------------------------------------------
#
# **Ce qui est vérifié ici est la DÉCLARATION, et c'est tout ce qui peut l'être.**
#
# La propriété visée est qu'une suppression de proposition ne détruise pas la
# ligne de bordereau qui l'a reprise. Elle n'est **atteignable par aucun
# chemin** aujourd'hui, et deux verrous s'y opposent, tous deux voulus :
#
#  1. aucune route ne détruit une proposition seule — la seule purge livrée est
#     celle d'une organisation entière, qui emporte aussi ses bordereaux ;
#  2. une mesure reprenable porte nécessairement une **décision humaine**, et
#     `validation_decisions` est en ajout seul : sur PostgreSQL, un déclencheur
#     refuse toute suppression hors purge autorisée. Mesuré en écrivant ce
#     fichier : « validation decision is append-only ».
#
# Tenter la suppression en test exigerait donc de fabriquer une autorisation de
# purge, c'est-à-dire d'éprouver la purge et non la reprise. Ce qui est vérifié
# est donc l'action DÉCLARÉE — un `CASCADE` posé par distraction ferait
# disparaître un montant de devis le jour où une purge par document existera.
#
# La confrontation de cette déclaration au catalogue réel de PostgreSQL est
# faite ailleurs, et pour toutes les clés à la fois :
# `test_referential_action_drift.py`, classe `TestTheCatalogueAgreesWithTheModels`.


def test_la_cle_de_provenance_est_en_set_null_et_jamais_en_cascade() -> None:
    """`CASCADE` ici ferait disparaître un montant de devis avec un dessin."""
    from sqlalchemy import ForeignKeyConstraint

    from metreo_api.models import BoqItem

    actions = {
        contrainte.name: contrainte.ondelete
        for contrainte in BoqItem.__table__.constraints
        if isinstance(contrainte, ForeignKeyConstraint) and contrainte.name
    }

    assert actions["fk_boq_items_source_proposal_tenant"] == "SET NULL (source_proposal_id)"
    # La colonne est NOMMÉE : un `SET NULL` nu viderait aussi
    # `organization_id`, qui est NOT NULL, et la suppression échouerait au lieu
    # de dénouer le lien.
    assert "organization_id" not in (actions["fk_boq_items_source_proposal_tenant"] or "")


def test_la_ligne_reprise_porte_son_empreinte_en_plus_du_lien(
    seeded_client: TestClient, chantier
) -> None:
    """Le lien peut tomber ; l'empreinte, non — elle n'est pas une clé.

    C'est pour cela que les deux coexistent. Sans l'empreinte, dénouer le lien
    laisserait une quantité sans justification ; sans le lien, on ne pourrait
    plus rouvrir le plan à la bonne page tant qu'il existe.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")
    ligne = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()

    assert ligne["source_proposal_id"] == mesure["proposal_id"]
    # L'empreinte redit, sans la clé, tout ce qu'il faut pour justifier le
    # nombre : d'où il vient, ce qu'il valait, qui l'a tranché et pourquoi.
    empreinte = ligne["source_mesure"]
    assert empreinte["proposal_id"] == mesure["proposal_id"]
    assert empreinte["valeur_retenue"] == mesure["valeur_retenue"]
    assert empreinte["decision"] == "accepted"
    assert empreinte["motif_de_la_decision"]
    assert empreinte["motif_de_la_calibration"]
    assert empreinte["reprise_le"]


# ---------------------------------------------------------------------------
# 6. L'aperçu : voir le nombre avant de l'écrire
# ---------------------------------------------------------------------------
#
# **Pourquoi une route pour une conversion qui tient en un facteur mille.**
# Parce que ce facteur serait sinon écrit DEUX fois : une en Python, une en
# TypeScript. Les deux finiraient par diverger — c'est la règle que le dépôt
# applique déjà au facteur d'échelle, aux valeurs lisibles et aux totaux d'un
# devis, et chaque fois pour la même raison.
#
# Et le patron est celui du dépôt depuis l'import de prix : prévisualiser, puis
# confirmer. Rien n'est écrit tant que la personne n'a pas vu.


def _apercu(
    client: TestClient,
    entetes: dict[str, str],
    boq_id: str,
    proposal_id: str,
    *,
    unite_cible: str | None = None,
    quantite_retenue: str | None = None,
):
    corps: dict[str, object] = {"proposal_id": proposal_id}
    if unite_cible is not None:
        corps["unite_cible"] = unite_cible
    if quantite_retenue is not None:
        corps["quantite_retenue"] = quantite_retenue
    return client.post(
        f"/api/v1/boqs/{boq_id}/reprises-de-mesure/apercu", headers=entetes, json=corps
    )


def test_l_apercu_rend_la_quantite_sans_rien_ecrire(seeded_client: TestClient, chantier) -> None:
    """La propriété centrale : le nombre est rendu, et le bordereau ne bouge pas."""
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    avant = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    reponse = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 200, reponse.text
    apercu = reponse.json()

    assert Decimal(apercu["quantite"]) == Decimal(mesure["valeur_retenue"])
    assert apercu["unite"] == mesure["unite"]
    # Rien n'a été écrit : c'est toute la différence avec la route voisine.
    apres = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    assert len(apres) == len(avant)


def test_l_apercu_rend_la_quantite_convertie_et_son_ecriture_lisible(
    seeded_client: TestClient, chantier
) -> None:
    """Le nombre exact ET sa lecture, écrite comme le bordereau l'écrira.

    Une ligne de bordereau vit dans le monde du DOCUMENT : son nombre sera
    multiplié par un prix unitaire et imprimé sur un devis. L'aperçu l'écrit
    donc comme le document l'écrira — deux décimales au moins, celles de la
    valeur au-delà — et non avec les décimales de la MESURE, tirées de son
    incertitude. Annoncer « 6,0200 m » puis écrire « 6,02 m » était annoncer
    autre chose que ce qu'on fait.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    apercu = _apercu(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], unite_cible="m"
    ).json()

    assert apercu["unite"] == "m"
    assert Decimal(apercu["quantite"]) == Decimal(mesure["valeur_retenue"]) / Decimal(1000)
    assert apercu["quantite_lisible"].endswith(" m")
    assert apercu["quantite_lisible"] == lisible.quantite_de_document_lisible(
        Decimal(apercu["quantite"]), "m"
    )


def test_l_apercu_annonce_EXACTEMENT_le_nombre_que_la_ligne_portera(
    seeded_client: TestClient, chantier
) -> None:
    """L'aperçu et l'écriture, sur la même mesure, à la dernière décimale.

    **Le défaut que ce test ferme.** `convert` travaille à vingt-huit chiffres
    significatifs ; la colonne `quantity` en garde dix et quantise à
    l'écriture. Une conversion de millimètres en mètres produisait donc treize
    décimales, dont l'aperçu annonçait les treize et dont la ligne n'écrivait
    que dix. L'écart valait 10⁻¹¹ m : invisible, et c'est précisément pourquoi
    il fallait un test. Un aperçu qui annonce autre chose que ce qu'il écrit
    n'est plus un aperçu.

    La cote est choisie pour que la division par mille ne tombe pas juste.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="corrected",
        corrigee="4180.6822810844",
    )

    annonce = _apercu(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], unite_cible="m"
    ).json()
    ecrite = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
        position="70.10",
        designation="La cote qui ne tombe pas juste",
    ).json()

    assert Decimal(ecrite["quantity"]) == Decimal(annonce["quantite"]), (
        f"annoncé {annonce['quantite']}, écrit {ecrite['quantity']}"
    )
    # Et la ligne relue par l'API dit la même chose : l'égalité ne tient pas
    # qu'à la réponse de la route d'écriture.
    lignes = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    relue = next(ligne for ligne in lignes if ligne["id"] == ecrite["id"])
    assert Decimal(relue["quantity"]) == Decimal(annonce["quantite"])
    assert relue["quantity_lisible"] == annonce["quantite_lisible"]


def test_une_quantite_a_dix_decimales_s_ecrit_pareil_de_l_apercu_au_pdf(
    seeded_client: TestClient, chantier
) -> None:
    """Le cas trouvé sur un plan réel, figé : une écriture, quatre surfaces.

    **Le défaut.** Une surface de balcon mesurée sur un plan d'exécution, puis
    reprise, valait 6,3787950927 m². L'aperçu et le bordereau l'écrivaient
    « 6,378795 », plafonnés à six décimales ; l'étude et le PDF du devis
    l'écrivaient « 6,3787950927 ». La valeur était la même partout — le moteur
    multiplie la quantité stockée —, mais l'aperçu annonçait un nombre que la
    ligne ne portait pas, et le client lisait autre chose que l'écran.

    **Ce que ce test n'exige pas** : que la quantité soit arrondie. Arrondir
    une quantité reprise est une décision de chiffrage, ouverte dans
    `docs/ARRONDI_DES_DOCUMENTS.md`. Il exige seulement que le nombre, quel
    qu'il soit, s'écrive de la même façon partout où il passe.

    La cote est corrigée en millimètres pour que la conversion en mètres porte
    exactement dix décimales significatives.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="corrected",
        corrigee="6378.7950927",
    )
    ecriture = "6,3787950927"

    annonce = _apercu(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], unite_cible="m"
    ).json()
    assert annonce["quantite_lisible"] == f"{ecriture} m"

    prix = seeded_client.post(
        f"/api/v1/price-books/versions/{chantier['price_book_version_id']}/items",
        headers=entetes,
        json={
            "code": "DIX-DEC",
            "label": "Poste au mètre",
            "unit_code": "m",
            "unit_price": "10.00",
            "resource_kind": "subcontract",
        },
    )
    assert prix.status_code == 201, prix.text
    ligne = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
        position="70.20",
        designation="Dix décimales, une seule écriture",
    ).json()
    assert ligne["quantity_lisible"] == f"{ecriture} m"
    assert (
        seeded_client.patch(
            f"/api/v1/boq-items/{ligne['id']}",
            headers=entetes,
            json={"price_item_id": prix.json()["id"]},
        ).status_code
        == 200
    )

    estimation = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}", headers=entetes
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions", headers=entetes
    ).json()[0]
    calcul = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions/{version['id']}/computation",
        headers=entetes,
    ).json()
    poste = next(p for p in calcul["result"]["lines"] if p["line_id"] == ligne["id"])
    # L'étude écrit le texte du moteur, transcrit : c'est la même écriture.
    assert lisible.nombre_francais_tel_quel(str(poste["quantity"])) == ecriture

    emission.prix_manquant(seeded_client, entetes, estimation)
    fiche = emission.fiche(seeded_client, entetes)
    emission.rattacher(seeded_client, entetes, estimation["project_id"], fiche["id"])
    emission.geler(seeded_client, entetes, estimation, version)
    devis = emission.emettre(seeded_client, entetes, estimation, version)
    assert devis.status_code == 201, devis.text
    fichier = seeded_client.get(
        f"/api/v1/issued-quotes/{devis.json()['id']}/document.pdf", headers=entetes
    )
    assert fichier.status_code == 200, fichier.text
    assert ecriture in moteur_pdf.extraire_le_texte(fichier.content)


# ---------------------------------------------------------------------------
# 7. La quantité RETENUE : proposée à la finesse de la mesure, choisie dans le ±
# ---------------------------------------------------------------------------
#
# Trouvé sur un plan réel : une surface confirmée valait 6,3787950927 m²
# ± 0,041 m², et c'est ce nombre à dix décimales qui est parti au devis. Dix
# décimales affirment une précision que la mesure n'a pas. Le serveur propose
# donc la quantité à la finesse du ±, et la personne peut en retenir une autre,
# dans le ± : 6,38 est une écriture de la même mesure, 6,5 un autre nombre.


def _surface_confirmee(seeded_client: TestClient, chantier) -> dict:
    """Une surface mesurée et CONFIRMÉE : elle a un ±, donc une proposition."""
    mesure = _mesure_tranchee(
        seeded_client,
        chantier["entetes"],
        chantier["project_id"],
        decision="accepted",
        type_de_mesure="surface",
        libelle="Dalle du balcon",
    )
    assert mesure["incertitude"] not in (None, "")
    assert Decimal(mesure["incertitude"]) > 0
    return mesure


def test_l_apercu_propose_la_quantite_a_la_finesse_de_la_mesure(
    seeded_client: TestClient, chantier
) -> None:
    """Brute, ±, proposée : les trois sont rendus, et la proposée s'écrit par défaut."""
    entetes = chantier["entetes"]
    mesure = _surface_confirmee(seeded_client, chantier)

    apercu = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()
    brute = Decimal(apercu["quantite_brute"])
    incertitude = Decimal(apercu["incertitude"])
    assert brute == Decimal(mesure["valeur_retenue"])
    assert incertitude == Decimal(mesure["incertitude"])
    assert apercu["incertitude_lisible"].startswith("±")

    # La proposée est la brute arrondie à la décimale que le ± autorise — la
    # règle déjà appliquée à l'écriture du ± lui-même — et rien de plus fin.
    decimales = lisible.decimales_utiles(incertitude)
    assert Decimal(apercu["quantite_proposee"]) == brute.quantize(
        Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP
    )
    assert abs(Decimal(apercu["quantite_proposee"]) - brute) <= incertitude
    assert apercu["quantite"] == apercu["quantite_proposee"]
    assert apercu["quantite_retenue_par"] == "proposition"
    assert apercu["quantite_lisible"] == apercu["quantite_proposee_lisible"]

    # Renvoyer la proposition telle quelle, c'est l'accepter : le formulaire la
    # pré-remplit et la renvoie, et elle ne repasse pas le contrôle du ± qui
    # l'a produite.
    renvoyee = _apercu(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        quantite_retenue=apercu["quantite_proposee"],
    ).json()
    assert renvoyee["quantite"] == apercu["quantite_proposee"]
    assert renvoyee["quantite_retenue_par"] == "proposition"

    # Sans quantité retenue, la ligne porte la proposée — le nombre montré.
    ligne = _reprendre(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()
    assert Decimal(ligne["quantity"]) == Decimal(apercu["quantite_proposee"])
    assert Decimal(ligne["source_mesure"]["quantite_brute"]) == brute
    assert ligne["source_mesure"]["quantite_retenue_par"] == "proposition"


def test_une_quantite_retenue_dans_le_plus_ou_moins_s_ecrit_jusqu_au_pdf(
    seeded_client: TestClient, chantier
) -> None:
    """6,38 pour 6,3787950927 ± 0,041 : la même mesure, écrite par une personne.

    Et c'est CE nombre, identique, que portent l'aperçu, le bordereau, le
    calcul de l'étude et le PDF remis au client.
    """
    entetes = chantier["entetes"]
    mesure = _surface_confirmee(seeded_client, chantier)
    apercu = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()
    brute = Decimal(apercu["quantite_brute"])
    incertitude = Decimal(apercu["incertitude"])
    # Un nombre à deux décimales qui tient dans le ± : la personne le choisit.
    retenue = (brute + incertitude / 2).quantize(Decimal("0.01"))
    if abs(retenue - brute) > incertitude:
        retenue = brute.quantize(Decimal("0.01"))
    assert abs(retenue - brute) <= incertitude, "le test doit choisir dans le ±"
    assert retenue != brute

    annonce = _apercu(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        quantite_retenue=str(retenue),
    ).json()
    assert Decimal(annonce["quantite"]) == retenue
    assert annonce["quantite_retenue_par"] == "personne"
    assert "quantité retenue" in annonce["provenance_lisible"]
    assert Decimal(annonce["quantite_brute"]) == brute, "la brute ne bouge pas"

    prix = seeded_client.post(
        f"/api/v1/price-books/versions/{chantier['price_book_version_id']}/items",
        headers=entetes,
        json={
            "code": "DALLE-M2",
            "label": "Dalle au mètre carré",
            "unit_code": "m2",
            "unit_price": "10.00",
            "resource_kind": "subcontract",
        },
    )
    assert prix.status_code == 201, prix.text
    ligne = _reprendre(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        position="70.30",
        designation="Dalle de balcon, quantité retenue",
        quantite_retenue=str(retenue),
    ).json()
    assert Decimal(ligne["quantity"]) == retenue
    assert ligne["quantity_lisible"] == annonce["quantite_lisible"]
    assert ligne["unit_lisible"] == "m²"
    assert Decimal(ligne["source_mesure"]["quantite_retenue"]) == retenue
    assert Decimal(ligne["source_mesure"]["quantite_brute"]) == brute
    assert ligne["source_mesure"]["quantite_retenue_par"] == "personne"
    assert (
        seeded_client.patch(
            f"/api/v1/boq-items/{ligne['id']}",
            headers=entetes,
            json={"price_item_id": prix.json()["id"]},
        ).status_code
        == 200
    )

    estimation = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}", headers=entetes
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions", headers=entetes
    ).json()[0]
    calcul = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate_id']}/versions/{version['id']}/computation",
        headers=entetes,
    ).json()
    poste = next(p for p in calcul["result"]["lines"] if p["line_id"] == ligne["id"])
    assert Decimal(str(poste["quantity"])) == retenue
    assert poste["unit_lisible"] == "m²"

    emission.prix_manquant(seeded_client, entetes, estimation)
    fiche = emission.fiche(seeded_client, entetes)
    emission.rattacher(seeded_client, entetes, estimation["project_id"], fiche["id"])
    emission.geler(seeded_client, entetes, estimation, version)
    devis = emission.emettre(seeded_client, entetes, estimation, version)
    assert devis.status_code == 201, devis.text
    fichier = seeded_client.get(
        f"/api/v1/issued-quotes/{devis.json()['id']}/document.pdf", headers=entetes
    )
    assert fichier.status_code == 200, fichier.text
    texte = moteur_pdf.extraire_le_texte(fichier.content)
    ecriture = lisible.nombre_francais_tel_quel(str(retenue)).replace("\u202f", "\u00a0")
    assert ecriture in texte
    assert "m²" in texte, "le devis imprime le symbole, pas le code"
    # La brute à dix décimales, elle, n'est imprimée nulle part.
    brute_stockee = ligne["source_mesure"]["quantite_brute"]
    assert "." in brute_stockee and len(brute_stockee.split(".")[1]) == 10
    assert lisible.nombre_francais_tel_quel(brute_stockee) not in texte.replace("\u00a0", "\u202f")

    # La décision est au journal : brute, proposée, retenue, et par qui.
    evenements = seeded_client.get(
        f"/api/v1/audit/events?object_id={ligne['id']}", headers=entetes
    ).json()["items"]
    reprise = next(e for e in evenements if e["action"] == "boq_item.created_from_measurement")
    assert Decimal(reprise["payload"]["quantity"]) == retenue
    assert Decimal(reprise["payload"]["quantity_raw"]) == brute
    assert reprise["payload"]["quantity_retained_by"] == "personne"
    # Le ± qui a justifié l'acceptation est dans l'événement : il se relit seul.
    assert Decimal(reprise["payload"]["quantity_uncertainty"]) == incertitude


def test_une_quantite_retenue_hors_du_plus_ou_moins_est_refusee(
    seeded_client: TestClient, chantier
) -> None:
    """Au-delà du ±, ce n'est plus une écriture de la mesure : c'est un autre nombre.

    Le bon geste est alors de corriger la mesure, avec un motif, et la route
    le dit. Rien n'est écrit, ni par l'aperçu, ni par la reprise.
    """
    entetes = chantier["entetes"]
    mesure = _surface_confirmee(seeded_client, chantier)
    apercu = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()
    trop = Decimal(apercu["quantite_brute"]) + 3 * Decimal(apercu["incertitude"]) + 1

    avant = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    for route in (_apercu, _reprendre):
        refus = route(
            seeded_client,
            entetes,
            chantier["boq_id"],
            mesure["proposal_id"],
            quantite_retenue=str(trop),
        )
        assert refus.status_code == 422, refus.text
        assert refus.json()["detail"]["code"] == "quantite_retenue_hors_tolerance"
        assert "corrigez la mesure" in refus.json()["detail"]["message"]
    apres = seeded_client.get(f"/api/v1/boqs/{chantier['boq_id']}/items", headers=entetes).json()
    assert len(apres) == len(avant)

    # Zéro n'est pas une quantité d'ouvrage non plus.
    nul = _apercu(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], quantite_retenue="0"
    )
    assert nul.status_code == 422
    assert nul.json()["detail"]["code"] == "quantite_retenue_invalide"


def test_une_mesure_corrigee_n_a_pas_de_plus_ou_moins_et_s_ecrit_telle_quelle(
    seeded_client: TestClient, chantier
) -> None:
    """La personne a déjà choisi le nombre : il n'y a ni proposition arrondie,
    ni marge pour en retenir un autre sans motif."""
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="corrected",
        corrigee="6020",
    )
    apercu = _apercu(
        seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"], unite_cible="m"
    ).json()
    assert apercu["incertitude"] is None
    assert apercu["incertitude_lisible"] is None
    assert apercu["quantite_proposee"] == apercu["quantite_brute"]
    assert Decimal(apercu["quantite"]) == Decimal("6.02")

    # La valeur exacte est acceptée ; un autre nombre, même proche, est refusé.
    meme = _apercu(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
        quantite_retenue="6.020",
    )
    assert meme.status_code == 200, meme.text
    assert meme.json()["quantite_retenue_par"] == "proposition"
    autre = _apercu(
        seeded_client,
        entetes,
        chantier["boq_id"],
        mesure["proposal_id"],
        unite_cible="m",
        quantite_retenue="6.0",
    )
    assert autre.status_code == 422
    assert autre.json()["detail"]["code"] == "quantite_retenue_hors_tolerance"


def test_l_apercu_dit_d_ou_vient_le_nombre(seeded_client: TestClient, chantier) -> None:
    """Sans la provenance, « 3,80 m » est un nombre sans auteur."""
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(
        seeded_client,
        entetes,
        chantier["project_id"],
        decision="corrected",
        corrigee="3800.50",
    )

    apercu = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"]).json()

    assert f"page {mesure['page']}" in apercu["provenance_lisible"]
    assert "corrigée" in apercu["provenance_lisible"]
    # Et c'est bien la valeur de la PERSONNE, pas la proposition de la machine.
    assert Decimal(apercu["quantite"]) == Decimal("3800.50")


def test_l_apercu_refuse_une_mesure_rejetee_avant_toute_saisie(
    seeded_client: TestClient, chantier
) -> None:
    """Le refus arrive AU MOMENT où on désigne la mesure.

    C'est l'intérêt de l'aperçu au-delà du nombre : une mesure rejetée se dit
    rejetée avant qu'on ait tapé une position et une désignation, pas après.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="rejected")

    reponse = _apercu(seeded_client, entetes, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == "mesure_sans_decision_retenue"
    assert "rejetée" in reponse.json()["detail"]["message"]


def test_l_apercu_est_offert_a_qui_lit_un_bordereau_sans_pouvoir_y_ecrire(
    seeded_client: TestClient, chantier
) -> None:
    """`BOQ_READ` suffit : savoir ce qu'une reprise écrirait n'écrit rien.

    Un lecteur ne peut pas reprendre la mesure — la route voisine le refuse —
    mais il peut voir ce que la reprise mettrait dans le bordereau.
    """
    entetes = chantier["entetes"]
    mesure = _mesure_tranchee(seeded_client, entetes, chantier["project_id"], decision="accepted")

    lecteur = login(seeded_client, "lecteur@dubois.demo")
    reponse = _apercu(seeded_client, lecteur, chantier["boq_id"], mesure["proposal_id"])
    assert reponse.status_code == 200, reponse.text

    refus = _reprendre(seeded_client, lecteur, chantier["boq_id"], mesure["proposal_id"])
    assert refus.status_code == 403, refus.text
