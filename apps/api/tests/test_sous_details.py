"""Gérer un sous-détail de prix par l'API : ce qui passe, ce qui est refusé.

**Ce qui manquait, mesuré au navigateur depuis une organisation vide.** Le
serveur savait CRÉER et LISTER un sous-détail. Il ne savait ni en rendre le
détail, ni le modifier, ni le dupliquer, ni le supprimer, ni prévisualiser son
coût — et le web n'en affichait qu'un badge quand un identifiant existait.
Résultat pour un métreur : un sous-détail se posait par appel d'API et ne se
reprenait plus jamais.

Ce fichier éprouve les cinq routes qui ferment cette rupture, et surtout leurs
refus. Un sous-détail porte les rendements et les prix de ressources de toute
une entreprise : ce qui compte n'est pas qu'on puisse l'écrire, c'est qu'on ne
puisse pas l'écraser sans le savoir.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version(seeded_client: TestClient, admin: dict[str, str]) -> str:
    """Une version de bibliothèque BROUILLON, à nous, vide de sous-détails."""
    livre = seeded_client.post("/api/v1/price-books", headers=admin, json={"name": "Sous-détails"})
    assert livre.status_code == 201, livre.text
    creee = seeded_client.post(
        f"/api/v1/price-books/{livre.json()['id']}/versions",
        headers=admin,
        json={"label": "v1"},
    )
    assert creee.status_code == 201, creee.text
    return str(creee.json()["id"])


FORFAIT: dict[str, Any] = {
    "component_type": "lump_sum",
    "label": "Amenée et repli",
    "resource_kind": "other",
    "lump_sum_amount": "250.00",
}


def _creer(
    client: TestClient, entetes: dict[str, str], version: str, **remplacements: Any
) -> dict[str, Any]:
    corps: dict[str, Any] = {
        "code": "SD-1",
        "label": "Sous-détail",
        "unit_code": "m3",
        "components": [FORFAIT],
        **remplacements,
    }
    reponse = client.post(
        f"/api/v1/price-books/versions/{version}/composites", headers=entetes, json=corps
    )
    assert reponse.status_code == 201, reponse.text
    return dict(reponse.json())


# --------------------------------------------------------------------------
# 1. Les quatre types de composants, et leur arithmétique
# --------------------------------------------------------------------------


def _apercu(
    client: TestClient,
    entetes: dict[str, str],
    version: str,
    composants: list[dict[str, Any]],
    unite: str = "m3",
) -> dict[str, Any]:
    reponse = client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=entetes,
        json={"unit_code": unite, "components": composants},
    )
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def test_un_forfait_se_previsualise_a_son_montant(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Le cas le plus simple, et le plus vérifiable à la main : 250 = 250."""
    rendu = _apercu(seeded_client, admin, version, [FORFAIT])
    assert rendu["unit_cost_display"] == "250.00"
    assert rendu["currency"] == "EUR"


