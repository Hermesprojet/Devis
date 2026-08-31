"""Le lien de consultation : ce qu'il permet, et surtout ce qu'il ne permet pas.

Le secret d'un lien ouvre un document commercial à qui n'a pas de compte. Ce
fichier éprouve les six choses qui rendent cela acceptable :

* il est assez long pour n'être pas devinable, et la base n'en garde que
  l'empreinte — une copie de la base n'ouvre aucun devis ;
* il n'apparaît nulle part ailleurs : ni journal, ni audit, ni message
  d'erreur, ni chemin, ni chaîne de requête ;
* un secret faux, périmé ou révoqué ne distingue pas son refus des autres ;
* la révocation ferme immédiatement les sessions DÉJÀ ouvertes ;
* un devis portant les coûts internes ne se partage pas du tout ;
* la vue publique ne rend jamais de coût interne, sous aucune forme.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from metreo_api.db import get_session_factory
from metreo_api.models import QuotePublicSession, QuoteShareLink, utcnow
from metreo_api.services import partage

from .conftest import login
from .emission import emettre, fiche, geler, prix_manquant, rattacher

TERMES_INTERNES = ("Déboursé", "déboursé", "Revient", "revient", "Marge", "marge")


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _emettre(client: TestClient, admin: dict[str, str], **corps) -> dict:
    estimation = client.get("/api/v1/estimates", headers=admin).json()[0]
    version = client.get(f"/api/v1/estimates/{estimation['id']}/versions", headers=admin).json()[0]
    prix_manquant(client, admin, estimation)
    rattacher(client, admin, estimation["project_id"], fiche(client, admin)["id"])
    geler(client, admin, estimation, version)
    reponse = emettre(client, admin, estimation, version, **corps)
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


@pytest.fixture()
def devis(seeded_client: TestClient, admin) -> dict:
    return _emettre(seeded_client, admin)


def _lien(client: TestClient, admin: dict[str, str], devis: dict, **corps) -> dict:
    reponse = client.post(
        f"/api/v1/issued-quotes/{devis['id']}/share-links", headers=admin, json=corps
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _secret(url: str) -> str:
    return url.split("#", 1)[1]


def _ouvrir(client: TestClient, secret: str):
    return client.post("/api/v1/public/quote-sessions", json={"secret": secret})


# --------------------------------------------------------------------------
# Le secret
# --------------------------------------------------------------------------


def test_le_secret_porte_au_moins_256_bits(seeded_client: TestClient, admin, devis) -> None:
    """Devinable ou non : c'est une question d'entropie, pas d'opinion."""
    secret = _secret(_lien(seeded_client, admin, devis)["url"])
    # `token_urlsafe` rend du base64url sans remplissage : on le complète pour
    # mesurer les octets réellement tirés.
    octets = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    assert len(octets) * 8 >= 256, f"{len(octets) * 8} bits seulement"


def test_la_base_ne_contient_que_l_empreinte_du_secret(
    seeded_client: TestClient, admin, devis
) -> None:
    """Une sauvegarde, un export ou une fuite n'ouvrent aucun devis."""
    secret = _secret(_lien(seeded_client, admin, devis)["url"])
    with get_session_factory()() as session:
        lignes = list(session.scalars(select(QuoteShareLink)).all())
        assert len(lignes) == 1
        assert lignes[0].secret_sha256 == hashlib.sha256(secret.encode()).hexdigest()
        # Et le secret n'est nulle part ailleurs dans la table.
        assert secret not in json.dumps(
            {c.name: str(getattr(lignes[0], c.name)) for c in QuoteShareLink.__table__.columns}
        )

    _ouvrir(seeded_client, secret)
    with get_session_factory()() as session:
        sessions = list(session.scalars(select(QuotePublicSession)).all())
        assert len(sessions) == 1
        assert len(sessions[0].token_sha256) == 64
        assert secret not in sessions[0].token_sha256


