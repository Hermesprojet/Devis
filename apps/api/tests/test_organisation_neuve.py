"""Ce qu'une organisation neuve doit pouvoir faire elle-même.

Une organisation créée par ``bootstrap`` n'a aucun taux de taxe, aucun prix et
une seule personne. Avant ce travail, aucune route ne permettait d'y remédier :
le premier devis correct exigeait une insertion en base. Ces tests décrivent le
chemin qui l'a remplacé, et surtout ce qu'il refuse.

Ils portent sur deux configurations qu'une entreprise décide elle-même — ses
taxes et son équipe — et sur rien d'autre : le calcul lui-même est éprouvé
ailleurs.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from .conftest import login

TAXES = "/api/v1/organization/tax-rates"
MEMBRES = "/api/v1/organization/members"


@pytest.fixture()
def neuve(client: TestClient) -> dict[str, str]:
    """Une organisation issue du SEUL bootstrap : ni démonstration, ni SQL."""
    from metreo_api.bootstrap import bootstrap
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        organisation, admin, _ = bootstrap(
            session,
            organization_name="Entreprise neuve",
            admin_email="admin@neuve.example",
            admin_full_name="Alice Admin",
        )
        session.commit()
        return {"organization_id": organisation.id, "admin": admin.email}
    finally:
        session.close()


@pytest.fixture()
def admin(client: TestClient, neuve: dict[str, str]) -> dict[str, str]:
    return login(client, neuve["admin"], neuve["organization_id"])


# --------------------------------------------------------------------------
# Ce que le bootstrap NE fait pas
# --------------------------------------------------------------------------


def test_le_bootstrap_n_installe_aucun_taux(client: TestClient, admin: dict[str, str]) -> None:
    """Aucune règle fiscale n'est devinée à la place de l'entreprise.

    Un « TVA 21 % » préinstallé serait faux pour tout travail relevant d'un
    taux réduit, et ferait porter à l'entreprise une affirmation fiscale
    qu'elle n'a jamais prise.
    """
    assert client.get(TAXES, headers=admin).json() == []


def test_le_bootstrap_ne_cree_qu_une_personne(client: TestClient, admin: dict[str, str]) -> None:
    membres = client.get(MEMBRES, headers=admin).json()
    assert [m["email"] for m in membres] == ["admin@neuve.example"]
    assert membres[0]["role"] == "org_admin"


# --------------------------------------------------------------------------
# Les taux de taxe
# --------------------------------------------------------------------------


def test_l_administrateur_cree_lit_et_modifie_un_taux(
    client: TestClient, admin: dict[str, str]
) -> None:
    cree = client.post(
        TAXES,
        headers=admin,
        json={"code": "TVA-21", "label": "TVA 21 %", "rate": "0.21", "source": "Décision interne"},
    )
    assert cree.status_code == 201, cree.text
    assert cree.json()["rate"] == "0.21"

    identifiant = cree.json()["id"]
    modifie = client.patch(
        f"{TAXES}/{identifiant}", headers=admin, json={"label": "TVA vingt-et-un"}
    )
    assert modifie.status_code == 200, modifie.text
    assert modifie.json()["label"] == "TVA vingt-et-un"
    assert modifie.json()["code"] == "TVA-21"


def test_le_code_d_un_taux_n_est_pas_modifiable(client: TestClient, admin: dict[str, str]) -> None:
    """Un devis gelé porte le code : le changer réécrirait son histoire."""
    identifiant = client.post(
        TAXES, headers=admin, json={"code": "TVA-21", "label": "TVA 21 %", "rate": "0.21"}
    ).json()["id"]
    refus = client.patch(f"{TAXES}/{identifiant}", headers=admin, json={"code": "TVA-06"})
    assert refus.status_code == 422


def test_un_taux_zero_est_representable(client: TestClient, admin: dict[str, str]) -> None:
    """Une exonération se DIT, avec son code et son libellé.

    L'absence de taux et un taux nul ne racontent pas la même chose : le
    premier est une configuration manquante, le second une décision.
    """
    cree = client.post(
        TAXES,
        headers=admin,
        json={"code": "EXO-0", "label": "Exonéré (autoliquidation)", "rate": "0"},
    )
    assert cree.status_code == 201, cree.text
    assert Decimal(cree.json()["rate"]) == 0


def test_deux_taux_de_meme_code_qui_se_chevauchent_sont_refuses(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Ils seraient appliqués TOUS LES DEUX, sans que rien ne le signale."""
    premier = {"code": "TVA-21", "label": "TVA 21 %", "rate": "0.21"}
    assert client.post(TAXES, headers=admin, json=premier).status_code == 201
    refus = client.post(TAXES, headers=admin, json=premier)
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "tax_rate_overlap"