def test_une_consommation_applique_sa_perte(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """2 t × 1,05 × 15,00 = 31,50. Le chiffre se recalcule de tête."""
    rendu = _apercu(
        seeded_client,
        admin,
        version,
        [
            {
                "component_type": "consumption",
                "label": "Grave",
                "resource_kind": "material",
                "consumption": "2",
                "resource_unit_code": "t",
                "unit_price": "15.00",
                "loss_ratio": "0.05",
            }
        ],
    )
    assert rendu["unit_cost_display"] == "31.50"
    assert [n["label"] for n in rendu["by_kind"]] == ["Matériaux"]


def test_un_rendement_divise_par_l_heure_et_multiplie_par_l_equipe(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """1 m3 ÷ 10 m3/h × 60,00 €/h × 2 personnes = 12,00."""
    rendu = _apercu(
        seeded_client,
        admin,
        version,
        [
            {
                "component_type": "output_rate",
                "label": "Équipe de pose",
                "resource_kind": "labor",
                "output_rate": "10",
                "hourly_rate": "60.00",
                "crew_size": "2",
            }
        ],
    )
    assert rendu["unit_cost_display"] == "12.00"


def test_une_rotation_arrondit_au_camion_superieur(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """1 t à évacuer, camion de 10 t : une rotation entière, pas un dixième.

    C'est le point du type `rotation` — un camion ne part pas à 10 % chargé, et
    un calcul au prorata sous-estimerait systématiquement l'évacuation.
    40,00 par rotation + 20 km × 1,00 = 60,00.

    Le sous-détail est en tonnes, comme la charge utile : croiser m³ et tonnes
    ici demanderait une masse volumique sourcée — c'est le refus éprouvé plus
    bas, et le mêler à celui-ci rendrait les deux illisibles.
    """
    rendu = _apercu(
        seeded_client,
        admin,
        version,
        [
            {
                "component_type": "rotation",
                "label": "Camion",
                "resource_kind": "transport",
                "payload_value": "10",
                "payload_unit_code": "t",
                "cost_per_rotation": "40.00",
                "round_up": True,
                "distance_km": "20",
                "rate_per_km": "1.00",
            }
        ],
        unite="t",
    )
    assert rendu["unit_cost_display"] == "60.00"


def test_la_ventilation_separe_les_natures(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Matériaux, main-d'œuvre et transport comptés séparément, et leur somme.

    250 (divers) + 31,50 (matériaux) + 12,00 (main-d'œuvre) = 293,50.
    """
    rendu = _apercu(
        seeded_client,
        admin,
        version,
        [
            FORFAIT,
            {
                "component_type": "consumption",
                "label": "Grave",
                "resource_kind": "material",
                "consumption": "2",
                "resource_unit_code": "t",
                "unit_price": "15.00",
                "loss_ratio": "0.05",
            },
            {
                "component_type": "output_rate",
                "label": "Équipe",
                "resource_kind": "labor",
                "output_rate": "10",
                "hourly_rate": "60.00",
                "crew_size": "2",
            },
        ],
    )
    assert rendu["unit_cost_display"] == "293.50"
    par_nature = {n["label"]: n["amount_display"] for n in rendu["by_kind"]}
    assert par_nature["Matériaux"] == "31.50"
    assert par_nature["Main-d'œuvre"] == "12.00"
    assert par_nature["Divers"] == "250.00"


# --------------------------------------------------------------------------
# 2. La masse volumique : jamais sans sa source
# --------------------------------------------------------------------------


def test_une_conversion_masse_volume_exige_une_source(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Une densité sans source est refusée, et le message le dit.

    C'est l'invariant que le produit tient partout : un chiffre qui change une
    unité doit pouvoir être remonté à son origine. Une densité inventée
    fausserait toute une évacuation sans laisser de trace.
    """
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=admin,
        json={
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "consumption",
                    "label": "Terres",
                    "resource_kind": "disposal",
                    "consumption": "1",
                    "resource_unit_code": "t",
                    "unit_price": "12.00",
                    "convert_boq_quantity": True,
                    "density_value": "1800",
                }
            ],
        },
    )
    assert refus.status_code == 422, refus.text
    # Refusé par le SCHÉMA d'entrée, avant même le service : c'est la couche la
    # plus haute qui puisse le voir, et donc la bonne. Le message nomme la
    # source, pour que la personne sache quoi fournir.
    assert "source" in refus.text


def test_une_conversion_masse_volume_sourcee_est_acceptee(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """1 m3 × 1,8 t/m3 × 12,00 = 21,60, et la source voyage avec le résultat."""
    rendu = _apercu(
        seeded_client,
        admin,
        version,
        [
            {
                "component_type": "consumption",
                "label": "Terres",
                "resource_kind": "disposal",
                "consumption": "1",
                "resource_unit_code": "t",
                "unit_price": "12.00",
                "convert_boq_quantity": True,
                "density_value": "1800",
                "density_source": "Rapport fictif GT-2026-018, p. 12",
            }
        ],
    )
    assert rendu["unit_cost_display"] == "21.60"
    assert rendu["components"][0]["density_source"] == "Rapport fictif GT-2026-018, p. 12"


def test_une_unite_inconnue_est_refusee_avec_son_nom(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=admin,
        json={
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "consumption",
                    "label": "Quelque chose",
                    "resource_kind": "material",
                    "consumption": "1",
                    "resource_unit_code": "brouettes",
                    "unit_price": "1.00",
                }
            ],
        },
    )
    assert refus.status_code == 422, refus.text
    problemes = refus.json()["detail"]["problems"]
    assert any("brouettes" in p["message"] for p in problemes)


def test_un_champ_obligatoire_manquant_nomme_le_composant_fautif(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """L'index du composant est rendu : sans lui, il faut relire les vingt."""
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=admin,
        json={
            "unit_code": "m3",
            "components": [
                FORFAIT,
                {
                    "component_type": "rotation",
                    "label": "Camion sans charge utile",
                    "resource_kind": "transport",
                    "cost_per_rotation": "40.00",
                },
            ],
        },
    )
    assert refus.status_code == 422, refus.text
    # Le schéma discriminé nomme le composant fautif par son INDEX dans `loc` :
    # sans lui, il faudrait relire les vingt.
    detail = refus.json()["detail"]
    localisations = [e["loc"] for e in detail if isinstance(e.get("loc"), list)]
    # `['body', 'components', 1, 'rotation', 'payload_value']` : l'index du
    # composant ET le champ manquant, ce qu'il faut pour surligner la bonne case.
    assert any(1 in loc and "payload_value" in loc for loc in localisations), detail


def test_au_dela_du_plafond_de_composants_l_api_refuse(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Au-delà, ce n'est plus un sous-détail mais un bordereau."""
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=admin,
        json={"unit_code": "m3", "components": [FORFAIT] * 201},
    )
    assert refus.status_code == 422, refus.text


# --------------------------------------------------------------------------
# 3. Lire, modifier, dupliquer
# --------------------------------------------------------------------------


def test_le_detail_porte_de_quoi_decider_des_commandes_offertes(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """`revision`, `version_published` et `referenced_by` voyagent avec la ligne.

    L'écran ne doit proposer aucune commande qui échouerait ; il ne peut le
    savoir qu'en le lisant ici. Les redeviner côté web donnerait deux vérités.
    """
    cree = _creer(seeded_client, admin, version)
    lu = seeded_client.get(f"/api/v1/price-books/composites/{cree['id']}", headers=admin)
    assert lu.status_code == 200, lu.text
    detail = lu.json()
    assert detail["revision"] == 1
    assert detail["version_published"] is False
    assert detail["referenced_by"] == 0
    assert detail["is_demo_data"] is False


def test_une_modification_remplace_les_composants_et_avance_la_revision(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version)
    modifie = seeded_client.put(
        f"/api/v1/price-books/composites/{cree['id']}",
        headers=admin,
        json={
            "code": "SD-1",
            "label": "Renommé",
            "unit_code": "m3",
            "revision": cree["revision"],
            "components": [
                {**FORFAIT, "lump_sum_amount": "300.00"},
                {**FORFAIT, "label": "Second forfait", "lump_sum_amount": "10.00"},
            ],
        },
    )
    assert modifie.status_code == 200, modifie.text
    rendu = modifie.json()
    assert rendu["label"] == "Renommé"
    assert rendu["revision"] == 2
    assert len(rendu["components"]) == 2
    # Les anciens composants ne survivent pas au remplacement.
    assert all(c["lump_sum_amount"] != "250.00" for c in rendu["components"])


def test_une_modification_fondee_sur_une_revision_perimee_est_refusee(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Deux éditeurs, et le second n'écrase pas le premier en silence.

    C'est la garantie qui justifie la colonne `revision` : sans elle, la
    seconde écriture gagnerait sans que personne ne l'apprenne, et un
    rendement corrigé disparaîtrait d'un sous-détail que toute l'entreprise
    utilise.
    """
    cree = _creer(seeded_client, admin, version)
    corps = {
        "code": "SD-1",
        "label": "Premier éditeur",
        "unit_code": "m3",
        "revision": cree["revision"],
        "components": [FORFAIT],
    }
    premier = seeded_client.put(
        f"/api/v1/price-books/composites/{cree['id']}", headers=admin, json=corps
    )
    assert premier.status_code == 200, premier.text

    # Le second a chargé la ligne AVANT le premier : son jeton est périmé.
    second = seeded_client.put(
        f"/api/v1/price-books/composites/{cree['id']}",
        headers=admin,
        json={**corps, "label": "Second éditeur"},
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "composite_stale"
    assert detail["current_revision"] == 2

    # Et le travail du premier est intact.
    lu = seeded_client.get(f"/api/v1/price-books/composites/{cree['id']}", headers=admin)
    assert lu.json()["label"] == "Premier éditeur"


def test_dupliquer_copie_les_composants_sous_un_code_neuf(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version, components=[FORFAIT, {**FORFAIT, "label": "B"}])
    copie = seeded_client.post(
        f"/api/v1/price-books/composites/{cree['id']}/duplicate",
        headers=admin,
        json={"code": "SD-2", "label": "Variante"},
    )
    assert copie.status_code == 201, copie.text
    rendu = copie.json()
    assert rendu["code"] == "SD-2"
    assert rendu["label"] == "Variante"
    assert len(rendu["components"]) == 2
    assert rendu["id"] != cree["id"]


def test_dupliquer_sous_un_code_deja_pris_est_refuse(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version)
    refus = seeded_client.post(
        f"/api/v1/price-books/composites/{cree['id']}/duplicate",
        headers=admin,
        json={"code": "SD-1"},
    )
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "duplicate_code"


def test_une_copie_ne_porte_jamais_le_marqueur_de_demonstration(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Ce qu'un utilisateur duplique devient SA donnée, plus une démonstration."""
    livres = seeded_client.get("/api/v1/price-books", headers=admin).json()
    versions = seeded_client.get(
        f"/api/v1/price-books/{livres[0]['id']}/versions", headers=admin
    ).json()
    semes = seeded_client.get(
        f"/api/v1/price-books/versions/{versions[0]['id']}/composites", headers=admin
    ).json()
    demonstration = next(c for c in semes if c["is_demo_data"])

    copie = seeded_client.post(
        f"/api/v1/price-books/composites/{demonstration['id']}/duplicate",
        headers=admin,
        json={"code": "SD-COPIE"},
    )
    assert copie.status_code == 201, copie.text
    assert copie.json()["is_demo_data"] is False


# --------------------------------------------------------------------------
# 4. Supprimer — et les deux cas où c'est refusé
# --------------------------------------------------------------------------


def test_un_sous_detail_libre_se_supprime(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version)
    efface = seeded_client.delete(f"/api/v1/price-books/composites/{cree['id']}", headers=admin)
    assert efface.status_code == 204, efface.text
    assert (
        seeded_client.get(f"/api/v1/price-books/composites/{cree['id']}", headers=admin).status_code
        == 404
    )


def test_supprimer_un_sous_detail_utilise_est_refuse_en_409(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """`SET NULL` laisserait le poste sans prix sans le dire.

    Le devis brouillon deviendrait incalculable au prochain recalcul, loin du
    geste qui l'a causé. Le refus nomme le nombre de postes pour que la
    personne sache quoi faire.
    """
    livres = seeded_client.get("/api/v1/price-books", headers=admin).json()
    versions = seeded_client.get(
        f"/api/v1/price-books/{livres[0]['id']}/versions", headers=admin
    ).json()
    semes = seeded_client.get(
        f"/api/v1/price-books/versions/{versions[0]['id']}/composites", headers=admin
    ).json()
    utilise = next(c for c in semes if c["referenced_by"] > 0)

    refus = seeded_client.delete(f"/api/v1/price-books/composites/{utilise['id']}", headers=admin)
    assert refus.status_code == 409, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "composite_referenced"
    assert detail["referenced_by"] == utilise["referenced_by"]


# --------------------------------------------------------------------------
# 5. Une version publiée est close
# --------------------------------------------------------------------------


def _publier(client: TestClient, entetes: dict[str, str], version: str) -> None:
    publiee = client.post(
        f"/api/v1/price-books/versions/{version}/publish", headers=entetes, json={}
    )
    assert publiee.status_code == 200, publiee.text


def test_une_version_publiee_refuse_modification_duplication_et_suppression(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Trois portes, un seul refus : la version est figée, tout ce qu'elle porte aussi."""
    cree = _creer(seeded_client, admin, version)
    _publier(seeded_client, admin, version)

    modifie = seeded_client.put(
        f"/api/v1/price-books/composites/{cree['id']}",
        headers=admin,
        json={
            "code": "SD-1",
            "label": "Tentative",
            "unit_code": "m3",
            "revision": cree["revision"],
            "components": [FORFAIT],
        },
    )
    assert modifie.status_code == 409, modifie.text

    copie = seeded_client.post(
        f"/api/v1/price-books/composites/{cree['id']}/duplicate",
        headers=admin,
        json={"code": "SD-9"},
    )
    assert copie.status_code == 409, copie.text

    efface = seeded_client.delete(f"/api/v1/price-books/composites/{cree['id']}", headers=admin)
    assert efface.status_code == 409, efface.text


def test_le_detail_annonce_qu_une_version_publiee_est_en_lecture_seule(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """L'écran doit pouvoir le dire AVANT de proposer une commande."""
    cree = _creer(seeded_client, admin, version)
    assert cree["version_published"] is False
    _publier(seeded_client, admin, version)
    lu = seeded_client.get(f"/api/v1/price-books/composites/{cree['id']}", headers=admin)
    assert lu.json()["version_published"] is True


# --------------------------------------------------------------------------
# 6. Permissions et cloisonnement
# --------------------------------------------------------------------------


def test_un_lecteur_ne_peut_ni_modifier_ni_supprimer(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version)
    lecteur = login(seeded_client, "lecteur@dubois.demo")

    modifie = seeded_client.put(
        f"/api/v1/price-books/composites/{cree['id']}",
        headers=lecteur,
        json={
            "code": "SD-1",
            "label": "Tentative",
            "unit_code": "m3",
            "revision": 1,
            "components": [FORFAIT],
        },
    )
    assert modifie.status_code == 403, modifie.text
    assert (
        seeded_client.delete(
            f"/api/v1/price-books/composites/{cree['id']}", headers=lecteur
        ).status_code
        == 403
    )


def test_la_previsualisation_exige_la_lecture_des_couts(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Elle rend des montants de ressources : c'est un coût interne.

    Un lecteur qui n'a pas `COST_READ` ne doit pas obtenir par cette route ce
    que la matrice lui refuse partout ailleurs.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=lecteur,
        json={"unit_code": "m3", "components": [FORFAIT]},
    )
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "cost:read"


def test_un_sous_detail_d_une_autre_organisation_repond_404(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    cree = _creer(seeded_client, admin, version)
    etranger = login(seeded_client, "admin@janssens.demo")
    for appel in (
        seeded_client.get(f"/api/v1/price-books/composites/{cree['id']}", headers=etranger),
        seeded_client.delete(f"/api/v1/price-books/composites/{cree['id']}", headers=etranger),
    ):
        assert appel.status_code == 404, appel.text


def test_croiser_volume_et_masse_sans_densite_est_refuse_par_le_moteur(
    seeded_client: TestClient, admin: dict[str, str], version: str
) -> None:
    """Un sous-détail en m³ et un camion chargé en tonnes : combien de rotations ?

    Le moteur ne devine pas. C'est le cas central du terrassement, et le seul
    honnête est le refus : une masse volumique inventée changerait le nombre de
    camions, donc le prix, sans que rien ne le signale. Le refus nomme les deux
    unités et ce qui manque.
    """
    refus = seeded_client.post(
        f"/api/v1/price-books/versions/{version}/composites/preview",
        headers=admin,
        json={
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "rotation",
                    "label": "Camion",
                    "resource_kind": "transport",
                    "payload_value": "10",
                    "payload_unit_code": "t",
                    "cost_per_rotation": "40.00",
                }
            ],
        },
    )
    assert refus.status_code == 422, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "ambiguous_conversion"
    assert detail["context"]["required"] == "density"
