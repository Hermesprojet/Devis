"""Détruire une organisation : ce qui est refusé, ce qui reste écrit.

**Le défaut, reproduit avant correction.** Sur base PostgreSQL jetable, un
`DELETE FROM organizations` portant un devis émis donnait :

    ligne issued_quotes   1 → 0     le devis disparaît sans un mot
    journal d'audit       9 → 0     la trace de l'émission disparaît avec lui
    fichier PDF           présent   et il le reste, octet pour octet

Le troisième fait condamne les deux autres : une purge motivée par un
effacement détruisait sa propre preuve tout en conservant le document du
client. C'est l'inverse exact de ce qu'un effacement doit produire.

La politique posée par `a5b6c7d8e9fa` tient en une phrase — rien ne se détruit
sans un écrit préalable qui dit ce qui va être détruit, et qui survit à la
destruction — et ce fichier la démontre point par point.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from metreo_api.config import get_settings
from metreo_api.db import get_session_factory
from metreo_api.models import IssuedQuote, Organization, OrganizationPurge, OrganizationSettings
from metreo_api.services import conservation
from metreo_api.services.document_storage import StockageLocal

from .conftest import login
from .emission import graphe_complet


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def devis(seeded_client: TestClient, admin: dict[str, str]) -> dict:
    return graphe_complet(seeded_client, admin, "PURGE-001")


@pytest.fixture()
def organisation(seeded_client: TestClient, admin: dict[str, str]) -> str:
    return str(seeded_client.get("/api/v1/auth/me", headers=admin).json()["organization_id"])


def _stockage() -> StockageLocal:
    return StockageLocal(get_settings().storage_root)


def _regler_la_retention(organization_id: str, annees: int | None) -> None:
    with get_session_factory()() as session:
        reglages = session.get(OrganizationSettings, organization_id)
        assert reglages is not None
        reglages.quote_retention_years = annees
        session.commit()


def _vieillir(quote_id: str, annees: int) -> None:
    """Recule la date d'émission, pour éprouver un seuil sans attendre des ans."""
    with get_session_factory()() as session:
        devis = session.get(IssuedQuote, quote_id)
        assert devis is not None
        devis.issued_at = devis.issued_at - timedelta(days=int(annees * 365.25) + 1)
        session.commit()


# --------------------------------------------------------------------------
# 1. La cascade silencieuse n'existe plus
# --------------------------------------------------------------------------


def test_supprimer_l_organisation_est_desormais_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Le cas exact qui a été mesuré, et qui échoue maintenant bruyamment.

    C'était la dernière porte par laquelle un devis émis disparaissait sans un
    mot. `RESTRICT` la ferme : la base refuse, elle ne détruit pas en silence.
    """
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError):
            session.execute(text("DELETE FROM organizations WHERE id = :i"), {"i": organisation})
            session.flush()
        session.rollback()

    with get_session_factory()() as session:
        reste = session.execute(
            text("SELECT COUNT(*) FROM issued_quotes WHERE id = :i"), {"i": devis["id"]}
        ).scalar()
    assert reste == 1, "le devis a disparu alors que la suppression devait être refusée"


def test_detruire_un_devis_sans_purge_inscrite_est_refuse(
    seeded_client: TestClient, devis: dict
) -> None:
    """Le déclencheur ne demande plus « l'organisation existe-t-elle ».

    Il demande « une purge inscrite autorise-t-elle ceci ». La ligne du
    registre EST l'autorisation : sans elle, la base refuse.
    """
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError):
            session.execute(text("DELETE FROM issued_quotes WHERE id = :i"), {"i": devis["id"]})
            session.flush()
        session.rollback()


# --------------------------------------------------------------------------
# 2. Les trois refus, avant toute écriture
# --------------------------------------------------------------------------


def test_sans_politique_decidee_la_purge_refuse_plutot_que_de_supposer(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """`None` veut dire « non tranchée », jamais « sans limite ».

    C'est le cœur de la décision : une durée de conservation est une règle
    réglementaire, le dépôt n'en détient aucune de datée et sourcée, donc le
    code n'en invente pas. Un défaut à sept ans serait un avis juridique rendu
    par une valeur par défaut.
    """
    _regler_la_retention(organisation, None)
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(session, organization_id=organisation, reason="demande du client")
    assert refus.value.code == "quote_retention_undecided"


def test_un_devis_encore_dans_sa_duree_retient_toute_l_organisation(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    _regler_la_retention(organisation, 7)
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(session, organization_id=organisation, reason="demande du client")
    assert refus.value.code == "quote_retention_not_elapsed"
    assert devis["number"] in refus.value.context["retained"]


def test_une_destruction_sans_motif_ecrit_est_refusee(
    seeded_client: TestClient, organisation: str
) -> None:
    _regler_la_retention(organisation, 7)
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(session, organization_id=organisation, reason="   ")
    assert refus.value.code == "purge_reason_required"


def test_un_refus_n_ecrit_rien_au_registre(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Trois refus enchaînés ne laissent aucune ligne derrière eux."""
    _regler_la_retention(organisation, None)
    with get_session_factory()() as session:
        for motif in ("", "demande du client"):
            with pytest.raises(conservation.PurgeRefusee):
                conservation.demander(session, organization_id=organisation, reason=motif)
        session.rollback()
    with get_session_factory()() as session:
        assert session.query(OrganizationPurge).count() == 0


