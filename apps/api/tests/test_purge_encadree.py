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

**La section 4 est la plus importante.** Une première version faisait de la
DEMANDE l'autorisation : inscrire une purge ouvrait la porte, et une demande
abandonnée la laissait ouverte indéfiniment. La demande n'autorise plus rien ;
seule une fenêtre d'exécution ouverte et non expirée autorise, et c'est la base
qui en juge, avec sa propre horloge.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from metreo_api.config import get_settings
from metreo_api.db import get_session_factory
from metreo_api.models import (
    IssuedQuote,
    Organization,
    OrganizationPurge,
    QuoteRetentionDecision,
    utcnow,
)
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


def _decider(organization_id: str, annees: int) -> str:
    """Enregistre une décision de conservation complète, comme le ferait un humain.

    Les cinq éléments sont fictifs mais PRÉSENTS : le test éprouve la
    mécanique, pas le droit. Aucun d'eux n'a de valeur par défaut dans le
    service — c'est précisément ce qui empêche une opinion de passer pour une
    règle.
    """
    with get_session_factory()() as session:
        decision = conservation.decider(
            session,
            organization_id=organization_id,
            years=annees,
            jurisdiction="BE-WAL",
            source_label="Source fictive de recette — aucune valeur juridique",
            source_checked_on=date(2026, 1, 15),
            effective_from=date(2026, 1, 1),
        )
        session.commit()
        return str(decision.id)


def _vieillir(quote_id: str, annees: int) -> None:
    """Recule la date d'émission, pour éprouver un seuil sans attendre des ans."""
    with get_session_factory()() as session:
        devis = session.get(IssuedQuote, quote_id)
        assert devis is not None
        devis.issued_at = devis.issued_at - timedelta(days=int(annees * 365.25) + 1)
        session.commit()


def _purge_prete(organisation: str, devis: dict, annees: int = 7) -> str:
    """Une organisation dont la conservation est décidée ET échue."""
    _decider(organisation, annees)
    _vieillir(devis["id"], annees + 1)
    return organisation


def _autorisation_ouverte(purge_id: str) -> bool:
    """La condition SQL EXACTE que le déclencheur interroge.

    Interrogée telle quelle plutôt que réécrite en Python : une reformulation
    testerait ma compréhension de la règle, pas la règle.
    """
    with get_session_factory()() as session:
        maintenant = (
            "datetime('now')"
            if session.bind.dialect.name == "sqlite"
            else "(now() AT TIME ZONE 'UTC')"
        )
        compte = session.execute(
            text(
                "SELECT COUNT(*) FROM organization_purges "
                "WHERE id = :i AND status = 'executing' "
                f"AND authorized_until > {maintenant}"
            ),
            {"i": purge_id},
        ).scalar()
    return bool(compte)


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


def test_detruire_un_devis_sans_autorisation_est_refuse(
    seeded_client: TestClient, devis: dict
) -> None:
    """Le déclencheur ne demande plus « l'organisation existe-t-elle ».

    Il demande « une fenêtre d'exécution est-elle ouverte ». Sans elle, la base
    refuse.
    """
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError):
            session.execute(text("DELETE FROM issued_quotes WHERE id = :i"), {"i": devis["id"]})
            session.flush()
        session.rollback()


# --------------------------------------------------------------------------
# 2. Les refus, avant toute écriture
# --------------------------------------------------------------------------


