"""Deux écritures d'audit simultanées obtiennent deux séquences consécutives.

Bloquant C de la revue : verrouiller le dernier événement ne fermait pas la
course sur le PREMIER événement d'une organisation — il n'y avait aucune ligne
à verrouiller, et deux transactions calculaient `sequence = 1`.

Ces tests exigent un vrai PostgreSQL : SQLite sérialise ses écritures et ne
peut donc pas exhiber la course.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from .conftest import running_on_postgresql

pytestmark = pytest.mark.skipif(
    not running_on_postgresql(),
    reason="La course ne s'observe que sur PostgreSQL ; SQLite sérialise ses écritures.",
)


def _write_in_its_own_session(
    database_url: str,
    organization_id: str,
    barrier: threading.Barrier,
    failures: list[BaseException],
) -> None:
    from metreo_api.services import audit

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            # Les deux fils se rejoignent ici : sans verrou, ils lisent la même
            # séquence et l'un des deux viole uq_audit_org_sequence.
            barrier.wait(timeout=10)
            audit.record(
                session,
                organization_id=organization_id,
                action="test.concurrent",
                object_type="test",
                summary="Écriture concurrente",
            )
            session.commit()
    except BaseException as exc:
        failures.append(exc)
    finally:
        engine.dispose()


def _run_two_concurrent_writes(database_url: str, organization_id: str) -> None:
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []
    threads = [
        threading.Thread(
            target=_write_in_its_own_session,
            args=(database_url, organization_id, barrier, failures),
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not failures, f"écriture concurrente en échec : {failures}"


def _sequences(database_url: str, organization_id: str) -> list[int]:
    from metreo_api.models import AuditEvent

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            return list(
                session.scalars(
                    select(AuditEvent.sequence)
                    .where(AuditEvent.organization_id == organization_id)
                    .order_by(AuditEvent.sequence.asc())
                ).all()
            )
    finally:
        engine.dispose()


def test_two_concurrent_writes_on_an_empty_chain(migrated: None, database_url: str) -> None:
    """Le cas que le verrou sur le dernier événement ne couvrait pas."""
    from metreo_api.models import Organization

    engine = create_engine(database_url)
    with Session(engine) as session:
        organization = Organization(name="Concurrence")
        session.add(organization)
        session.commit()
        organization_id = organization.id
    engine.dispose()

    _run_two_concurrent_writes(database_url, organization_id)
    assert _sequences(database_url, organization_id) == [1, 2]


def test_two_concurrent_writes_on_an_existing_chain(
    seeded: dict[str, str], database_url: str
) -> None:
    from metreo_api.models import AuditEvent

    engine = create_engine(database_url)
    with Session(engine) as session:
        organization_id = session.scalars(select(AuditEvent.organization_id).limit(1)).one()
        before = session.scalar(
            select(func.max(AuditEvent.sequence)).where(
                AuditEvent.organization_id == organization_id
            )
        )
    engine.dispose()

    _run_two_concurrent_writes(database_url, organization_id)
    sequences = _sequences(database_url, organization_id)
    assert sequences[-2:] == [before + 1, before + 2]
    assert len(sequences) == len(set(sequences)), "séquences dupliquées"
