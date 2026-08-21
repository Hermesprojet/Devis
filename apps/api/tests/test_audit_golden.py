"""Empreintes de référence, figées et indépendantes du code courant.

Les tests de `test_audit_migration.py` calculent leurs hashs avec
`HASH_SCHEMAS` — le code même qu'ils vérifient. Ils resteraient donc verts si
l'algorithme et le test dérivaient ensemble. Ce fichier ferme cet angle mort :
les empreintes ci-dessous ont été calculées **une fois** et inscrites en dur.
Si un jour l'algorithme change sans que sa version change, ces tests tombent.

Ne jamais régénérer ces constantes depuis le code. Une empreinte qui ne
correspond plus est le symptôme à examiner, pas la valeur à mettre à jour :
la corriger reviendrait à déclarer valide tout ce que le nouveau code produit,
y compris ce qu'il produit à tort.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

# Un événement unique, décrit champ par champ. Les valeurs sont fixes : rien
# ici ne dépend de l'horloge, d'un identifiant engendré ou de la configuration.
GOLDEN_EVENT: dict[str, Any] = {
    "event_id": "golden-1",
    "organization_id": "org-golden",
    "sequence": 1,
    "occurred_at": "2026-08-20T12:00:00",
    "actor_user_id": None,
    "actor_email": "historique@dubois.demo",
    "request_id": None,
    "action": "test.golden",
    "object_type": "test",
    "object_id": None,
    "summary": "Événement de référence 1",
    "payload": {},
    "previous_hash": "",
}

#: Scellé sous le schéma v1 — celui d'avant la revue.
V1_HASH = "72d9c84a42f4f45f5f9a01b983e72bdf27abb01b6899c472fc85b2fa632aa987"

#: Scellé sous le schéma v2 — celui que `fc2dcb0` produisait déjà.
V2_HASH = "9eff8e7dbf41210e78167994582aba6dcb09532775fe4009ca84cd633ce43b08"

#: Deuxième maillon d'une chaîne v1 pure, chaîné sur V1_HASH.
V1_SECOND_HASH = "d4f191ece78066ef1980c35640774a46e03fd9401de4f6ace38a52800bb31289"

#: Deuxième maillon scellé en v2, chaîné sur un premier maillon v1 : le cas
#: d'une base migrée en cours de route.
MIXED_SECOND_HASH = "24ed7eca5a07abc03530e3966adf68dd3c1bd0a4d531d67767f41816a5a7b962"


class TestTheAlgorithmsStillProduceTheFrozenHashes:
    def test_v1_reproduces_its_golden_hash(self) -> None:
        from metreo_api.services.audit import HASH_SCHEMAS

        assert HASH_SCHEMAS[1]({**GOLDEN_EVENT, "schema_version": 1}) == V1_HASH

    def test_v2_reproduces_its_golden_hash(self) -> None:
        from metreo_api.services.audit import HASH_SCHEMAS

        assert HASH_SCHEMAS[2]({**GOLDEN_EVENT, "schema_version": 2}) == V2_HASH

    def test_the_two_versions_do_not_collide(self) -> None:
        assert V1_HASH != V2_HASH

    def test_classification_recognises_each_frozen_hash(self) -> None:
        from metreo_api.services.audit import classify_schema_version

        assert classify_schema_version(GOLDEN_EVENT, V1_HASH) == 1
        assert classify_schema_version(GOLDEN_EVENT, V2_HASH) == 2
        assert classify_schema_version(GOLDEN_EVENT, "0" * 64) is None


def _insert_raw(connection: Any, event: dict[str, Any], digest: str, schema_version: int) -> None:
    """Écrit un événement avec un hash imposé, sans calculer quoi que ce soit."""
    connection.execute(
        text(
            "INSERT INTO audit_events (id, organization_id, sequence, occurred_at, "
            "actor_email, action, object_type, summary, payload, previous_hash, hash, "
            "hash_schema_version) VALUES (:i,:o,:s,:t,:e,:a,:ot,:su,:p,:pr,:h,:v)"
        ),
        {
            "i": event["event_id"],
            "o": event["organization_id"],
            "s": event["sequence"],
            "t": datetime.fromisoformat(event["occurred_at"]),
            "e": event["actor_email"],
            "a": event["action"],
            "ot": event["object_type"],
            "su": event["summary"],
            "p": json.dumps(event["payload"]),
            "pr": event["previous_hash"] or None,
            "h": digest,
            "v": schema_version,
        },
    )


def _create_organization(connection: Any) -> None:
    columns = [c["name"] for c in inspect(connection).get_columns("organizations")]
    wanted = {
        "id": "org-golden",
        "name": "Référence",
        "country_code": "BE",
        "region_code": "BE-WAL",
        "locale": "fr-BE",
        "currency": "EUR",
        "timezone": "Europe/Brussels",
        "unit_system": "metric",
        "is_demo_data": 0,
        "created_at": datetime(2026, 8, 20, 12, 0, 0),
        "updated_at": datetime(2026, 8, 20, 12, 0, 0),
    }
    use = {k: v for k, v in wanted.items() if k in columns}
    connection.execute(
        text(
            f"INSERT INTO organizations ({','.join(use)}) VALUES ({','.join(':' + k for k in use)})"
        ),
        use,
    )


@pytest.mark.parametrize(
    "first_version,first_digest,second_version,second_digest",
    [
        pytest.param(1, V1_HASH, 1, V1_SECOND_HASH, id="chaîne-v1-pure"),
        # Premier maillon scellé en v1, second en v2 : le cas d'une base
        # migrée en cours de route, celui que la première migration cassait.
        pytest.param(1, V1_HASH, 2, MIXED_SECOND_HASH, id="chaîne-mixte-v1-puis-v2"),
    ],
)
def test_a_frozen_chain_verifies_after_migration(
    migrated: None,
    database_url: str,
    first_version: int,
    first_digest: str,
    second_version: int,
    second_digest: str,
) -> None:
    """Une chaîne écrite avec des hashs figés reste vérifiable.

    Aucun hash n'est calculé ici : ils viennent des constantes ci-dessus.
    """
    from metreo_api.services import audit

    engine = create_engine(database_url)
    with engine.begin() as connection:
        _create_organization(connection)
        _insert_raw(connection, GOLDEN_EVENT, first_digest, first_version)
        second = {
            **GOLDEN_EVENT,
            "event_id": "golden-2",
            "sequence": 2,
            "summary": "Événement de référence 2",
            "previous_hash": first_digest,
        }
        _insert_raw(connection, second, second_digest, second_version)

    try:
        with Session(engine) as session:
            report = audit.verify_chain(session, "org-golden")
    finally:
        engine.dispose()
    assert report["valid"] is True, report