def test_le_secret_n_apparait_ni_dans_l_audit_ni_dans_les_journaux(
    seeded_client: TestClient, admin, devis, caplog: pytest.LogCaptureFixture
) -> None:
    """Un audit s'exporte, un journal se sauvegarde : ni l'un ni l'autre ne doit le porter."""
    with caplog.at_level(logging.DEBUG):
        cree = _lien(seeded_client, admin, devis)
        secret = _secret(cree["url"])
        _ouvrir(seeded_client, secret)
        seeded_client.post("/api/v1/public/quote-sessions", json={"secret": "faux-secret"})

    evenements = seeded_client.get("/api/v1/audit/events?limit=200", headers=admin).json()
    assert secret not in json.dumps(evenements), "le secret a fui dans le journal d'audit"

    journal = "\n".join(enregistrement.getMessage() for enregistrement in caplog.records)
    assert secret not in journal, "le secret a fui dans les journaux applicatifs"

    with get_session_factory()() as session:
        for table in ("audit_events", "quote_events"):
            lignes = session.execute(text(f"SELECT * FROM {table}")).mappings().all()
            assert secret not in json.dumps([dict(ligne) for ligne in lignes], default=str)


def test_le_secret_ne_passe_ni_par_le_chemin_ni_par_la_requete(
    seeded_client: TestClient, admin, devis
) -> None:
    """Il vit dans le FRAGMENT, que le navigateur n'envoie jamais au serveur."""
    url = _lien(seeded_client, admin, devis)["url"]
    avant_fragment, _, fragment = url.partition("#")
    assert fragment, "l'URL ne porte pas de fragment"
    assert "?" not in avant_fragment, "un secret dans la chaîne de requête"
    assert fragment not in avant_fragment


def test_un_secret_faux_perime_ou_revoque_recoit_le_meme_refus(
    seeded_client: TestClient, admin, devis
) -> None:
    """Distinguer les refus dirait à qui essaie s'il a trouvé un lien réel."""
    cree = _lien(seeded_client, admin, devis)
    secret = _secret(cree["url"])

    inconnu = _ouvrir(seeded_client, "un-secret-qui-n-a-jamais-existe")
    assert inconnu.status_code == 404, inconnu.text
    refus_inconnu = inconnu.json()["detail"]

    revoque = seeded_client.delete(
        f"/api/v1/issued-quotes/{devis['id']}/share-links/{cree['link']['id']}", headers=admin
    )
    assert revoque.status_code == 204, revoque.text
    apres = _ouvrir(seeded_client, secret)
    assert apres.status_code == 404
    assert apres.json()["detail"]["code"] == refus_inconnu["code"]
    assert apres.json()["detail"]["message"] == refus_inconnu["message"]


def test_un_lien_perime_n_ouvre_plus_rien(seeded_client: TestClient, admin, devis) -> None:
    cree = _lien(seeded_client, admin, devis)
    secret = _secret(cree["url"])
    with get_session_factory()() as session:
        lien = session.get(QuoteShareLink, cree["link"]["id"])
        assert lien is not None
        lien.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    assert _ouvrir(seeded_client, secret).status_code == 404


def test_l_expiration_du_lien_ne_depasse_jamais_la_validite_du_devis(
    seeded_client: TestClient, admin, devis
) -> None:
    """Un document périmé ne se consulte pas plus longtemps que promis."""
    cree = _lien(seeded_client, admin, devis, days=365)
    fin = cree["link"]["expires_at"][:10]
    assert fin <= devis["valid_until"], f"{fin} > {devis['valid_until']}"


# --------------------------------------------------------------------------
# La révocation
# --------------------------------------------------------------------------


def test_revoquer_ferme_une_session_deja_ouverte(seeded_client: TestClient, admin, devis) -> None:
    """C'est le cas dangereux : le destinataire a DÉJÀ la page sous les yeux."""
    cree = _lien(seeded_client, admin, devis)
    assert _ouvrir(seeded_client, _secret(cree["url"])).status_code == 204
    assert seeded_client.get("/api/v1/public/quote").status_code == 200

    revoque = seeded_client.delete(
        f"/api/v1/issued-quotes/{devis['id']}/share-links/{cree['link']['id']}", headers=admin
    )
    assert revoque.status_code == 204, revoque.text

    ferme = seeded_client.get("/api/v1/public/quote")
    assert ferme.status_code == 401, ferme.text
    assert ferme.json()["detail"]["code"] == "link_unusable"
    assert seeded_client.get("/api/v1/public/quote/document.pdf").status_code == 401


