"""Le journal d'audit respecte `margin:read`, comme l'écran des réglages.

La fuite, reproduite sur `main` avant correction : un `VIEWER` — qui porte
`AUDIT_READ` mais **pas** `MARGIN_READ` — lisait correctement
`margin_rate: null` sur `/organization/settings`, puis récupérait la valeur
exacte dans `/audit/events` :

    {"before": {"margin_rate": "0.08"}, "after": {"margin_rate": "0.17"}}

Le masque d'un écran ne vaut rien si un autre écran donne la valeur. Le modèle
de menaces classe pourtant les marges parmi « la fuite la plus coûteuse
commercialement ».

Ce qui est masqué l'est **à la lecture seulement**. Le payload stocké et son
empreinte ne bougent pas : `/audit/verify` recalcule la chaîne depuis la base et
reste valide. Sans cela, corriger une fuite de confidentialité aurait cassé
l'intégrité du journal — on aurait échangé un défaut contre un autre.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.schemas import CHAMPS_COMMERCIAUX_SENSIBLES
from metreo_api.security.roles import ROLE_PERMISSIONS, Permission, Role

from .conftest import login

#: Modifiés ensemble : deux champs commerciaux et un champ qui ne l'est pas.
#: Le troisième est le contrôle — masquer tout le payload le ferait disparaître
#: et rendrait le journal inutilisable pour un auditeur.
MODIFICATION = {
    "margin_rate": "0.17",
    "general_overheads_rate": "0.11",
    "rounding_scale": 3,
}

ACTION = "organization.settings.updated"


def _evenement_de_reglages(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    reponse = client.get(f"/api/v1/audit/events?action={ACTION}", headers=headers)
    assert reponse.status_code == 200, reponse.text
    items = reponse.json()["items"]
    assert items, "aucun événement de modification des réglages dans le journal"
    return items[0]


@pytest.fixture()
def journal_apres_modification(seeded_client: TestClient) -> TestClient:
    """Un administrateur modifie la politique commerciale ; le journal l'enregistre."""
    admin = login(seeded_client, "admin@dubois.demo")
    reponse = seeded_client.patch("/api/v1/organization/settings", headers=admin, json=MODIFICATION)
    assert reponse.status_code == 200, reponse.text
    return seeded_client


def test_a_reader_cannot_recover_the_margin_through_the_audit_journal(
    journal_apres_modification: TestClient,
) -> None:
    """Le cœur de la faille."""
    lecteur = login(journal_apres_modification, "lecteur@dubois.demo")
    assert Permission.MARGIN_READ not in ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.AUDIT_READ in ROLE_PERMISSIONS[Role.VIEWER]

    evenement = _evenement_de_reglages(journal_apres_modification, lecteur)
    assert evenement["payload_redacted"] is True

    for moment in ("before", "after"):
        valeurs = evenement["payload"][moment]
        for champ in ("margin_rate", "general_overheads_rate"):
            assert valeurs[champ] is None, f"{moment}.{champ} = {valeurs[champ]!r}"
        # Le nom du champ reste : qu'une politique ait changé est une
        # information d'audit légitime ; son montant est le secret.
        assert "margin_rate" in valeurs


def test_the_rest_of_the_event_stays_readable_for_a_reader(
    journal_apres_modification: TestClient,
) -> None:
    """Masquer tout le payload rendrait le journal inutile.

    Sans ce contrôle, une correction paresseuse — vider `payload` — passerait le
    test précédent tout en supprimant l'audit.
    """
    lecteur = login(journal_apres_modification, "lecteur@dubois.demo")
    evenement = _evenement_de_reglages(journal_apres_modification, lecteur)

    assert evenement["payload"]["after"]["rounding_scale"] == "3"
    assert evenement["action"] == ACTION
    assert evenement["actor_email"] == "admin@dubois.demo"
    assert evenement["summary"]


