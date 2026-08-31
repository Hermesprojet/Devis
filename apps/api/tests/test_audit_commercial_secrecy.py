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

from metreo_api.schemas import CHAMPS_COMMERCIAUX_SENSIBLES, OrganizationSettingsOut
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


#: Les réglages que l'on déclare **non** commerciaux, et donc lisibles par
#: quiconque a `audit:read`. Chaque nom ici est une décision assumée, pas un
#: oubli : `rounding_scale` et consorts décrivent la présentation des nombres,
#: `quote_number_pattern` la numérotation, `ai_enabled` l'activation d'une
#: fonction. Aucun ne révèle ce que l'entreprise gagne.
#:
#: `show_internal_costs_in_client_pdf` est ici volontairement : il dit si les
#: coûts figurent au devis, pas quels ils sont. Le contenu, lui, est protégé
#: par `cost:read` et par le réglage lui-même.
REGLAGES_NON_COMMERCIAUX = frozenset(
    {
        "rounding_scale",
        "rounding_mode",
        "unit_price_scale",
        "missing_price_policy",
        "quote_number_pattern",
        "show_internal_costs_in_client_pdf",
        "ai_enabled",
        # La durée de conservation ne dit rien d'un coût, d'une marge ni d'une
        # politique de prix : elle dit combien de temps l'entreprise garde ce
        # qu'elle a déjà envoyé à ses clients. C'est un réglage de gouvernance
        # des données, et le masquer nuirait — la trace de qui l'a fixée, et à
        # quelle valeur, est précisément ce qu'on veut pouvoir relire quand une
        # destruction est contestée.
        "quote_retention_years",
    }
)

#: Les réglages non commerciaux dont `null` est une VALEUR, pas un masque.
#:
#: `quote_retention_years` vaut `null` sur toute organisation neuve : la durée
#: de conservation n'a pas été tranchée, et c'est l'état qui fait refuser une
#: destruction. Exiger qu'il soit non nul pour prouver qu'il est lisible
#: confondrait « absent » et « vide ».
NULL_SIGNIFIANT = frozenset({"quote_retention_years"})


def test_a_new_setting_cannot_be_added_without_deciding_if_it_is_sensitive() -> None:
    """Le vrai risque n'est pas la liste d'aujourd'hui, c'est celle de demain.

    Les huit champs déclarés sensibles couvrent exactement l'état actuel — le
    test précédent le vérifie. Mais rien n'obligeait le prochain réglage
    commercial à rejoindre cette liste : un champ ajouté à
    `OrganizationSettingsUpdate` entre dans la charge utile du journal
    immédiatement, et en sort en clair tant que personne n'y pense.

    C'est exactement la liste de refus que la PR sur les coûts internes a
    montrée fragile, transposée ici. Ce test la retourne en liste
    d'autorisation : tout champ doit être rangé d'un côté ou de l'autre, et un
    champ nouveau fait rougir la suite jusqu'à ce que quelqu'un tranche.
    """
    from metreo_api.schemas import OrganizationSettingsUpdate

    modifiables = set(OrganizationSettingsUpdate.model_fields)
    classes = set(CHAMPS_COMMERCIAUX_SENSIBLES) | REGLAGES_NON_COMMERCIAUX

    non_classes = modifiables - classes
    assert not non_classes, (
        f"réglages modifiables et non classés : {sorted(non_classes)}. "
        "Ajoutez-les à CHAMPS_COMMERCIAUX_SENSIBLES (schemas.py) s'ils "
        "révèlent la politique commerciale, sinon à REGLAGES_NON_COMMERCIAUX "
        "ici — et dites pourquoi."
    )

    fantomes = set(CHAMPS_COMMERCIAUX_SENSIBLES) - modifiables
    assert not fantomes, (
        f"déclarés sensibles mais non modifiables : {sorted(fantomes)}. "
        "Un masque sur un champ qui n'existe plus ne protège rien et cache "
        "que la liste a dérivé."
    )


def test_every_sensitive_setting_is_also_masked_on_the_settings_endpoint(
    seeded_client: TestClient,
) -> None:
    """Les deux lecteurs de la liste la respectent, champ par champ.

    Le test de la liste unique montre que le journal masque les huit. Celui-ci
    montre que `/organization/settings` les masque aussi — sans quoi la
    « liste unique, deux lecteurs » n'aurait qu'un lecteur.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    reponse = seeded_client.get("/api/v1/organization/settings", headers=lecteur)
    assert reponse.status_code == 200, reponse.text
    reglages = reponse.json()

    assert reglages["commercial_rates_visible"] is False
    visibles = [champ for champ in CHAMPS_COMMERCIAUX_SENSIBLES if reglages.get(champ) is not None]
    assert visibles == [], f"champs commerciaux lisibles par un lecteur : {visibles}"

    # Et les réglages non commerciaux restent lisibles : masquer tout serait
    # une autre façon de casser la page « paramètres » pour ce rôle.
    #
    # « Lisible » se vérifie sur la PRÉSENCE de la clé, pas sur sa non-nullité.
    # La nuance est arrivée avec `quote_retention_years`, dont `null` est une
    # valeur de plein droit — « durée non tranchée », l'état d'une organisation
    # neuve — et non un masque. Confondre les deux ferait rougir la suite pour
    # un champ parfaitement lisible, et masquerait le jour où un vrai champ
    # deviendrait nul sans raison.
    for champ in REGLAGES_NON_COMMERCIAUX & set(OrganizationSettingsOut.model_fields):
        assert champ in reglages, f"{champ} absent de la réponse : masqué à tort"
    for champ in REGLAGES_NON_COMMERCIAUX & set(reglages) - NULL_SIGNIFIANT:
        assert reglages[champ] is not None, f"{champ} masqué à tort"