def test_deux_codes_differents_peuvent_coexister(client: TestClient, admin: dict[str, str]) -> None:
    """Une entreprise peut légitimement facturer une taxe et une redevance."""
    assert (
        client.post(
            TAXES, headers=admin, json={"code": "TVA-21", "label": "TVA", "rate": "0.21"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            TAXES, headers=admin, json={"code": "REDEV", "label": "Redevance", "rate": "0.02"}
        ).status_code
        == 201
    )


def test_le_meme_code_sur_deux_periodes_disjointes_est_accepte(
    client: TestClient, admin: dict[str, str]
) -> None:
    """C'est le cas normal d'un taux qui change à une date connue."""
    assert (
        client.post(
            TAXES,
            headers=admin,
            json={
                "code": "TVA-21",
                "label": "TVA 21 %",
                "rate": "0.21",
                "applies_to": "2025-12-31",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            TAXES,
            headers=admin,
            json={
                "code": "TVA-21",
                "label": "TVA 22 %",
                "rate": "0.22",
                "applies_from": "2026-01-01",
            },
        ).status_code
        == 201
    )


def test_une_periode_inversee_est_refusee(client: TestClient, admin: dict[str, str]) -> None:
    refus = client.post(
        TAXES,
        headers=admin,
        json={
            "code": "TVA-21",
            "label": "TVA",
            "rate": "0.21",
            "applies_from": "2026-06-01",
            "applies_to": "2026-01-01",
        },
    )
    assert refus.status_code == 422
    assert refus.json()["detail"]["code"] == "invalid_period"


@pytest.mark.parametrize("valeur", ["-0.01", "11", "abc"])
def test_un_taux_hors_bornes_est_refuse(
    client: TestClient, admin: dict[str, str], valeur: str
) -> None:
    refus = client.post(TAXES, headers=admin, json={"code": "X", "label": "X", "rate": valeur})
    assert refus.status_code == 422


def test_retirer_un_taux_du_service_le_sort_des_taux_en_vigueur(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Borner dans le temps, plutôt que supprimer : l'historique survit."""
    identifiant = client.post(
        TAXES, headers=admin, json={"code": "TVA-21", "label": "TVA 21 %", "rate": "0.21"}
    ).json()["id"]
    hier = (date.today() - timedelta(days=1)).isoformat()
    retrait = client.patch(
        f"{TAXES}/{identifiant}", headers=admin, json={"applies_to": hier, "is_default": False}
    )
    assert retrait.status_code == 200, retrait.text

    from metreo_api.db import get_session_factory
    from metreo_api.services import estimating

    session = get_session_factory()()
    try:
        organisation = client.get("/api/v1/organization", headers=admin).json()["id"]
        assert estimating.active_taxes(session, organisation) == ()
    finally:
        session.close()
    # La ligne demeure : le devis gelé qui l'a appliquée reste lisible.
    assert [t["id"] for t in client.get(TAXES, headers=admin).json()] == [identifiant]


def test_un_taux_jamais_applique_peut_etre_supprime(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Corriger une faute de frappe faite il y a deux minutes reste possible."""
    identifiant = client.post(
        TAXES, headers=admin, json={"code": "TVA-12", "label": "Faute", "rate": "0.12"}
    ).json()["id"]
    assert client.delete(f"{TAXES}/{identifiant}", headers=admin).status_code == 204
    assert client.get(TAXES, headers=admin).json() == []


def _devis_gele(client: TestClient, admin: dict[str, str], *, code_taxe: str) -> None:
    """Monte un devis complet et le gèle, dans l'organisation neuve.

    Construit par l'API et non par le jeu de démonstration : le devis
    d'exemple porte une ligne sans prix, que la règle de l'entreprise refuse
    de geler — il ne peut donc pas servir à prouver ce qu'un instantané gelé
    retient.
    """
    assert (
        client.post(
            TAXES,
            headers=admin,
            json={"code": code_taxe, "label": "TVA 21 %", "rate": "0.21"},
        ).status_code
        == 201
    )
    livre = client.post("/api/v1/price-books", headers=admin, json={"name": "Prix"}).json()
    version_prix = client.get(f"/api/v1/price-books/{livre['id']}/versions", headers=admin).json()[
        0
    ]["id"]
    prix = client.post(
        f"/api/v1/price-books/versions/{version_prix}/items",
        headers=admin,
        json={"code": "T.1", "label": "Déblai", "unit_code": "m3", "unit_price": "18.4567"},
    ).json()
    projet = client.post(
        "/api/v1/projects", headers=admin, json={"reference": "GEL-001", "name": "Chantier"}
    ).json()
    bordereau = client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=admin, json={"name": "Métré"}
    ).json()
    assert (
        client.post(
            f"/api/v1/boqs/{bordereau['id']}/items",
            headers=admin,
            json={
                "position": "01.10",
                "designation": "Déblai",
                "unit_code": "m3",
                "quantity": "1250.5",
                "price_item_id": prix["id"],
            },
        ).status_code
        == 201
    )
    estimation = client.post(
        "/api/v1/estimates",
        headers=admin,
        json={
            "project_id": projet["id"],
            "boq_id": bordereau["id"],
            "price_book_version_id": version_prix,
            "name": "Étude",
        },
    ).json()
    version = client.get(f"/api/v1/estimates/{estimation['id']}/versions", headers=admin).json()[0][
        "id"
    ]
    gel = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions/{version}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text


def test_un_taux_porte_par_un_devis_gele_ne_peut_pas_etre_supprime(
    client: TestClient, admin: dict[str, str]
) -> None:
    """L'instantané doit rester lisible : « TVA-21 » doit rester nommable."""
    _devis_gele(client, admin, code_taxe="TVA-21")
    applique = client.get(TAXES, headers=admin).json()[0]
    refus = client.delete(f"{TAXES}/{applique['id']}", headers=admin)
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "tax_rate_in_use"


def test_un_taux_qu_aucun_devis_gele_ne_porte_reste_supprimable(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Le refus vise l'histoire écrite, pas la table."""
    _devis_gele(client, admin, code_taxe="TVA-21")
    jamais_applique = client.post(
        TAXES,
        headers=admin,
        json={"code": "REDEV", "label": "Redevance", "rate": "0.02"},
    ).json()
    assert client.delete(f"{TAXES}/{jamais_applique['id']}", headers=admin).status_code == 204


def test_un_taux_retire_du_service_ne_touche_pas_le_devis_deja_gele(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Le devis remis au client garde le taux qui lui a été appliqué."""
    _devis_gele(client, admin, code_taxe="TVA-21")
    taux = client.get(TAXES, headers=admin).json()[0]
    client.patch(
        f"{TAXES}/{taux['id']}",
        headers=admin,
        json={"applies_to": date.today().isoformat(), "is_default": False},
    )
    estimation = client.get("/api/v1/estimates", headers=admin).json()[0]["id"]
    version = client.get(f"/api/v1/estimates/{estimation}/versions", headers=admin).json()[0]["id"]
    calcul = client.get(
        f"/api/v1/estimates/{estimation}/versions/{version}/computation", headers=admin
    ).json()
    assert calcul["from_snapshot"] is True
    assert [t["code"] for t in calcul["result"]["taxes"]] == ["TVA-21"]


def test_une_etude_creee_avant_les_taxes_les_reprend_des_qu_elles_existent(
    client: TestClient, admin: dict[str, str]
) -> None:
    """L'ordre des gestes n'enferme pas l'entreprise.

    Un BROUILLON est recalculé à chaque lecture, à partir des taux en vigueur
    ce jour-là : une étude créée avant que la TVA ne soit configurée la reprend
    d'elle-même, sans qu'il faille recréer quoi que ce soit. Seul le GEL fige
    les taxes, et c'est là tout son sens.

    Ce test existe parce que la croyance inverse avait produit un écran entier
    — un avertissement « taxes périmées » et un bouton pour recréer la version
    — qui ne pouvait jamais se déclencher. Ce qui suit est ce que le moteur
    fait ; le vérifier empêche de reconstruire ce remède à un mal inexistant.
    """
    livre = client.post("/api/v1/price-books", headers=admin, json={"name": "Prix"}).json()
    version_prix = client.get(f"/api/v1/price-books/{livre['id']}/versions", headers=admin).json()[
        0
    ]["id"]
    prix = client.post(
        f"/api/v1/price-books/versions/{version_prix}/items",
        headers=admin,
        json={"code": "T.1", "label": "Déblai", "unit_code": "m3", "unit_price": "18.4567"},
    ).json()
    projet = client.post(
        "/api/v1/projects", headers=admin, json={"reference": "TARD-001", "name": "Chantier"}
    ).json()
    bordereau = client.post(
        f"/api/v1/projects/{projet['id']}/boqs", headers=admin, json={"name": "Métré"}
    ).json()
    client.post(
        f"/api/v1/boqs/{bordereau['id']}/items",
        headers=admin,
        json={
            "position": "01.10",
            "designation": "Déblai",
            "unit_code": "m3",
            "quantity": "1250.5",
            "price_item_id": prix["id"],
        },
    )
    estimation = client.post(
        "/api/v1/estimates",
        headers=admin,
        json={
            "project_id": projet["id"],
            "boq_id": bordereau["id"],
            "price_book_version_id": version_prix,
            "name": "Étude",
        },
    ).json()
    version = client.get(f"/api/v1/estimates/{estimation['id']}/versions", headers=admin).json()[0][
        "id"
    ]
    calcul = f"/api/v1/estimates/{estimation['id']}/versions/{version}/computation"

    # Sans taux : le TTC vaut le HT, et rien ne le maquille.
    avant = client.get(calcul, headers=admin).json()["result"]
    assert avant["taxes"] == []
    assert avant["total_ttc"] == avant["total_selling_price_ht"]

    # Le taux arrive après coup : le brouillon le reprend, sans geste de plus.
    client.post(TAXES, headers=admin, json={"code": "TVA-21", "label": "TVA 21 %", "rate": "0.21"})
    apres = client.get(calcul, headers=admin).json()["result"]
    assert [t["code"] for t in apres["taxes"]] == ["TVA-21"]
    assert Decimal(apres["total_ttc"]) > Decimal(apres["total_selling_price_ht"])

    # Une fois gelé, plus rien ne bouge : un taux retiré du service ensuite ne
    # touche pas le devis remis au client.
    gel = client.post(
        f"/api/v1/estimates/{estimation['id']}/versions/{version}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text
    taux = client.get(TAXES, headers=admin).json()[0]
    client.patch(
        f"{TAXES}/{taux['id']}",
        headers=admin,
        json={"applies_to": date.today().isoformat(), "is_default": False},
    )
    fige = client.get(calcul, headers=admin).json()
    assert fige["from_snapshot"] is True
    assert [t["code"] for t in fige["result"]["taxes"]] == ["TVA-21"]


# --------------------------------------------------------------------------
# Qui a le droit
# --------------------------------------------------------------------------


@pytest.mark.parametrize("qui", ["metreur@dubois.demo", "lecteur@dubois.demo"])
def test_seul_org_manage_ecrit_les_taux(seeded_client: TestClient, qui: str) -> None:
    """Un métreur utilise la configuration ; il ne la décide pas."""
    entetes = login(seeded_client, qui)
    lecture = seeded_client.get(TAXES, headers=entetes)
    assert lecture.status_code == 200, "un taux doit rester lisible pour qui chiffre"

    refus = seeded_client.post(
        TAXES, headers=entetes, json={"code": "X", "label": "X", "rate": "0.05"}
    )
    assert refus.status_code == 403
    assert refus.json()["detail"]["required_permission"] == "org:manage"

    cible = lecture.json()[0]["id"]
    assert (
        seeded_client.patch(f"{TAXES}/{cible}", headers=entetes, json={"rate": "0"}).status_code
        == 403
    )
    assert seeded_client.delete(f"{TAXES}/{cible}", headers=entetes).status_code == 403


@pytest.mark.parametrize("qui", ["metreur@dubois.demo", "lecteur@dubois.demo"])
def test_seul_user_manage_voit_et_compose_l_equipe(seeded_client: TestClient, qui: str) -> None:
    entetes = login(seeded_client, qui)
    assert seeded_client.get(MEMBRES, headers=entetes).status_code == 403
    refus = seeded_client.post(
        MEMBRES,
        headers=entetes,
        json={"email": "x@exemple.example", "full_name": "X", "role": "viewer"},
    )
    assert refus.status_code == 403
    assert refus.json()["detail"]["required_permission"] == "user:manage"


def test_un_taux_d_une_autre_organisation_est_introuvable(seeded_client: TestClient) -> None:
    a = login(seeded_client, "admin@dubois.demo")
    b = login(seeded_client, "admin@janssens.demo")
    cible = seeded_client.get(TAXES, headers=a).json()[0]["id"]
    assert seeded_client.patch(f"{TAXES}/{cible}", headers=b, json={"rate": "0"}).status_code == 404
    assert seeded_client.delete(f"{TAXES}/{cible}", headers=b).status_code == 404


# --------------------------------------------------------------------------
# L'équipe
# --------------------------------------------------------------------------


def test_l_administrateur_ajoute_un_metreur_et_un_lecteur(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Sans cela, une organisation neuve restait à une seule personne."""
    for adresse, role in (
        ("metreur@neuve.example", "estimator"),
        ("lecteur@neuve.example", "viewer"),
    ):
        cree = client.post(
            MEMBRES, headers=admin, json={"email": adresse, "full_name": adresse, "role": role}
        )
        assert cree.status_code == 201, cree.text
        assert cree.json()["role"] == role

    # Et ils peuvent réellement entrer : c'est le seul sens de l'opération.
    metreur = login(client, "metreur@neuve.example")
    assert client.get("/api/v1/projects", headers=metreur).status_code == 200


def test_la_meme_adresse_ne_rejoint_pas_deux_fois_l_organisation(
    client: TestClient, admin: dict[str, str]
) -> None:
    corps = {"email": "metreur@neuve.example", "full_name": "Marc", "role": "estimator"}
    assert client.post(MEMBRES, headers=admin, json=corps).status_code == 201
    refus = client.post(MEMBRES, headers=admin, json=corps)
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "already_member"


def test_l_acces_se_retire_et_se_rend(client: TestClient, admin: dict[str, str]) -> None:
    membre = client.post(
        MEMBRES,
        headers=admin,
        json={"email": "parti@neuve.example", "full_name": "Parti", "role": "estimator"},
    ).json()
    retrait = client.patch(f"{MEMBRES}/{membre['id']}", headers=admin, json={"is_active": False})
    assert retrait.status_code == 200
    assert retrait.json()["is_active"] is False

    # Retiré, il n'entre plus — mais la ligne demeure, et l'audit qu'il a
    # écrit reste attribuable.
    refus = client.post("/api/v1/auth/dev-login", json={"email": "parti@neuve.example"})
    assert refus.status_code == 403

    rendu = client.patch(f"{MEMBRES}/{membre['id']}", headers=admin, json={"is_active": True})
    assert rendu.json()["is_active"] is True


def test_le_dernier_administrateur_ne_peut_pas_se_retirer_lui_meme(
    client: TestClient, admin: dict[str, str]
) -> None:
    """Sinon l'organisation se referme, et seule une main en base rouvre."""
    moi = client.get(MEMBRES, headers=admin).json()[0]
    refus = client.patch(f"{MEMBRES}/{moi['id']}", headers=admin, json={"is_active": False})
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "last_administrator"

    degradation = client.patch(f"{MEMBRES}/{moi['id']}", headers=admin, json={"role": "viewer"})
    assert degradation.status_code == 409

    # Avec un second administrateur, le geste redevient possible.
    client.post(
        MEMBRES,
        headers=admin,
        json={"email": "second@neuve.example", "full_name": "Second", "role": "org_admin"},
    )
    assert (
        client.patch(f"{MEMBRES}/{moi['id']}", headers=admin, json={"role": "viewer"}).status_code
        == 200
    )


def test_un_collaborateur_d_une_autre_organisation_est_introuvable(
    seeded_client: TestClient,
) -> None:
    a = login(seeded_client, "admin@dubois.demo")
    b = login(seeded_client, "admin@janssens.demo")
    cible = seeded_client.get(MEMBRES, headers=a).json()[0]["id"]
    refus = seeded_client.patch(f"{MEMBRES}/{cible}", headers=b, json={"role": "viewer"})
    assert refus.status_code == 404


def test_les_gestes_de_configuration_sont_journalises(
    client: TestClient, admin: dict[str, str]
) -> None:
    client.post(TAXES, headers=admin, json={"code": "TVA-21", "label": "TVA", "rate": "0.21"})
    client.post(
        MEMBRES,
        headers=admin,
        json={"email": "journal@neuve.example", "full_name": "J", "role": "viewer"},
    )
    actions = {
        evenement["action"]
        for evenement in client.get("/api/v1/audit/events", headers=admin).json()["items"]
    }
    assert {"tax_rate.created", "member.invited"} <= actions
