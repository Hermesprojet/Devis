"""La réponse du client, et l'état commercial qu'elle produit.

L'état d'un devis n'est stocké nulle part : il se déduit du journal et de la
date du jour. Ces tests éprouvent la déduction ET les règles qui la protègent —
une consultation ne fait pas régresser une décision, une décision ne se
contredit pas, une correction n'efface rien, et un devis périmé ne s'accepte
plus tout en restant lisible.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from metreo_api.db import get_session_factory
from metreo_api.models import IssuedQuote, QuoteEvent
from metreo_api.services import cycle_devis

from .conftest import login
from .emission import emettre, fiche, geler, prix_manquant, rattacher


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def devis(seeded_client: TestClient, admin) -> dict:
    estimation = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimation['id']}/versions", headers=admin
    ).json()[0]
    prix_manquant(seeded_client, admin, estimation)
    rattacher(seeded_client, admin, estimation["project_id"], fiche(seeded_client, admin)["id"])
    geler(seeded_client, admin, estimation, version)
    reponse = emettre(seeded_client, admin, estimation, version)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _lien(client: TestClient, admin: dict[str, str], devis: dict) -> str:
    reponse = client.post(
        f"/api/v1/issued-quotes/{devis['id']}/share-links", headers=admin, json={}
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["url"].split("#", 1)[1]


def _ouvrir(client: TestClient, secret: str) -> None:
    assert client.post("/api/v1/public/quote-sessions", json={"secret": secret}).status_code == 204


def _repondre(client: TestClient, **corps):
    return client.post("/api/v1/public/quote/response", json={"confirmed": True, **corps})


def _etat(client: TestClient, admin: dict[str, str], devis: dict) -> dict:
    fiche_devis = client.get(f"/api/v1/issued-quotes/{devis['id']}", headers=admin)
    assert fiche_devis.status_code == 200, fiche_devis.text
    return fiche_devis.json()


def _perimer(devis: dict, *, jours: int = 1) -> None:
    """Recule la validité dans le passé, comme le temps l'aurait fait."""
    with get_session_factory()() as session:
        ligne = session.get(IssuedQuote, devis["id"])
        assert ligne is not None
        ligne.valid_until = date.today() - timedelta(days=jours)
        session.commit()


# --------------------------------------------------------------------------
# L'échelle des états
# --------------------------------------------------------------------------


def test_un_devis_neuf_est_emis_et_rien_de_plus(seeded_client: TestClient, admin, devis) -> None:
    etat = _etat(seeded_client, admin, devis)["state"]
    assert etat["code"] == "issued"
    assert etat["label"] == "Émis"
    assert etat["decision"] is None


def test_creer_un_lien_ne_transmet_pas(seeded_client: TestClient, admin, devis) -> None:
    """Préparer l'envoi n'est pas envoyer. L'entreprise seule sait si elle l'a fait."""
    _lien(seeded_client, admin, devis)
    fiche_devis = _etat(seeded_client, admin, devis)
    assert fiche_devis["state"]["code"] == "issued"
    assert [e["kind"] for e in fiche_devis["events"]] == ["link_created"]


def test_marquer_transmis_puis_consulter_fait_monter_l_etat(
    seeded_client: TestClient, admin, devis
) -> None:
    secret = _lien(seeded_client, admin, devis)
    transmis = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email", "comment": "Envoyé à travaux@…"},
    )
    assert transmis.status_code == 201, transmis.text
    assert transmis.json()["state"]["code"] == "transmitted"

    _ouvrir(seeded_client, secret)
    assert seeded_client.get("/api/v1/public/quote").status_code == 200
    assert _etat(seeded_client, admin, devis)["state"]["code"] == "viewed"


def test_rafraichir_la_page_n_ajoute_pas_de_consultation(
    seeded_client: TestClient, admin, devis
) -> None:
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))
    for _ in range(4):
        assert seeded_client.get("/api/v1/public/quote").status_code == 200
    consultations = [
        e for e in _etat(seeded_client, admin, devis)["events"] if e["kind"] == "viewed"
    ]
    assert len(consultations) == 1


def test_une_consultation_ne_fait_pas_regresser_un_devis_accepte(
    seeded_client: TestClient, admin, devis
) -> None:
    """Le cas qui rendrait l'écran menteur : accepté hier, consulté aujourd'hui."""
    secret = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, secret)
    assert (
        _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont").status_code
        == 200
    )
    assert seeded_client.get("/api/v1/public/quote").status_code == 200
    assert _etat(seeded_client, admin, devis)["state"]["code"] == "accepted"


def test_l_expiration_se_deduit_de_la_date_sans_tache_planifiee(
    seeded_client: TestClient, admin, devis
) -> None:
    assert _etat(seeded_client, admin, devis)["state"]["code"] == "issued"
    _perimer(devis)
    #: Aucun traitement n'a tourné entre les deux lectures : c'est la date du
    #: jour qui a changé de côté, et l'état s'en déduit à la lecture.
    etat = _etat(seeded_client, admin, devis)["state"]
    assert etat["code"] == "expired"
    assert etat["expired"] is True