def test_sans_decision_de_conservation_la_purge_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Une durée seule n'est pas une décision, et l'absence de décision refuse.

    C'est le cœur : une durée de conservation est une règle réglementaire, le
    dépôt n'en détient aucune de datée et sourcée, donc le code n'en invente
    pas. Le refus conserve.
    """
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(session, organization_id=organisation, reason_code="contract_ended")
    assert refus.value.code == "quote_retention_undecided"


def test_un_devis_encore_dans_sa_duree_retient_toute_l_organisation(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    _decider(organisation, 7)
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(session, organization_id=organisation, reason_code="contract_ended")
    assert refus.value.code == "quote_retention_not_elapsed"
    assert devis["number"] in refus.value.context["retained"]


def test_un_motif_hors_liste_est_refuse(seeded_client: TestClient, organisation: str) -> None:
    """La liste est fermée : ce qui n'y est pas n'entre pas."""
    _decider(organisation, 7)
    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(
            session, organization_id=organisation, reason_code="parce que le client a demandé"
        )
    assert refus.value.code == "purge_reason_unknown"


def test_une_reference_qui_ressemble_a_une_phrase_est_refusee(
    seeded_client: TestClient, organisation: str
) -> None:
    """Le format opaque est la barrière contre la donnée personnelle.

    Une référence désigne un dossier ailleurs ; elle ne le raconte pas ici. Un
    registre qui accepterait « demande de Jean Dupont » réintroduirait par
    écrit ce que la destruction sert à effacer.
    """
    _decider(organisation, 7)
    for candidate in (
        "demande de Jean Dupont",
        "Terrassements Untel, résiliation",
        "jean.dupont@exemple.be a demandé",
    ):
        with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
            conservation.demander(
                session,
                organization_id=organisation,
                reason_code="subject_request",
                reference=candidate,
            )
        assert refus.value.code == "purge_reference_not_opaque", candidate


def test_une_reference_opaque_est_acceptee(seeded_client: TestClient, organisation: str) -> None:
    _decider(organisation, 7)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason_code="subject_request",
            reference="DOSSIER-2026-014",
        )
        assert purge.reference == "DOSSIER-2026-014"
        session.rollback()


def test_un_refus_n_ecrit_rien_au_registre(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Des refus enchaînés ne laissent aucune ligne derrière eux."""
    with get_session_factory()() as session:
        for code in ("contract_ended", "motif_invente"):
            with pytest.raises(conservation.PurgeRefusee):
                conservation.demander(session, organization_id=organisation, reason_code=code)
        session.rollback()
    with get_session_factory()() as session:
        assert session.query(OrganizationPurge).count() == 0


# --------------------------------------------------------------------------
# 3. La décision de conservation est structurée, ou n'est pas
# --------------------------------------------------------------------------


def test_une_decision_porte_ses_cinq_elements(seeded_client: TestClient, organisation: str) -> None:
    identifiant = _decider(organisation, 7)
    with get_session_factory()() as session:
        decision = session.get(QuoteRetentionDecision, identifiant)
        assert decision is not None
        assert decision.years == 7
        assert decision.jurisdiction == "BE-WAL"
        assert decision.source_label
        assert decision.source_checked_on == date(2026, 1, 15)
        assert decision.effective_from == date(2026, 1, 1)
        assert decision.validated_at is not None


def test_une_decision_sans_juridiction_ni_source_est_refusee(
    seeded_client: TestClient, organisation: str
) -> None:
    with get_session_factory()() as session:
        for champs in ({"jurisdiction": "  "}, {"source_label": ""}):
            arguments = {
                "years": 7,
                "jurisdiction": "BE-WAL",
                "source_label": "un texte",
                "source_checked_on": date(2026, 1, 15),
                "effective_from": date(2026, 1, 1),
                **champs,
            }
            with pytest.raises(conservation.PurgeRefusee) as refus:
                conservation.decider(session, organization_id=organisation, **arguments)
            assert refus.value.code == "retention_decision_incomplete"


def test_une_decision_future_ne_s_applique_pas_encore(
    seeded_client: TestClient, organisation: str
) -> None:
    """Une décision datée de demain ne décide pas aujourd'hui."""
    with get_session_factory()() as session:
        conservation.decider(
            session,
            organization_id=organisation,
            years=7,
            jurisdiction="BE-WAL",
            source_label="Source fictive de recette",
            source_checked_on=date(2026, 1, 15),
            effective_from=utcnow().date() + timedelta(days=30),
        )
        session.commit()
    with get_session_factory()() as session:
        assert conservation.decision_active(session, organisation) is None


def test_corriger_une_decision_en_ajoute_une_et_garde_l_ancienne(
    seeded_client: TestClient, organisation: str
) -> None:
    """Versionnée, jamais modifiée en place — comme les packs régionaux.

    Une purge exécutée hier doit rester jugeable sur la règle qui l'autorisait,
    pas sur celle d'aujourd'hui.
    """
    premiere = _decider(organisation, 7)
    with get_session_factory()() as session:
        conservation.decider(
            session,
            organization_id=organisation,
            years=10,
            jurisdiction="BE-WAL",
            source_label="Source fictive révisée",
            source_checked_on=date(2026, 6, 1),
            effective_from=date(2026, 6, 1),
        )
        session.commit()
    with get_session_factory()() as session:
        assert session.query(QuoteRetentionDecision).count() == 2
        assert session.get(QuoteRetentionDecision, premiere) is not None
        active = conservation.decision_active(session, organisation)
        assert active is not None and active.years == 10


