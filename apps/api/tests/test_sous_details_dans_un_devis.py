"""Un sous-détail employé dans un devis : ce qui bouge, ce qui ne bouge plus.

Un sous-détail est une donnée VIVANTE de la bibliothèque : on corrige un
rendement, on met un prix de ressource à jour. Un devis gelé, lui, ne bouge
jamais. Entre les deux, une frontière que ce fichier éprouve dans les deux
sens :

* tant que la version de bibliothèque est brouillon, corriger un sous-détail
  change le devis BROUILLON qui s'en sert — c'est le but, sinon la correction
  n'aurait servi à rien ;
* une fois la version publiée, plus rien ne bouge ;
* une fois le devis GELÉ, il porte ses propres composants et ses propres
  montants, et aucune modification ultérieure ne les atteint.

La confidentialité est l'autre moitié : la ventilation d'un sous-détail dit ce
que l'entreprise paie ses ressources. Elle ne doit sortir ni par le PDF client,
ni par la page publique, ni par la route de prévisualisation ajoutée avec le
constructeur.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import login

# Des montants ronds, pour que l'attendu se recalcule de tête :
# 1 m3 ÷ 10 m3/h × 60,00 €/h × 1 = 6,00 de déboursé sec par m3.
RENDEMENT: dict[str, Any] = {
    "component_type": "output_rate",
    "label": "Équipe de pose",
    "resource_kind": "labor",
    "output_rate": "10",
    "hourly_rate": "60.00",
    "crew_size": "1",
}


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def chantier(seeded_client: TestClient, admin: dict[str, str]) -> dict[str, Any]:
    """Un chantier entier dont le SEUL poste est chiffré par un sous-détail.

    Les attentes portent sur le DÉBOURSÉ SEC (`total_direct_cost`), pas sur le
    prix de vente : c'est ce que le sous-détail décide, et cela se recalcule de
    tête. La chaîne de marges de l'organisation a ses propres tests ; la mêler
    ici rendrait chaque attendu invérifiable sans relire trois fichiers.
    """
    livre = seeded_client.post(
        "/api/v1/price-books", headers=admin, json={"name": "Sous-détails au devis"}
    )
    version_prix = seeded_client.post(
        f"/api/v1/price-books/{livre.json()['id']}/versions", headers=admin, json={"label": "v1"}
    ).json()

    compose = seeded_client.post(
        f"/api/v1/price-books/versions/{version_prix['id']}/composites",
        headers=admin,
        json={
            "code": "SD-POSE",
            "label": "Pose",
            "unit_code": "m3",
            "components": [RENDEMENT],
        },
    )
    assert compose.status_code == 201, compose.text

    projet = seeded_client.post(
        "/api/v1/projects",
        headers=admin,
        json={"reference": "SD-DEVIS", "name": "Chantier sous-détail"},
    ).json()
    boq = seeded_client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=admin, json={"name": "Bordereau"}
    ).json()
    poste = seeded_client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=admin,
        json={
            "position": "1",
            "designation": "Pose",
            "unit_code": "m3",
            "quantity": "100",
            "kind": "item",
            "composite_price_id": compose.json()["id"],
        },
    )
    assert poste.status_code == 201, poste.text

    estimation = seeded_client.post(
        "/api/v1/estimates",
        headers=admin,
        json={
            "project_id": projet["id"],
            "boq_id": boq["id"],
            "price_book_version_id": version_prix["id"],
            "name": "Étude",
        },
    ).json()
    return {
        "price_book": livre.json()["id"],
        "price_book_version": version_prix["id"],
        "composite": compose.json()["id"],
        "project": projet["id"],
        "boq": boq["id"],
        "item": poste.json()["id"],
        "estimate": estimation["id"],
    }


def _brouillon(client: TestClient, entetes: dict[str, str], estimate_id: str) -> dict[str, Any]:
    versions = client.get(f"/api/v1/estimates/{estimate_id}/versions", headers=entetes).json()
    return dict(versions[0])


def _calcul(client: TestClient, entetes: dict[str, str], estimate_id: str) -> dict[str, Any]:
    """Le calcul de la version courante.

    Les totaux ne sont pas portés par la LISTE des versions : une version
    brouillon n'a pas de total tant qu'on ne l'a pas demandé, et c'est
    cohérent — le calcul dépend du bordereau, qui bouge. On passe donc par
    `/computation`, qui calcule ou relit selon que la version est gelée.
    """
    version = _brouillon(client, entetes, estimate_id)
    reponse = client.get(
        f"/api/v1/estimates/{estimate_id}/versions/{version['id']}/computation", headers=entetes
    )
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def _debourse(calcul: dict[str, Any]) -> Decimal:
    """Le déboursé sec du devis : la somme de ce que les ressources coûtent."""
    return Decimal(str(calcul["result"]["total_direct_cost"]))


# --------------------------------------------------------------------------
# 1. Tant que la bibliothèque est brouillon, corriger PROPAGE
# --------------------------------------------------------------------------


def test_modifier_un_sous_detail_recalcule_le_devis_brouillon(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """Sans propagation, corriger un rendement n'aurait servi à rien.

    100 m3 × 6,00 = 600,00. Le taux horaire passe de 60 à 120 : 100 × 12,00 =
    1 200,00. Les deux chiffres se recalculent de tête, et c'est voulu — un
    attendu qu'on ne peut pas vérifier soi-même ne prouve pas grand-chose.
    """
    avant = _calcul(seeded_client, admin, chantier["estimate"])
    assert _debourse(avant) == Decimal("600.00")

    detail = seeded_client.get(
        f"/api/v1/price-books/composites/{chantier['composite']}", headers=admin
    ).json()
    modifie = seeded_client.put(
        f"/api/v1/price-books/composites/{chantier['composite']}",
        headers=admin,
        json={
            "code": "SD-POSE",
            "label": "Pose",
            "unit_code": "m3",
            "revision": detail["revision"],
            "components": [{**RENDEMENT, "hourly_rate": "120.00"}],
        },
    )
    assert modifie.status_code == 200, modifie.text

    apres = _calcul(seeded_client, admin, chantier["estimate"])
    assert _debourse(apres) == Decimal("1200.00")


# --------------------------------------------------------------------------
# 2. Gelé, le devis ne bouge plus
# --------------------------------------------------------------------------


def test_le_gel_conserve_les_composants_et_les_montants(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """L'instantané porte les COMPOSANTS, pas seulement un total.

    Un total gelé sans sa décomposition serait un chiffre que plus personne ne
    peut expliquer — exactement ce que ce produit refuse.
    """
    version = _brouillon(seeded_client, admin, chantier["estimate"])
    gel = seeded_client.post(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text
    calcul = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/computation",
        headers=admin,
    ).json()
    assert calcul["version"]["status"] == "frozen"
    # Relu depuis l'INSTANTANÉ, pas recalculé depuis les tables courantes.
    assert calcul["from_snapshot"] is True
    assert _debourse(calcul) == Decimal("600.00")

    lignes = calcul["result"]["lines"]
    composants = lignes[0]["price"]["components"]
    assert len(composants) == 1
    assert composants[0]["label"] == "Équipe de pose"
    # La formule aussi : c'est elle qui explique le chiffre.
    assert "60" in composants[0]["formula"]


def test_modifier_le_sous_detail_apres_le_gel_ne_touche_pas_le_devis_gele(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """La garantie centrale du produit, éprouvée sur le chemin des sous-détails.

    Le gel copie ses entrées ; il ne relit jamais la bibliothèque. Un devis
    remis à un client hier ne doit pas changer parce qu'un rendement a été
    corrigé aujourd'hui.
    """
    version = _brouillon(seeded_client, admin, chantier["estimate"])
    seeded_client.post(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    avant = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/computation",
        headers=admin,
    ).json()
    empreinte = avant["version"]["snapshot_sha256"]

    detail = seeded_client.get(
        f"/api/v1/price-books/composites/{chantier['composite']}", headers=admin
    ).json()
    seeded_client.put(
        f"/api/v1/price-books/composites/{chantier['composite']}",
        headers=admin,
        json={
            "code": "SD-POSE",
            "label": "Pose",
            "unit_code": "m3",
            "revision": detail["revision"],
            "components": [{**RENDEMENT, "hourly_rate": "999.00"}],
        },
    )

    relue = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/computation",
        headers=admin,
    ).json()
    assert _debourse(relue) == Decimal("600.00")
    assert relue["version"]["snapshot_sha256"] == empreinte


# --------------------------------------------------------------------------
# 3. La ventilation ne sort pas côté client
# --------------------------------------------------------------------------


def test_l_export_client_ne_porte_aucun_composant_ni_cout_de_ressource(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """Ce que l'entreprise paie ses ressources ne regarde pas son client.

    L'aperçu client est éprouvé sur son TEXTE : le taux horaire, le libellé de
    la ressource et le mot « déboursé » ne doivent y figurer nulle part.
    """
    version = _brouillon(seeded_client, admin, chantier["estimate"])
    seeded_client.post(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/freeze",
        headers=admin,
        json={"confirm": True},
    )

    apercu = seeded_client.get(
        f"/api/v1/estimates/{chantier['estimate']}/versions/{version['id']}/quote.html",
        headers=admin,
    )
    assert apercu.status_code == 200, apercu.text
    texte = apercu.text
    assert "Équipe de pose" not in texte
    assert "60.00" not in texte
    assert "déboursé" not in texte.lower()


def test_un_lecteur_sans_droit_de_cout_n_obtient_aucune_ventilation(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """La route ajoutée avec le constructeur ne rouvre pas une porte fermée.

    `COST_READ` la garde, comme partout ailleurs. Sans ce contrôle, un rôle
    qui ne voit pas les coûts dans le devis les aurait obtenus en
    prévisualisant un sous-détail.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{chantier['price_book_version']}/composites/preview",
        headers=lecteur,
        json={"unit_code": "m3", "components": [RENDEMENT]},
    )
    assert refus.status_code == 403, refus.text
    assert "60.00" not in refus.text