def test_un_devis_valide_jusqu_a_aujourd_hui_n_est_pas_encore_expire(
    seeded_client: TestClient, admin, devis
) -> None:
    """La borne est inclusive : le dernier jour compte encore."""
    with get_session_factory()() as session:
        ligne = session.get(IssuedQuote, devis["id"])
        assert ligne is not None
        ligne.valid_until = date.today()
        session.commit()
    assert _etat(seeded_client, admin, devis)["state"]["code"] == "issued"


def test_un_devis_accepte_puis_perime_reste_accepte(
    seeded_client: TestClient, admin, devis
) -> None:
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))
    _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont")
    _perimer(devis)
    etat = _etat(seeded_client, admin, devis)["state"]
    assert etat["code"] == "accepted"
    assert etat["expired"] is True


# --------------------------------------------------------------------------
# Idempotence et conflit
# --------------------------------------------------------------------------


def test_rejouer_la_meme_acceptation_rend_le_meme_recu(
    seeded_client: TestClient, admin, devis
) -> None:
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))
    premier = _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont")
    assert premier.status_code == 200, premier.text
    assert premier.json()["created"] is True

    second = _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont")
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["decided_at"] == premier.json()["decided_at"]
    assert second.json()["pdf_sha256"] == premier.json()["pdf_sha256"]

    decisions = [e for e in _etat(seeded_client, admin, devis)["events"] if e["kind"] == "accepted"]
    assert len(decisions) == 1, "une seconde acceptation a été écrite"


def test_une_decision_opposee_est_refusee_en_409(seeded_client: TestClient, admin, devis) -> None:
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))
    assert (
        _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont").status_code
        == 200
    )

    contraire = _repondre(seeded_client, decision="declined", comment="Finalement non")
    assert contraire.status_code == 409, contraire.text
    assert contraire.json()["detail"]["code"] == "quote_already_answered"
    assert _etat(seeded_client, admin, devis)["state"]["code"] == "accepted"


def test_un_devis_perime_reste_consultable_mais_ne_s_accepte_plus(
    seeded_client: TestClient, admin, devis
) -> None:
    secret = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, secret)
    _perimer(devis)

    vue = seeded_client.get("/api/v1/public/quote")
    assert vue.status_code == 200, "un devis périmé doit rester lisible"
    assert vue.json()["can_respond"] is False
    assert "valable" in vue.json()["cannot_respond_reason"]

    refus = _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont")
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "quote_expired"


def test_accepter_exige_un_nom_et_une_confirmation(seeded_client: TestClient, admin, devis) -> None:
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))

    sans_confirmation = seeded_client.post(
        "/api/v1/public/quote/response",
        json={"decision": "accepted", "respondent_name": "Marie", "confirmed": False},
    )
    assert sans_confirmation.status_code == 422
    assert sans_confirmation.json()["detail"]["code"] == "confirmation_required"

    sans_nom = _repondre(seeded_client, decision="accepted")
    assert sans_nom.status_code == 422
    assert sans_nom.json()["detail"]["code"] == "respondent_required"

    #: Refuser, en revanche, n'oblige personne à se nommer.
    refus = _repondre(seeded_client, decision="declined", comment="Budget insuffisant")
    assert refus.status_code == 200, refus.text


# --------------------------------------------------------------------------
# Le parcours hors ligne
# --------------------------------------------------------------------------


def test_une_reponse_hors_ligne_exige_une_note_et_historise_l_acteur(
    seeded_client: TestClient, admin, devis
) -> None:
    """Sans portail, l'entreprise envoie elle-même et note ce qu'on lui répond."""
    sans_note = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "accepted", "channel": "phone", "respondent_name": "Marie Dupont"},
    )
    assert sans_note.status_code == 422, sans_note.text
    assert sans_note.json()["detail"]["code"] == "offline_note_required"

    avec_note = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={
            "kind": "accepted",
            "channel": "phone",
            "respondent_name": "Marie Dupont",
            "comment": "Appel du 12/03, accord verbal confirmé par courriel.",
        },
    )
    assert avec_note.status_code == 201, avec_note.text
    fiche_devis = avec_note.json()
    assert fiche_devis["state"]["code"] == "accepted"
    decision = next(e for e in fiche_devis["events"] if e["kind"] == "accepted")
    assert decision["actor_email"] == "admin@dubois.demo"
    assert decision["channel"] == "phone"
    assert decision["respondent_name"] == "Marie Dupont"


def test_une_decision_hors_ligne_et_une_reponse_publique_ne_se_contredisent_pas(
    seeded_client: TestClient, admin, devis
) -> None:
    secret = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, secret)
    seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={
            "kind": "declined",
            "channel": "phone",
            "comment": "Refus annoncé au téléphone le 12/03.",
        },
    )
    contraire = _repondre(seeded_client, decision="accepted", respondent_name="Marie Dupont")
    assert contraire.status_code == 409, contraire.text
    assert contraire.json()["detail"]["code"] == "quote_already_answered"