def test_an_authorised_user_still_reads_the_values(
    journal_apres_modification: TestClient,
) -> None:
    """Le masque suit la permission, il ne s'applique pas à tout le monde."""
    admin = login(journal_apres_modification, "admin@dubois.demo")
    assert Permission.MARGIN_READ in ROLE_PERMISSIONS[Role.ORG_ADMIN]

    evenement = _evenement_de_reglages(journal_apres_modification, admin)
    assert evenement["payload_redacted"] is False
    assert evenement["payload"]["after"]["margin_rate"] == "0.17"
    assert evenement["payload"]["before"]["margin_rate"] == "0.08"


def test_the_stored_payload_and_the_chain_are_untouched(
    journal_apres_modification: TestClient,
) -> None:
    """Corriger une fuite ne doit pas casser l'intégrité qu'on protégeait.

    Le masque s'applique à la copie rendue. La ligne en base garde sa valeur et
    son empreinte, et `verify_chain` — qui relit la base — reste valide.
    """
    from sqlalchemy import select

    from metreo_api.db import get_session_factory
    from metreo_api.models import AuditEvent

    lecteur = login(journal_apres_modification, "lecteur@dubois.demo")
    rendu = _evenement_de_reglages(journal_apres_modification, lecteur)

    session = get_session_factory()()
    try:
        stocke = session.scalars(select(AuditEvent).where(AuditEvent.id == rendu["id"])).one()
        # La base garde la valeur : c'est elle qui a été scellée.
        assert stocke.payload["after"]["margin_rate"] == "0.17"
        assert stocke.hash == rendu["hash"]
    finally:
        session.close()

    verification = journal_apres_modification.get("/api/v1/audit/verify", headers=lecteur)
    assert verification.status_code == 200, verification.text
    assert verification.json()["valid"] is True


def test_both_masks_are_driven_by_the_same_list(
    journal_apres_modification: TestClient,
) -> None:
    """Deux listes tenues séparément, c'est ce qui a ouvert la fuite.

    L'écran des réglages masque un ensemble de champs ; le journal doit masquer
    exactement le même. Ce test compare les deux résultats observés, sans
    relire la constante d'un seul côté : ajouter un champ commercial à l'un et
    l'oublier dans l'autre le fait échouer.
    """
    lecteur = login(journal_apres_modification, "lecteur@dubois.demo")

    reglages = journal_apres_modification.get(
        "/api/v1/organization/settings", headers=lecteur
    ).json()
    masques_par_les_reglages = {
        champ for champ in CHAMPS_COMMERCIAUX_SENSIBLES if reglages.get(champ) is None
    }
    assert masques_par_les_reglages == set(CHAMPS_COMMERCIAUX_SENSIBLES), (
        "l'écran des réglages ne masque pas tous les champs déclarés sensibles : "
        f"{sorted(set(CHAMPS_COMMERCIAUX_SENSIBLES) - masques_par_les_reglages)}"
    )

    # Et le journal masque les mêmes, sur un événement qui les porte tous.
    admin = login(journal_apres_modification, "admin@dubois.demo")
    tous = {
        "site_overheads_rate": "0.07",
        "site_overheads_base": "direct_cost",
        "general_overheads_rate": "0.12",
        "general_overheads_base": "direct_cost",
        "contingency_rate": "0.02",
        "contingency_base": "running_total",
        "margin_rate": "0.09",
        "margin_method": "on_cost",
    }
    assert set(tous) == set(CHAMPS_COMMERCIAUX_SENSIBLES), (
        "ce test doit exercer exactement les champs déclarés sensibles"
    )
    reponse = journal_apres_modification.patch(
        "/api/v1/organization/settings", headers=admin, json=tous
    )
    assert reponse.status_code == 200, reponse.text

    evenement = _evenement_de_reglages(journal_apres_modification, lecteur)
    en_clair = [
        champ
        for moment in ("before", "after")
        for champ, valeur in evenement["payload"][moment].items()
        if champ in CHAMPS_COMMERCIAUX_SENSIBLES and valeur is not None
    ]
    assert en_clair == [], f"champs commerciaux encore lisibles par un lecteur : {en_clair}"