def test_un_lecteur_n_atteint_meme_pas_la_liste_des_sous_details(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """Le rôle « lecteur » n'a pas `pricebook:read`, et la liste le respecte.

    Mesuré plutôt que supposé : j'avais écrit l'inverse — qu'un lecteur voit la
    structure sans les coûts. C'est faux dans ce dépôt, la bibliothèque
    entière lui est fermée. Le test dit donc ce qui EST, et il rougira le jour
    où quelqu'un ouvrira cette porte sans y penser.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    liste = seeded_client.get(
        f"/api/v1/price-books/versions/{chantier['price_book_version']}/composites",
        headers=lecteur,
    )
    assert liste.status_code == 403, liste.text
    assert liste.json()["detail"]["required_permission"] == "pricebook:read"
    assert "60.00" not in liste.text


# --------------------------------------------------------------------------
# 6. Un poste tire son prix d'UNE source, jamais de deux
# --------------------------------------------------------------------------
#
# L'écran offre désormais un choix explicite — aucun prix, prix de bibliothèque,
# sous-détail — et remet l'autre identifiant à `null` dans le même geste. Ce que
# l'écran promet, l'API doit le tenir seule : ces trois tests éprouvent la règle
# côté serveur, là où elle vaut aussi pour un appel qui ne passe pas par
# l'interface.


def _un_prix_de_bibliotheque(
    client: TestClient, entetes: dict[str, str], version_id: str
) -> dict[str, Any]:
    cree = client.post(
        f"/api/v1/price-books/versions/{version_id}/items",
        headers=entetes,
        json={
            "code": "PU-POSE",
            "label": "Pose au mètre cube",
            "unit_code": "m3",
            "unit_price": "42.00",
            "resource_kind": "labor",
        },
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


def test_creer_un_poste_avec_les_deux_sources_est_refuse(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """Porter les deux ne veut rien dire : le moteur devrait choisir au hasard."""
    prix = _un_prix_de_bibliotheque(seeded_client, admin, chantier["price_book_version"])
    refus = seeded_client.post(
        f"/api/v1/boqs/{chantier['boq']}/items",
        headers=admin,
        json={
            "position": "2",
            "designation": "Poste à deux sources",
            "unit_code": "m3",
            "quantity": "10",
            "kind": "item",
            "price_item_id": prix["id"],
            "composite_price_id": chantier["composite"],
        },
    )
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "conflicting_price_sources"


def test_poser_un_sous_detail_sur_un_poste_deja_tarife_est_refuse(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """La règle porte sur l'état FINAL, pas sur les champs cités.

    Le poste porte déjà un prix de bibliothèque ; la requête ne mentionne que
    le sous-détail. Ne regarder que la requête laisserait passer exactement la
    situation interdite — c'est ce que l'écran évite en effaçant l'autre côté,
    et ce que l'API doit refuser pour qui ne passe pas par l'écran.
    """
    prix = _un_prix_de_bibliotheque(seeded_client, admin, chantier["price_book_version"])
    poste = seeded_client.post(
        f"/api/v1/boqs/{chantier['boq']}/items",
        headers=admin,
        json={
            "position": "3",
            "designation": "Poste tarifé",
            "unit_code": "m3",
            "quantity": "10",
            "kind": "item",
            "price_item_id": prix["id"],
        },
    )
    assert poste.status_code == 201, poste.text

    refus = seeded_client.patch(
        f"/api/v1/boq-items/{poste.json()['id']}",
        headers=admin,
        json={"composite_price_id": chantier["composite"]},
    )
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "conflicting_price_sources"


def test_changer_de_source_en_effacant_l_autre_est_accepte(
    seeded_client: TestClient, admin: dict[str, str], chantier: dict[str, Any]
) -> None:
    """Le geste exact de l'écran : les deux champs partent ensemble.

    Et le devis suit : 10 m3 × 6,00 de déboursé = 60,00, là où le prix de
    bibliothèque n'en produisait aucun (un prix de vente n'est pas un
    déboursé).
    """
    prix = _un_prix_de_bibliotheque(seeded_client, admin, chantier["price_book_version"])
    poste = seeded_client.post(
        f"/api/v1/boqs/{chantier['boq']}/items",
        headers=admin,
        json={
            "position": "4",
            "designation": "Poste qui change de source",
            "unit_code": "m3",
            "quantity": "10",
            "kind": "item",
            "price_item_id": prix["id"],
        },
    ).json()

    bascule = seeded_client.patch(
        f"/api/v1/boq-items/{poste['id']}",
        headers=admin,
        json={"price_item_id": None, "composite_price_id": chantier["composite"]},
    )
    assert bascule.status_code == 200, bascule.text
    assert bascule.json()["price_item_id"] is None
    assert bascule.json()["composite_price_id"] == chantier["composite"]

    # Le poste initial (100 m3) et celui-ci (10 m3) partagent le sous-détail :
    # 110 × 6,00 = 660,00.
    assert _debourse(_calcul(seeded_client, admin, chantier["estimate"])) == Decimal("660.00")