# --------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------


def test_la_base_refuse_de_modifier_ou_d_effacer_un_evenement(
    seeded_client: TestClient, admin, devis
) -> None:
    """La promesse est tenue par un déclencheur, pas par la bonne conduite."""
    seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email"},
    )
    with get_session_factory()() as session:
        evenement = session.scalars(select(QuoteEvent)).first()
        assert evenement is not None
        identifiant = evenement.id

    for sql in (
        "UPDATE quote_events SET comment = 'réécrit' WHERE id = :i",
        "DELETE FROM quote_events WHERE id = :i",
    ):
        with get_session_factory()() as session:
            with pytest.raises(Exception) as capture:
                session.execute(text(sql), {"i": identifiant})
                session.commit()
            session.rollback()
            assert "quote_event_append_only" in str(capture.value)


def test_une_correction_barre_l_evenement_sans_l_effacer(
    seeded_client: TestClient, admin, devis
) -> None:
    pose = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email"},
    )
    assert pose.status_code == 201, pose.text
    original = next(e for e in pose.json()["events"] if e["kind"] == "transmitted")

    corrige = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events/{original['id']}/correction",
        headers=admin,
        json={"reason": "Envoyé par téléphone, pas par courriel."},
    )
    assert corrige.status_code == 201, corrige.text
    fiche_devis = corrige.json()

    #: L'original est TOUJOURS là, et il porte désormais son motif de correction.
    barre = next(e for e in fiche_devis["events"] if e["id"] == original["id"])
    assert barre["corrected"] is True
    assert "téléphone" in barre["correction_reason"]
    #: Et l'état retombe : la transmission ne compte plus.
    assert fiche_devis["state"]["code"] == "issued"


def test_une_correction_sans_motif_est_refusee(seeded_client: TestClient, admin, devis) -> None:
    pose = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email"},
    )
    original = next(e for e in pose.json()["events"] if e["kind"] == "transmitted")
    refus = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events/{original['id']}/correction",
        headers=admin,
        json={"reason": "   "},
    )
    assert refus.status_code == 422, refus.text


def test_un_evenement_ne_se_corrige_pas_deux_fois(seeded_client: TestClient, admin, devis) -> None:
    pose = seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email"},
    )
    original = next(e for e in pose.json()["events"] if e["kind"] == "transmitted")
    url = f"/api/v1/issued-quotes/{devis['id']}/events/{original['id']}/correction"
    assert (
        seeded_client.post(url, headers=admin, json={"reason": "Canal erroné"}).status_code == 201
    )
    encore = seeded_client.post(url, headers=admin, json={"reason": "Encore"})
    assert encore.status_code == 409, encore.text
    assert encore.json()["detail"]["code"] == "already_corrected"


# --------------------------------------------------------------------------
# Contenu libre
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "charge",
    [
        "<script>alert('xss')</script>",
        '"><img src=x onerror=alert(1)>',
        "'; DROP TABLE quote_events; --",
    ],
)
def test_le_contenu_libre_est_rendu_tel_quel_et_jamais_interprete(
    seeded_client: TestClient, admin, devis, charge: str
) -> None:
    """Ni exécuté, ni échappé deux fois, ni tronqué : conservé et rendu en JSON.

    L'API ne rend que du JSON, où une balise n'est qu'une chaîne. Le contrôle
    porte donc sur ce qui compte ici : la valeur revient INTACTE, la table
    existe toujours, et rien n'a été interprété au passage.
    """
    _ouvrir(seeded_client, _lien(seeded_client, admin, devis))
    reponse = _repondre(seeded_client, decision="accepted", respondent_name=charge, comment=charge)
    assert reponse.status_code == 200, reponse.text

    fiche_devis = _etat(seeded_client, admin, devis)
    decision = next(e for e in fiche_devis["events"] if e["kind"] == "accepted")
    assert decision["respondent_name"] == charge
    assert decision["comment"] == charge

    with get_session_factory()() as session:
        assert session.scalars(select(QuoteEvent)).first() is not None


# --------------------------------------------------------------------------
# L'état recalculé
# --------------------------------------------------------------------------


def test_l_etat_rendu_par_l_api_est_celui_que_le_journal_produit(
    seeded_client: TestClient, admin, devis
) -> None:
    """Une seule vérité : l'API ne fait que relire la fonction pure."""
    seeded_client.post(
        f"/api/v1/issued-quotes/{devis['id']}/events",
        headers=admin,
        json={"kind": "transmitted", "channel": "email"},
    )
    rendu = _etat(seeded_client, admin, devis)["state"]

    with get_session_factory()() as session:
        ligne = session.get(IssuedQuote, devis["id"])
        assert ligne is not None
        calcule = cycle_devis.etat(
            ligne, cycle_devis.journal(session, ligne), aujourdhui=date.today()
        )
    assert rendu["code"] == calcule.code
    assert rendu["label"] == calcule.label