def test_creer_un_nouveau_lien_revoque_le_precedent(
    seeded_client: TestClient, admin, devis
) -> None:
    premier = _lien(seeded_client, admin, devis)
    second = _lien(seeded_client, admin, devis)
    assert premier["link"]["id"] != second["link"]["id"]
    assert _secret(premier["url"]) != _secret(second["url"])

    assert _ouvrir(seeded_client, _secret(premier["url"])).status_code == 404
    assert _ouvrir(seeded_client, _secret(second["url"])).status_code == 204


# --------------------------------------------------------------------------
# Les coûts internes
# --------------------------------------------------------------------------


def test_un_devis_portant_les_couts_internes_ne_se_partage_pas(
    seeded_client: TestClient, admin
) -> None:
    """Le PDF remis EST le document : le partager enverrait la structure de prix."""
    interne = _emettre(seeded_client, admin, include_internal_costs=True)
    refus = seeded_client.post(
        f"/api/v1/issued-quotes/{interne['id']}/share-links", headers=admin, json={}
    )
    assert refus.status_code == 409, refus.text
    assert refus.json()["detail"]["code"] == "internal_costs_not_shareable"

    with get_session_factory()() as session:
        assert session.scalars(select(QuoteShareLink)).first() is None


def test_aucun_cout_interne_dans_la_vue_publique(seeded_client: TestClient, admin, devis) -> None:
    cree = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, _secret(cree["url"]))
    vue = seeded_client.get("/api/v1/public/quote")
    assert vue.status_code == 200, vue.text
    corps = vue.text
    for interdit in TERMES_INTERNES:
        assert interdit not in corps, f"« {interdit} » a fui dans la vue publique"
    for interdit in ("direct_cost", "cost_price", "margin"):
        assert interdit not in corps


# --------------------------------------------------------------------------
# Les en-têtes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chemin", ["/api/v1/public/quote", "/api/v1/public/quote/document.pdf"])
def test_les_reponses_publiques_portent_les_en_tetes_attendus(
    seeded_client: TestClient, admin, devis, chemin: str
) -> None:
    cree = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, _secret(cree["url"]))
    entetes = seeded_client.get(chemin).headers

    assert entetes["cache-control"] == "no-store"
    assert entetes["referrer-policy"] == "no-referrer"
    assert entetes["x-content-type-options"] == "nosniff"
    assert entetes["x-frame-options"] == "DENY"
    politique = entetes["content-security-policy"]
    assert "default-src 'none'" in politique
    assert "frame-ancestors 'none'" in politique


def test_le_cookie_de_session_est_httponly_et_samesite(
    seeded_client: TestClient, admin, devis
) -> None:
    cree = _lien(seeded_client, admin, devis)
    reponse = _ouvrir(seeded_client, _secret(cree["url"]))
    pose = reponse.headers["set-cookie"]
    assert partage.COOKIE in pose
    assert "HttpOnly" in pose
    assert "SameSite=lax" in pose.replace("samesite", "SameSite")
    assert "Path=/api/v1/public" in pose


# --------------------------------------------------------------------------
# Le document
# --------------------------------------------------------------------------


def test_le_pdf_public_est_identique_octet_pour_octet_a_celui_de_l_entreprise(
    seeded_client: TestClient, admin, devis
) -> None:
    """Le destinataire et l'émetteur regardent le même fichier, au bit près."""
    interne = seeded_client.get(
        f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
    ).content

    cree = _lien(seeded_client, admin, devis)
    _ouvrir(seeded_client, _secret(cree["url"]))
    public = seeded_client.get("/api/v1/public/quote/document.pdf")

    assert public.status_code == 200, public.text
    assert public.content == interne
    assert hashlib.sha256(public.content).hexdigest() == devis["pdf_sha256"]
    assert public.headers["x-quote-sha256"] == devis["pdf_sha256"]
    assert public.headers["content-disposition"].startswith("attachment;")