def test_aucune_decision_n_est_semee_par_le_depot(seeded_client: TestClient) -> None:
    """Le jeu de démonstration n'en crée aucune, et c'est le point.

    Une décision semée serait une durée que ce dépôt affirme sans la tenir
    d'une source. Une organisation neuve part donc de « non tranchée », et la
    destruction lui est refusée.
    """
    with get_session_factory()() as session:
        assert session.query(QuoteRetentionDecision).count() == 0


# --------------------------------------------------------------------------
# 4. L'autorisation est bornée — le correctif de fond
# --------------------------------------------------------------------------


def test_une_demande_seule_n_autorise_aucune_suppression(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Le défaut de la première version, retourné en garantie.

    Inscrire une purge ouvrait la porte. Une demande abandonnée la laissait
    donc ouverte indéfiniment : n'importe quelle suppression de devis de cette
    organisation passait ensuite, pour toujours. Elle n'ouvre plus rien.
    """
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        identifiant = purge.id
        session.commit()

    with get_session_factory()() as session:
        inscrite = session.get(OrganizationPurge, identifiant)
        assert inscrite is not None and inscrite.status == "requested"
        assert inscrite.authorized_until is None
    assert not _autorisation_ouverte(identifiant)

    # Et la base le confirme, sur le geste réel.
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError):
            session.execute(text("DELETE FROM issued_quotes WHERE id = :i"), {"i": devis["id"]})
            session.flush()
        session.rollback()

    with get_session_factory()() as session:
        assert session.get(IssuedQuote, devis["id"]) is not None


def test_executer_sans_autoriser_est_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Le service refuse aussi, avant même que la base ait à le faire."""
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        with pytest.raises(conservation.PurgeRefusee) as refus:
            conservation.executer(session, purge)
        assert refus.value.code == "purge_not_authorized"
        session.rollback()


def test_une_fenetre_expiree_n_autorise_plus_rien(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """La borne est vérifiée par la BASE, avec l'horloge de la base.

    Une fenêtre validée par l'appelant ne prouverait rien : il suffirait de
    mentir sur l'heure. Ici la fenêtre est reculée dans le passé, et la base
    referme d'elle-même, sans que personne ait à y penser.
    """
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        conservation.autoriser(session, purge)
        identifiant = purge.id
        session.commit()
    assert _autorisation_ouverte(identifiant), "la fenêtre venait d'être ouverte"

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        purge.authorized_until = utcnow() - timedelta(minutes=1)
        session.commit()

    assert not _autorisation_ouverte(identifiant)
    with get_session_factory()() as session:
        with pytest.raises(DatabaseError):
            session.execute(text("DELETE FROM issued_quotes WHERE id = :i"), {"i": devis["id"]})
            session.flush()
        session.rollback()
    with get_session_factory()() as session:
        assert session.get(IssuedQuote, devis["id"]) is not None


def test_une_purge_terminee_ou_echouee_n_autorise_plus_rien(
    seeded_client: TestClient, admin: dict[str, str], organisation: str
) -> None:
    """Ni `completed` ni `failed` ne figurent parmi les états qui ouvrent.

    Sans quoi une purge ancienne resterait une autorisation permanente sur une
    organisation recréée sous le même identifiant.
    """
    _decider(organisation, 1)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        identifiant = purge.id
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()
        assert purge.status == "completed"
    assert not _autorisation_ouverte(identifiant)

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        purge.status = "failed"
        purge.authorized_until = utcnow() + timedelta(hours=1)
        session.commit()
    assert not _autorisation_ouverte(identifiant), (
        "une purge en échec, même avec une borne future, ne doit rien autoriser"
    )


def test_reprendre_une_purge_jamais_autorisee_est_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Reprendre ne rouvre pas une fenêtre : il faut ré-autoriser."""
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        session.commit()
        identifiant = purge.id

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        with pytest.raises(conservation.PurgeRefusee) as refus:
            conservation.reprendre(session, purge, _stockage())
        assert refus.value.code == "purge_not_authorized"


def test_reprendre_une_fenetre_expiree_est_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Une reprise qui rouvrirait silencieusement une fenêtre annulerait la borne."""
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        conservation.autoriser(session, purge)
        purge.authorized_until = utcnow() - timedelta(minutes=1)
        identifiant = purge.id
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        with pytest.raises(conservation.PurgeRefusee) as refus:
            conservation.reprendre(session, purge, _stockage())
        assert refus.value.code == "purge_authorization_expired"


def test_autoriser_deux_fois_est_refuse(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Une fenêtre ne se prolonge pas en la rouvrant."""
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        conservation.autoriser(session, purge)
        with pytest.raises(conservation.PurgeRefusee) as refus:
            conservation.autoriser(session, purge)
        assert refus.value.code == "purge_not_pending"
        session.rollback()


# --------------------------------------------------------------------------
# 5. La purge elle-même
# --------------------------------------------------------------------------


def test_une_purge_echue_detruit_lignes_et_fichiers_et_laisse_son_registre(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Le parcours complet, et les faits mesurés à la fin.

    Ce test est l'inverse mesuré de la reproduction : la ligne part, le FICHIER
    part avec elle, et le registre — lui — reste.
    """
    _purge_prete(organisation, devis)

    with get_session_factory()() as session:
        quote = session.get(IssuedQuote, devis["id"])
        assert quote is not None
        cle = quote.pdf_storage_key
    fichier = _stockage().chemin(cle)
    assert fichier.exists(), "le PDF devrait exister avant la purge"

    with get_session_factory()() as session:
        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason_code="subject_request",
            reference="DOSSIER-2026-014",
        )
        identifiant = purge.id
        assert purge.quote_count == 1
        assert purge.documents[0]["sha256"] == devis["pdf_sha256"]
        assert purge.retention_decision_id is not None
        conservation.autoriser(session, purge)
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
        assert purge is not None and purge.status == "rows_deleted"
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    assert not fichier.exists(), "le PDF est resté sur le volume après la purge"
    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        assert purge.status == "completed"
        assert purge.files_deleted == 1
        assert purge.files_failed == []
        assert conservation.orphelins(session, _stockage()) == []


def test_le_registre_survit_a_l_organisation_qu_il_decrit(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """La raison d'être de la table, et de son absence de clé étrangère.

    Le journal d'audit porte un `organization_id` en CASCADE : il meurt avec
    l'organisation. Un registre qui ferait pareil ne prouverait rien.
    """
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        identifiant = purge.id
        conservation.autoriser(session, purge)
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
        decisions = session.query(QuoteRetentionDecision).count()
        assert decisions == 0, "la décision meurt avec l'organisation, elle en est la config"

        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None, "le registre a disparu avec ce qu'il enregistrait"
        assert purge.organization_id == organisation
        assert purge.quote_count == 1
        # La durée est RECOPIÉE : la décision qui l'a fondée vient de mourir,
        # et la purge doit rester jugeable après.
        assert purge.retention_years_applied == 7


def test_le_registre_ne_conserve_aucun_nom(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Prouver une destruction sans réintroduire ce qu'elle a effacé.

    Le registre porte des identifiants techniques, un code de motif, une
    référence opaque, des empreintes et des chemins de stockage — qui ne sont
    eux-mêmes que des identifiants. Aucun nom n'y entre, et aucune zone n'y
    permet d'en écrire un.
    """
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        avant = session.get(Organization, organisation)
        assert avant is not None
        noms = {avant.name, avant.legal_name or avant.name}
        quote = session.get(IssuedQuote, devis["id"])
        assert quote is not None
        noms.add(str(quote.client_snapshot.get("name", "")))

        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason_code="subject_request",
            reference="DOSSIER-2026-014",
        )
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        session.commit()
        identifiant = purge.id

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        serialise = str(
            [
                purge.organization_id,
                purge.reason_code,
                purge.reference,
                purge.documents,
                purge.files_failed,
            ]
        )
    for nom in noms:
        if nom:
            assert nom not in serialise, f"le registre a conservé « {nom} »"


# --------------------------------------------------------------------------
# 6. Une purge interrompue se termine, elle ne laisse pas d'orphelin
# --------------------------------------------------------------------------


def test_une_purge_interrompue_se_reprend_dans_sa_fenetre(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Ce qui justifie l'ordre choisi : lignes d'abord, fichiers ensuite.

    L'inverse laisserait des lignes désignant des fichiers absents — un devis
    qui existe et ne se télécharge plus. Ici l'interruption laisse au pire un
    fichier que le registre NOMME, donc que la reprise retrouve.
    """
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        quote = session.get(IssuedQuote, devis["id"])
        assert quote is not None
        cle = quote.pdf_storage_key
    fichier = _stockage().chemin(cle)

    # On s'arrête volontairement après les lignes : c'est la panne à couvrir.
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        identifiant = purge.id
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        session.commit()

    assert fichier.exists()
    with get_session_factory()() as session:
        assert conservation.orphelins(session, _stockage()) == [cle]

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        conservation.reprendre(session, purge, _stockage())
        session.commit()

    assert not fichier.exists()
    with get_session_factory()() as session:
        achevee = session.get(OrganizationPurge, identifiant)
        assert achevee is not None and achevee.status == "completed"
        assert conservation.orphelins(session, _stockage()) == []


def test_reprendre_une_purge_terminee_ne_fait_rien(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """Idempotence : rejouer la reprise ne casse rien et ne recompte pas."""
    _purge_prete(organisation, devis)
    with get_session_factory()() as session:
        purge = conservation.demander(
            session, organization_id=organisation, reason_code="contract_ended"
        )
        identifiant = purge.id
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        conservation.reprendre(session, purge, _stockage())
        conservation.reprendre(session, purge, _stockage())
        session.commit()

    with get_session_factory()() as session:
        achevee = session.get(OrganizationPurge, identifiant)
        assert achevee is not None
        assert achevee.status == "completed"
        assert achevee.files_deleted == 1


# --------------------------------------------------------------------------
# 7. Le seuil, et la frontière d'API
# --------------------------------------------------------------------------


def test_le_seuil_de_conservation_se_compte_a_la_date(
    seeded_client: TestClient, devis: dict, organisation: str
) -> None:
    """La veille retient, le jour même libère. Sans tâche planifiée."""
    _decider(organisation, 7)
    with get_session_factory()() as session:
        quote = session.get(IssuedQuote, devis["id"])
        assert quote is not None
        emis = quote.issued_at
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


def test_la_purge_n_est_exposee_par_aucune_route(client: TestClient) -> None:
    """Détruire un locataire entier ne s'expose pas en HTTP, et se vérifie.

    Ni la purge ni la décision de conservation ne passent par l'API : l'une
    détruit un locataire entier, l'autre engage l'entreprise sur un droit. Les
    deux se font par `scripts/purger_organisation.py`, qui montre ce qu'il va
    faire avant de le faire. Ce test tient la frontière — il rougira le jour où
    quelqu'un l'ouvrira sans avoir tranché.
    """
    schema = client.get("/openapi.json").json()
    chemins = [c for c in schema["paths"] if "purge" in c.lower() or "retention" in c.lower()]
    assert chemins == [], f"la purge est exposée : {chemins}"

    modeles = [
        nom
        for nom in schema.get("components", {}).get("schemas", {})
        if "urge" in nom or "etention" in nom
    ]
    assert modeles == [], f"la purge est sérialisée par l'API : {modeles}"


def test_la_purge_emporte_aussi_les_fichiers_de_logo(
    seeded_client: TestClient, organisation: str
) -> None:
    """Le volume ne doit RIEN garder de l'organisation détruite.

    Un devis émis pose deux fichiers quand l'entreprise a un logo : le PDF et
    la copie figée de son logo. L'organisation elle-même en porte un troisième,
    son logo courant. Le registre n'inscrivait que les PDF.

    C'était le pire état possible pour ce module : `executer` supprime la ligne
    `organizations`, donc `logo_storage_key` part avec elle, et les fichiers
    survivaient sans qu'AUCUNE ligne ne les désigne — ni l'écrit qui doit dire
    ce qui a été détruit. Ce test recense TOUT ce qui reste sous la racine,
    pas seulement les `*.pdf` : c'est ce qui l'aurait fait rougir.
    """
    from .images_fictives import carre

    entetes = login(seeded_client, "admin@dubois.demo")
    charge = seeded_client.put(
        "/api/v1/organization/logo",
        headers=entetes,
        files={"file": ("logo.png", carre(), "image/png")},
    )
    assert charge.status_code == 200, charge.text
    devis = graphe_complet(seeded_client, entetes, "PURGE-LOGO")
    assert devis["pdf_sha256"]

    racine = _stockage().racine
    avant = sorted(p for p in racine.rglob("*") if p.is_file())
    # Le PDF, la copie du logo, et le logo courant.
    assert len(avant) >= 3, [p.name for p in avant]

    with get_session_factory()() as session:
        # `sans_retention` : la porte qu'emprunte `seed --reset`. La durée de
        # conservation n'est pas le sujet de ce test — ce qui reste sur le
        # volume l'est.
        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason_code="test_fixture",
            reference="DOSSIER-2026-020",
            sans_retention=True,
        )
        identifiant = purge.id
        # L'écrit nomme les trois fichiers, pas seulement le PDF.
        cles = {d["storage_key"] for d in purge.documents}
        assert len(cles) >= 3, purge.documents
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()

    restants = sorted(p for p in racine.rglob("*") if p.is_file())
    assert restants == [], f"la purge a laissé {len(restants)} fichier(s) : {restants}"
    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        assert purge.status == "completed"
        assert purge.files_failed == []
        assert conservation.orphelins(session, _stockage()) == []
