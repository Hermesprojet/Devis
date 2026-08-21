"""La migration classe les événements existants au lieu de les invalider.

Bloquant de la revue : une première version attribuait la version 1 à toutes
les lignes existantes, alors que le commit précédent scellait déjà en v2. Une
chaîne produite la veille devenait invalide après migration.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from .conftest import running_on_postgresql


def _seal(values: dict[str, Any], version: int) -> str:
    from metreo_api.services.audit import HASH_SCHEMAS

    material = dict(values)
    material["schema_version"] = version
    return str(HASH_SCHEMAS[version](material))


def _insert_event(
    connection: Any, *, organization_id: str, sequence: int, version: int, previous_hash: str | None
) -> str:
    """Écrit un événement scellé sous `version`, sans passer par `record()`.

    Le but est de fabriquer une chaîne historique réelle, telle qu'un ancien
    code l'aurait produite. Le scellé est calculé à partir de la valeur
    **relue** de la base, et non de celle envoyée : PostgreSQL et SQLite ne
    rendent pas un horodatage sous la même forme, et sceller la chaîne envoyée
    rendrait le test vert sur un moteur et rouge sur l'autre — sans que le code
    y soit pour quoi que ce soit.
    """
    event_id = f"evt-{version}-{sequence:04d}"
    connection.execute(
        text(
            "INSERT INTO audit_events (id, organization_id, sequence, occurred_at, "
            "actor_email, action, object_type, summary, payload, previous_hash, hash, "
            "hash_schema_version) VALUES (:id, :org, :seq, :at, :email, :action, :otype, "
            ":summary, :payload, :prev, :hash, :ver)"
        ),
        {
            "id": event_id,
            "org": organization_id,
            "seq": sequence,
            "at": datetime(2026, 8, 20, 12, 0, 0),
            "email": "historique@dubois.demo",
            "action": "test.historique",
            "otype": "test",
            "summary": f"Événement historique {sequence}",
            "payload": json.dumps({}),
            "prev": previous_hash,
            "hash": "à-remplacer",
            "ver": version,
        },
    )
    stored_at = connection.execute(
        text("SELECT occurred_at FROM audit_events WHERE id = :id"), {"id": event_id}
    ).scalar_one()
    if isinstance(stored_at, str):
        stored_at = datetime.fromisoformat(stored_at.replace(" ", "T"))

    digest = _seal(
        {
            "event_id": event_id,
            "organization_id": organization_id,
            "sequence": sequence,
            "occurred_at": stored_at.isoformat(),
            "actor_user_id": None,
            "actor_email": "historique@dubois.demo",
            "request_id": None,
            "action": "test.historique",
            "object_type": "test",
            "object_id": None,
            "summary": f"Événement historique {sequence}",
            "payload": {},
            "previous_hash": previous_hash or "",
        },
        version,
    )
    connection.execute(
        text("UPDATE audit_events SET hash = :h WHERE id = :id"),
        {"h": digest, "id": event_id},
    )
    return digest


@pytest.fixture()
def organization(migrated: None, database_url: str) -> str:
    from metreo_api.models import Organization

    engine = create_engine(database_url)
    with Session(engine) as session:
        org = Organization(name="Historique")
        session.add(org)
        session.commit()
        organization_id = org.id
    engine.dispose()
    return organization_id


def _verify(database_url: str, organization_id: str) -> dict[str, Any]:
    from metreo_api.services import audit

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            return audit.verify_chain(session, organization_id)
    finally:
        engine.dispose()


class TestHistoricalChains:
    def test_a_chain_sealed_entirely_under_v1_stays_valid(
        self, organization: str, database_url: str
    ) -> None:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            previous = None
            for sequence in range(1, 4):
                previous = _insert_event(
                    connection,
                    organization_id=organization,
                    sequence=sequence,
                    version=1,
                    previous_hash=previous,
                )
        engine.dispose()
        assert _verify(database_url, organization)["valid"] is True

    def test_a_chain_sealed_entirely_under_v2_stays_valid(
        self, organization: str, database_url: str
    ) -> None:
        """Le cas que la première migration cassait."""
        engine = create_engine(database_url)
        with engine.begin() as connection:
            previous = None
            for sequence in range(1, 4):
                previous = _insert_event(
                    connection,
                    organization_id=organization,
                    sequence=sequence,
                    version=2,
                    previous_hash=previous,
                )
        engine.dispose()
        assert _verify(database_url, organization)["valid"] is True

    def test_a_mixed_chain_v1_then_v2_stays_valid(
        self, organization: str, database_url: str
    ) -> None:
        """Le cas réel d'une base migrée en cours de route."""
        engine = create_engine(database_url)
        with engine.begin() as connection:
            previous = _insert_event(
                connection, organization_id=organization, sequence=1, version=1, previous_hash=None
            )
            previous = _insert_event(
                connection,
                organization_id=organization,
                sequence=2,
                version=1,
                previous_hash=previous,
            )
            _insert_event(
                connection,
                organization_id=organization,
                sequence=3,
                version=2,
                previous_hash=previous,
            )
        engine.dispose()
        assert _verify(database_url, organization)["valid"] is True