# --------------------------------------------------------------------------
# 3. La purge elle-même
# --------------------------------------------------------------------------


def test_une_purge_echue_detruit_lignes_et_fichiers_et_laisse_son_registre(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Le parcours complet, et les quatre faits mesurés à la fin.

    Ce test est l'inverse mesuré de la reproduction : la ligne part, le FICHIER
    part avec elle, et le registre — lui — reste.
    """
    _regler_la_retention(organisation, 7)
    _vieillir(devis["id"], 8)

    with get_session_factory()() as session:
        cle = session.get(IssuedQuote, devis["id"]).pdf_storage_key
    fichier = _stockage().chemin(cle)
    assert fichier.exists(), "le PDF devrait exister avant la purge"

    with get_session_factory()() as session:
        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason="demande d'effacement du responsable",
        )
        identifiant = purge.id
        assert purge.quote_count == 1
        assert purge.documents[0]["sha256"] == devis["pdf_sha256"]
        conservation.executer(session, purge)
        session.commit()

    # Les lignes sont parties, l'organisation aussi.
    with get_session_factory()() as session:
        assert session.get(Organization, organisation) is None
        assert session.get(IssuedQuote, devis["id"]) is None

    # Le fichier ne part qu'ensuite, et c'est délibéré : le volume ne participe
    # pas à la transaction. Tant qu'il est là, le registre le nomme.
    assert fichier.exists()
    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge.status == "rows_deleted"
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    assert not fichier.exists(), "le PDF est resté sur le volume après la purge"
    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge.status == "completed"
        assert purge.files_deleted == 1
        assert purge.files_failed == []
        assert conservation.orphelins(session, _stockage()) == []


def test_le_registre_survit_a_l_organisation_qu_il_decrit(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """La raison d'être de la table, et la raison de son absence de clé étrangère.

    Le journal d'audit porte un `organization_id` en CASCADE : il meurt avec
    l'organisation. Un registre qui ferait pareil ne prouverait rien.
    """
    _regler_la_retention(organisation, 7)
    _vieillir(devis["id"], 8)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason="effacement demandé"
        )
        identifiant = purge.id
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    with get_session_factory()() as session:
        assert session.get(Organization, organisation) is None
        journal = session.execute(
            text("SELECT COUNT(*) FROM audit_events WHERE organization_id = :i"),
            {"i": organisation},
        ).scalar()
        assert journal == 0, "le journal d'audit meurt avec l'organisation, c'est connu"

        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None, "le registre a disparu avec ce qu'il enregistrait"
        assert purge.organization_id == organisation
        assert purge.quote_count == 1


def test_le_registre_ne_conserve_aucun_nom(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Prouver une destruction sans réintroduire ce qu'elle a effacé.

    Le registre porte des identifiants techniques, des empreintes et des
    chemins de stockage — qui ne sont eux-mêmes que des identifiants. Aucun nom
    d'organisation, de client ni de chantier n'y entre.
    """
    _regler_la_retention(organisation, 7)
    _vieillir(devis["id"], 8)
    with get_session_factory()() as session:
        avant = session.get(Organization, organisation)
        noms = {avant.name, avant.legal_name or avant.name}
        client_vu = session.get(IssuedQuote, devis["id"]).client_snapshot
        noms.add(str(client_vu.get("name", "")))

        purge = conservation.demander(
            session, organization_id=organisation, reason="effacement demandé"
        )
        conservation.executer(session, purge)
        session.commit()
        identifiant = purge.id

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        serialise = str([purge.organization_id, purge.reason, purge.documents, purge.files_failed])
    for nom in noms:
        if nom:
            assert nom not in serialise, f"le registre a conservé « {nom} »"


# --------------------------------------------------------------------------
# 4. Une purge interrompue se termine, elle ne laisse pas d'orphelin
# --------------------------------------------------------------------------


def test_une_purge_interrompue_se_reprend_sans_rien_redecouvrir(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """C'est ce qui justifie l'ordre choisi : lignes d'abord, fichiers ensuite.

    L'inverse laisserait des lignes désignant des fichiers absents — un devis
    qui existe et ne se télécharge plus. Ici l'interruption laisse au pire un
    fichier que le registre NOMME, donc que la reprise retrouve.
    """
    _regler_la_retention(organisation, 7)
    _vieillir(devis["id"], 8)
    with get_session_factory()() as session:
        cle = session.get(IssuedQuote, devis["id"]).pdf_storage_key
    fichier = _stockage().chemin(cle)

    # On s'arrête volontairement après les lignes : c'est la panne à couvrir.
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason="effacement demandé"
        )
        identifiant = purge.id
        conservation.executer(session, purge)
        session.commit()

    assert fichier.exists()
    with get_session_factory()() as session:
        assert conservation.orphelins(session, _stockage()) == [cle]

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        conservation.reprendre(session, purge, _stockage())
        session.commit()

    assert not fichier.exists()
    with get_session_factory()() as session:
        assert session.get(OrganizationPurge, identifiant).status == "completed"
        assert conservation.orphelins(session, _stockage()) == []


def test_reprendre_une_purge_terminee_ne_fait_rien(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Idempotence : rejouer la reprise ne casse rien et ne recompte pas."""
    _regler_la_retention(organisation, 7)
    _vieillir(devis["id"], 8)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason="effacement demandé"
        )
        identifiant = purge.id
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        conservation.reprendre(session, purge, _stockage())
        conservation.reprendre(session, purge, _stockage())
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge.status == "completed"
        assert purge.files_deleted == 1


def test_une_purge_refermee_n_autorise_plus_rien(
    seeded_client: TestClient, admin: dict[str, str], organisation: str
) -> None:
    """`completed` ne figure pas parmi les statuts qui ouvrent le déclencheur.

    Sans quoi une purge ancienne resterait une autorisation permanente de
    détruire les devis d'une organisation recréée sous le même identifiant.
    """
    _regler_la_retention(organisation, 1)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason="organisation vide"
        )
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()
        assert purge.status == "completed"

    # L'organisation est partie ; on vérifie la condition SQL elle-même, qui
    # est ce que le déclencheur interroge.
    with get_session_factory()() as session:
        ouvre = session.execute(
            text(
                "SELECT COUNT(*) FROM organization_purges "
                "WHERE organization_id = :i AND status IN ('requested', 'rows_deleted')"
            ),
            {"i": organisation},
        ).scalar()
    assert ouvre == 0


# --------------------------------------------------------------------------
# 5. La frontière : le seuil se compte à la date exacte
# --------------------------------------------------------------------------


def test_le_seuil_de_conservation_se_compte_a_la_date(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """La veille retient, le jour même libère. Sans tâche planifiée."""
    _regler_la_retention(organisation, 7)
    with get_session_factory()() as session:
        emis = session.get(IssuedQuote, devis["id"]).issued_at
        # L'échéance est la date ANNIVERSAIRE, pas une tranche de jours moyens :
        # une première version comptait en 365,25 jours et tombait un jour à
        # côté. Ici l'attente est écrite indépendamment du code qu'elle juge.
        attendue = emis.date().replace(year=emis.year + 7)
        assert conservation.echeance(emis, 7) == attendue

        veille = conservation.devis_retenus(
            session, organisation, annees=7, aujourdhui=attendue - timedelta(days=1)
        )
        jour = conservation.devis_retenus(session, organisation, annees=7, aujourdhui=attendue)
    assert [d.number for d in veille] == [devis["number"]]
    assert jour == []


def test_une_organisation_sans_devis_emis_se_purge_des_que_la_duree_est_reglee(
    seeded_client: TestClient, organisation: str
) -> None:
    """Le cas simple doit rester simple : pas de devis, rien à retenir."""
    _regler_la_retention(organisation, 7)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason="fin de contrat", aujourdhui=date.today()
        )
        assert purge.quote_count == 0
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()
        assert purge.status == "completed"

    with get_session_factory()() as session:
        assert session.get(Organization, organisation) is None


# --------------------------------------------------------------------------
# 6. La durée se règle par l'API, et « non tranchée » est une valeur
# --------------------------------------------------------------------------


def test_la_duree_se_lit_et_se_regle_par_l_api(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Une organisation neuve n'a AUCUNE durée, et l'API le dit sans détour."""
    lu = seeded_client.get("/api/v1/organization/settings", headers=admin).json()
    assert lu["quote_retention_years"] is None, (
        "une durée par défaut se serait glissée dans la configuration"
    )

    ecrit = seeded_client.patch(
        "/api/v1/organization/settings", headers=admin, json={"quote_retention_years": 10}
    )
    assert ecrit.status_code == 200, ecrit.text
    assert ecrit.json()["quote_retention_years"] == 10


def test_remettre_la_duree_a_non_tranchee_est_possible_et_distinct_d_un_silence(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """`null` explicite remet à « non tranchée » ; l'absence ne change rien.

    Les deux passeraient pour identiques sans `exclude_unset`. Pour ce champ la
    nuance décide : `null` REFERME la porte de la destruction, un silence la
    laisse dans l'état où elle était.
    """
    seeded_client.patch(
        "/api/v1/organization/settings", headers=admin, json={"quote_retention_years": 10}
    )

    silence = seeded_client.patch(
        "/api/v1/organization/settings", headers=admin, json={"missing_price_policy": "warn"}
    )
    assert silence.json()["quote_retention_years"] == 10, "un silence a effacé le réglage"

    remise = seeded_client.patch(
        "/api/v1/organization/settings", headers=admin, json={"quote_retention_years": None}
    )
    assert remise.status_code == 200, remise.text
    assert remise.json()["quote_retention_years"] is None


def test_une_duree_absurde_est_refusee_a_la_saisie(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    for valeur in (-1, 101):
        refus = seeded_client.patch(
            "/api/v1/organization/settings",
            headers=admin,
            json={"quote_retention_years": valeur},
        )
        assert refus.status_code == 422, valeur


# --------------------------------------------------------------------------
# 7. Le registre n'est pas une ressource d'API
# --------------------------------------------------------------------------


def test_le_registre_n_est_exposé_par_aucune_route(client: TestClient) -> None:
    """Détruire un locataire entier ne s'expose pas en HTTP, et se vérifie.

    L'ADR 0006 laisse cette question ouverte volontairement : une route qui
    efface une organisation a un rayon d'action considérable, personne ne l'a
    demandée, et l'ouvrir serait une décision séparée. Tant qu'elle ne l'est
    pas, ce test tient la frontière — il rougira le jour où quelqu'un exposera
    le registre sans avoir tranché.

    La purge se fait par `scripts/purger_organisation.py`, qui montre ce qu'il
    va détruire avant de le faire.
    """
    schema = client.get("/openapi.json").json()
    chemins = [chemin for chemin in schema["paths"] if "purge" in chemin.lower()]
    assert chemins == [], f"le registre de purge est exposé : {chemins}"

    modeles = [nom for nom in schema.get("components", {}).get("schemas", {}) if "urge" in nom]
    assert modeles == [], f"le registre de purge est sérialisé par l'API : {modeles}"
