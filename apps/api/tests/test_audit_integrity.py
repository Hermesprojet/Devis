"""Le hash d'audit couvre tout champ dont l'altération doit être détectée.

Régression P0-4 de la revue indépendante : `actor_email` modifié directement en
base laissait `verify_chain` répondre `valid: True`. Le modèle de menaces
promettait pourtant la détection de « toute modification d'une ligne ».
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from .conftest import login

#: Chaque champ porté par un événement et dont la falsification doit être vue.
#: Ajouter une colonne à `AuditEvent` sans l'ajouter ici — ou au hash — laisse
#: un angle mort ; le test de couverture ci-dessous le refuse.
TAMPERABLE_FIELDS = {
    "actor_email": "pirate@ailleurs.test",
    "actor_user_id": "00000000-0000-0000-0000-000000000000",
    "action": "boq_item.deleted",
    "object_type": "autre_chose",
    "object_id": "00000000-0000-0000-0000-000000000000",
    "summary": "Résumé réécrit après coup",
    "request_id": "falsifié",
}


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def organization_id(seeded_client: TestClient, headers: dict[str, str]) -> str:
    seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "AUD-1", "name": "Audit"}
    )
    from metreo_api.models import AuditEvent

    session = _session()
    try:
        event = session.scalars(select(AuditEvent).limit(1)).first()
        assert event is not None
        return str(event.organization_id)
    finally:
        session.close()


def _session():
    from metreo_api.db import get_session_factory

    return get_session_factory()()


@pytest.mark.parametrize("field,forged", sorted(TAMPERABLE_FIELDS.items()))
def test_falsifying_a_field_breaks_the_chain(organization_id: str, field: str, forged: str) -> None:
    from metreo_api.models import AuditEvent
    from metreo_api.services import audit

    session = _session()
    try:
        event = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.sequence.asc())
            .limit(1)
        ).first()
        assert event is not None, "aucun événement à falsifier"
        assert audit.verify_chain(session, organization_id)["valid"] is True

        session.execute(
            update(AuditEvent).where(AuditEvent.id == event.id).values(**{field: forged})
        )
        session.commit()

        report = audit.verify_chain(session, organization_id)
        assert report["valid"] is False, f"falsification de {field} non détectée"
    finally:
        session.close()


def test_moving_an_event_to_another_real_tenant_breaks_the_chain(
    organization_id: str,
) -> None:
    """Déplacer un événement vers une organisation qui existe vraiment.

    Une organisation inexistante est déjà refusée par la clé étrangère ; c'est
    le déplacement vers un tenant réel que seul le hash peut détecter.
    """
    from metreo_api.models import AuditEvent, Organization
    from metreo_api.services import audit

    session = _session()
    try:
        other = session.scalars(
            select(Organization).where(Organization.id != organization_id).limit(1)
        ).first()
        assert other is not None, "le jeu de démonstration doit porter deux organisations"
        event = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.sequence.desc())
            .limit(1)
        ).first()
        assert event is not None
        session.execute(
            update(AuditEvent)
            .where(AuditEvent.id == event.id)
            .values(organization_id=other.id, sequence=9999)
        )
        session.commit()
        assert audit.verify_chain(session, other.id)["valid"] is False
    finally:
        session.close()


def test_falsifying_the_chain_link_alone_breaks_the_chain(organization_id: str) -> None:
    """`previous_hash` réécrit seul, sans toucher au reste de l'événement.

    C'est le seul champ que le hash de l'événement ne protège pas de
    lui-même : `expected` est recalculé à partir du maillon **courant** de la
    boucle, pas de la colonne stockée. Une ligne dont on ne change que
    `previous_hash` garde donc un `hash` correct et une `sequence` intacte —
    seule la comparaison `event.previous_hash != previous_hash` la voit.

    Mesuré : en retirant cette comparaison de `verify_chain`, la falsification
    ci-dessous passe de `valid: False` à `valid: True`, et la suite complète
    reste verte. La liste `structural` du test de couverture affirmait que
    `previous_hash` était « vérifié séparément » ; il ne l'était pas.
    """
    from metreo_api.models import AuditEvent
    from metreo_api.services import audit

    session = _session()
    try:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.sequence.asc())
        ).all()
        assert len(events) >= 2, (
            "il faut au moins deux événements pour qu'il existe un maillon à "
            f"falsifier ; le journal en porte {len(events)}"
        )
        assert audit.verify_chain(session, organization_id)["valid"] is True

        cible = events[-1]
        hash_avant, sequence_avant = cible.hash, cible.sequence
        session.execute(
            update(AuditEvent).where(AuditEvent.id == cible.id).values(previous_hash="0" * 64)
        )
        session.commit()

        # Ce qui rend la détection non triviale : rien d'autre n'a bougé.
        relu = session.get(AuditEvent, cible.id)
        assert relu is not None
        assert relu.hash == hash_avant
        assert relu.sequence == sequence_avant

        verdict = audit.verify_chain(session, organization_id)
        assert verdict["valid"] is False
        assert verdict["failed_at_sequence"] == sequence_avant
    finally:
        session.close()


def test_every_significant_column_is_covered_by_the_hash() -> None:
    """Un champ ajouté au modèle et oublié dans le hash est un angle mort."""
    from metreo_api.models import AuditEvent

    columns = {c.name for c in AuditEvent.__table__.columns}
    # Ces colonnes sont exclues à dessein : `hash` est le résultat lui-même,
    # `previous_hash` et `sequence` sont la structure de la chaîne — le premier
    # par `test_falsifying_the_chain_link_alone_breaks_the_chain`, le second par
    # le contrôle `sequence_gap` de `verify_chain` — `payload` est déjà couvert,
    # `id` est vérifié via le hash.
    # `hash_schema_version` est structurel : il dit sous quel schéma l'événement
    # a été scellé, et `verify_chain` s'en sert pour refuser de juger un scellé
    # d'hier avec le code d'aujourd'hui. Le falsifier fait échouer la
    # vérification par ce chemin, pas par le hash.
    structural = {
        "hash",
        "previous_hash",
        "sequence",
        "payload",
        "occurred_at",
        "id",
        "hash_schema_version",
    }
    # `organization_id` a son propre test : le falsifier vers une organisation
    # inexistante est refusé par la clé étrangère, pas par le hash.
    uncovered = columns - structural - set(TAMPERABLE_FIELDS) - {"organization_id"}
    assert uncovered == set(), f"colonnes sans test de falsification : {uncovered}"


def test_the_request_id_reaches_the_response_header_and_the_event(
    seeded_client: TestClient, headers: dict[str, str]
) -> None:
    """Corrélation réelle entre la requête, sa réponse et le journal."""
    from metreo_api.models import AuditEvent

    marker = "essai-de-correlation-1234"
    response = seeded_client.post(
        "/api/v1/projects",
        headers={**headers, "X-Request-Id": marker},
        json={"reference": "COR-1", "name": "Corrélation"},
    )
    assert response.status_code == 201
    assert response.headers.get("X-Request-Id") == marker

    session = _session()
    try:
        event = session.scalars(
            select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
        ).first()
        assert event is not None
        assert event.request_id == marker, "l'identifiant de requête n'atteint pas l'événement"
    finally:
        session.close()