class TestTamperingIsStillDetected:
    @pytest.mark.parametrize("version", [1, 2])
    def test_falsifying_a_summary_breaks_the_chain(
        self, organization: str, database_url: str, version: int
    ) -> None:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            _insert_event(
                connection,
                organization_id=organization,
                sequence=1,
                version=version,
                previous_hash=None,
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET summary = :s WHERE organization_id = :o"),
                {"s": "Résumé réécrit", "o": organization},
            )
        engine.dispose()
        assert _verify(database_url, organization)["valid"] is False

    def test_falsifying_an_actor_email_is_detected_on_v2_only(
        self, organization: str, database_url: str
    ) -> None:
        """La v1 ne couvrait pas ce champ : le dire honnêtement.

        C'est exactement la raison d'être de la v2, et la preuve que garder
        l'ancien algorithme ne rétablit pas l'ancienne faiblesse pour les
        événements récents.
        """
        engine = create_engine(database_url)
        with engine.begin() as connection:
            _insert_event(
                connection, organization_id=organization, sequence=1, version=2, previous_hash=None
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET actor_email = :e WHERE organization_id = :o"),
                {"e": "pirate@ailleurs.test", "o": organization},
            )
        engine.dispose()
        assert _verify(database_url, organization)["valid"] is False


class TestUnknownVersion:
    def test_an_unknown_schema_version_is_refused_explicitly(
        self, organization: str, database_url: str
    ) -> None:
        """Après un retour en arrière du code sur une base déjà migrée."""
        engine = create_engine(database_url)
        with engine.begin() as connection:
            _insert_event(
                connection, organization_id=organization, sequence=1, version=2, previous_hash=None
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET hash_schema_version = 99 WHERE organization_id = :o"),
                {"o": organization},
            )
        engine.dispose()
        report = _verify(database_url, organization)
        assert report["valid"] is False
        assert report["reason"] == "unsupported_hash_schema_version"
        assert report["event_schema_version"] == 99


@pytest.mark.skipif(
    not running_on_postgresql(), reason="Le comportement est vérifié aussi sur SQLite."
)
def test_the_same_holds_on_postgresql(organization: str, database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        previous = _insert_event(
            connection, organization_id=organization, sequence=1, version=1, previous_hash=None
        )
        _insert_event(
            connection, organization_id=organization, sequence=2, version=2, previous_hash=previous
        )
    engine.dispose()
    assert _verify(database_url, organization)["valid"] is True


class TestTheMigrationItself:
    """La migration exécutée sur une base peuplée, pas seulement le lecteur.

    Reproduit le scénario de la revue : une base à la révision précédente,
    peuplée par le code qui scellait déjà en v2, puis migrée.
    """

    @staticmethod
    def _populate_before_migration(database_url: str, version: int) -> None:

        engine = create_engine(database_url)
        with engine.begin() as connection:
            # `inspect` plutôt qu'un PRAGMA : le même test doit tourner sur
            # les deux moteurs, et PRAGMA n'existe que sur SQLite.
            columns = [c["name"] for c in inspect(connection).get_columns("organizations")]
            wanted = {
                "id": "org-mig",
                "name": "Migration",
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
                    f"INSERT INTO organizations ({','.join(use)}) "
                    f"VALUES ({','.join(':' + k for k in use)})"
                ),
                use,
            )
            previous = None
            for sequence in (1, 2):
                event_id = f"m{sequence}"
                connection.execute(
                    text(
                        "INSERT INTO audit_events (id, organization_id, sequence, occurred_at, "
                        "actor_email, action, object_type, summary, payload, previous_hash, hash) "
                        "VALUES (:i,:o,:s,:t,:e,:a,:ot,:su,:p,:pr,:h)"
                    ),
                    {
                        "i": event_id,
                        "o": "org-mig",
                        "s": sequence,
                        "t": datetime(2026, 8, 20, 12, 0, 0),
                        "e": "avant@migration.test",
                        "a": "test.avant",
                        "ot": "t",
                        "su": f"Avant migration {sequence}",
                        "p": json.dumps({}),
                        "pr": previous,
                        "h": "à-remplacer",
                    },
                )
                # Sceller à partir de la valeur relue : les deux moteurs ne
                # rendent pas un horodatage sous la même forme.
                stored_at = connection.execute(
                    text("SELECT occurred_at FROM audit_events WHERE id = :i"), {"i": event_id}
                ).scalar_one()
                if isinstance(stored_at, str):
                    stored_at = datetime.fromisoformat(stored_at.replace(" ", "T"))
                digest = _seal(
                    {
                        "event_id": event_id,
                        "organization_id": "org-mig",
                        "sequence": sequence,
                        "occurred_at": stored_at.isoformat(),
                        "actor_user_id": None,
                        "actor_email": "avant@migration.test",
                        "request_id": None,
                        "action": "test.avant",
                        "object_type": "t",
                        "object_id": None,
                        "summary": f"Avant migration {sequence}",
                        "payload": {},
                        "previous_hash": previous or "",
                    },
                    version,
                )
                connection.execute(
                    text("UPDATE audit_events SET hash = :h WHERE id = :i"),
                    {"h": digest, "i": event_id},
                )
                previous = digest
        engine.dispose()

    @pytest.mark.parametrize("version", [1, 2])
    def test_a_populated_database_survives_the_migration(
        self, app_env: None, database_url: str, version: int
    ) -> None:
        import subprocess
        import sys
        from pathlib import Path

        api_root = Path(__file__).resolve().parent.parent
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(api_root / "src"),
            "METREO_DATABASE_URL": database_url,
            "METREO_ENVIRONMENT": "development",
            "METREO_AUTH_MODE": "dev",
        }

        def alembic(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                cwd=api_root,
                env=env,
                capture_output=True,
                text=True,
            )

        assert alembic("upgrade", "d88792b38c2d").returncode == 0
        self._populate_before_migration(database_url, version)

        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stdout + result.stderr

        engine = create_engine(database_url)
        with engine.begin() as connection:
            stored = [
                row[0]
                for row in connection.execute(
                    text("SELECT hash_schema_version FROM audit_events ORDER BY sequence")
                )
            ]
        engine.dispose()
        assert stored == [version, version], "la migration a mal classé les événements"
        assert _verify(database_url, "org-mig")["valid"] is True
