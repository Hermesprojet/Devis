"""Deux créations simultanées ne doivent pas se disputer un numéro de version.

Bloquant J de la revue. Deux endroits calculaient le prochain numéro de la
même façon — lire tous les numéros existants, prendre le maximum, ajouter un :

* `routers/pricebooks.py:create_version` ;
* `services/estimating.py:next_version_number`.

Entre la lecture et l'écriture, rien ne retenait une seconde transaction.
Les deux lisaient `1`, les deux choisissaient `2`, la première validait et la
seconde heurtait la contrainte d'unicité. Les données restaient justes — c'est
le rôle de la contrainte — mais le service rendu ne l'était pas : la seconde
requête remontait une `IntegrityError` non interceptée, donc un HTTP 500.

Ces tests exigent un vrai PostgreSQL. SQLite sérialise ses écritures : la
course n'y est pas observable, et un test qui « passe » sur SQLite ne
prouverait rien.

Chacun est passé en rouge avant correction, plusieurs fois de suite, en
neutralisant le verrou (voir `docs/PHASE1_VERIFICATION.md`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from .conftest import running_on_postgresql

pytestmark = pytest.mark.skipif(
    not running_on_postgresql(),
    reason="La course ne s'observe que sur PostgreSQL ; SQLite sérialise ses écritures.",
)


def run_in_parallel(
    work: Callable[[Session, threading.Barrier], Any],
    database_url: str,
    *,
    threads: int = 2,
) -> list[Any]:
    """Run ``work`` in ``threads`` real sessions that meet on a barrier.

    The barrier is what makes the race reproducible rather than lucky: every
    thread has opened its transaction and is about to read before any of them
    writes.
    """
    barrier = threading.Barrier(threads)
    results: list[Any] = [None] * threads

    def target(index: int) -> None:
        engine = create_engine(database_url)
        try:
            with Session(engine) as session:
                results[index] = work(session, barrier)
        except BaseException as exc:  # le test veut l'exception, pas un échec de fil
            results[index] = exc
        finally:
            engine.dispose()

    workers = [threading.Thread(target=target, args=(index,)) for index in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert not any(worker.is_alive() for worker in workers), "un fil ne s'est pas terminé"
    return results


class TestPriceBookVersionNumbering:
    def test_two_simultaneous_creations_get_distinct_contiguous_numbers(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        from metreo_api.models import PriceBook, PriceBookVersion
        from metreo_api.services import pricebook_versions

        engine = create_engine(database_url)
        with Session(engine) as session:
            book = session.scalars(select(PriceBook).limit(1)).one()
            book_id, organization_id = book.id, book.organization_id
            before = session.scalar(
                select(func.count())
                .select_from(PriceBookVersion)
                .where(PriceBookVersion.price_book_id == book_id)
            )
        engine.dispose()
        assert before is not None

        def create(session: Session, barrier: threading.Barrier) -> int:
            barrier.wait(timeout=20)
            number = pricebook_versions.next_version_number(
                session, organization_id=organization_id, price_book_id=book_id
            )
            session.add(
                PriceBookVersion(
                    organization_id=organization_id,
                    price_book_id=book_id,
                    version_number=number,
                    status="draft",
                )
            )
            session.commit()
            return number

        outcomes = run_in_parallel(create, database_url)
        errors = [item for item in outcomes if isinstance(item, BaseException)]
        assert errors == [], f"une création a échoué : {errors}"
        assert sorted(outcomes) == [before + 1, before + 2], outcomes


class TestEstimateVersionNumbering:
    def test_two_simultaneous_creations_get_distinct_contiguous_numbers(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        from metreo_api.models import Estimate, EstimateVersion
        from metreo_api.services import estimating

        engine = create_engine(database_url)
        with Session(engine) as session:
            estimate = session.scalars(select(Estimate).limit(1)).one()
            estimate_id = estimate.id
            organization_id = estimate.organization_id
            price_book_version_id = estimate.price_book_version_id
            before = session.scalar(
                select(func.count())
                .select_from(EstimateVersion)
                .where(EstimateVersion.estimate_id == estimate_id)
            )
        engine.dispose()
        assert before is not None

        def create(session: Session, barrier: threading.Barrier) -> int:
            barrier.wait(timeout=20)
            number = estimating.next_version_number(
                session, estimate_id, organization_id=organization_id
            )
            session.add(
                EstimateVersion(
                    organization_id=organization_id,
                    estimate_id=estimate_id,
                    version_number=number,
                    status="draft",
                    price_book_version_id=price_book_version_id,
                    markup={},
                    taxes=[],
                    rounding={},
                    missing_price_policy="warn",
                )
            )
            session.commit()
            return number

        outcomes = run_in_parallel(create, database_url)
        errors = [item for item in outcomes if isinstance(item, BaseException)]
        assert errors == [], f"une création a échoué : {errors}"
        assert sorted(outcomes) == [before + 1, before + 2], outcomes


class TestNeighbouringRaces:
    """Deux courses voisines, à vérifier avant de conclure."""

    def test_publishing_and_writing_a_price_are_sequential(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        """Publication contre écriture : jamais un prix ajouté après publication."""
        from metreo_api.models import PriceBookVersion
        from metreo_api.services import pricebook_versions

        engine = create_engine(database_url)
        with Session(engine) as session:
            version = session.scalars(
                select(PriceBookVersion).where(PriceBookVersion.status == "draft").limit(1)
            ).one()
            version_id, organization_id = version.id, version.organization_id
        engine.dispose()

        def publish(session: Session, barrier: threading.Barrier) -> str:
            # La barrière est franchie AVANT la prise de verrou : c'est le
            # verrou qui doit sérialiser, pas le test. L'attendre après
            # l'avoir pris fait attendre les deux fils indéfiniment.
            barrier.wait(timeout=20)
            locked = pricebook_versions.lock_version(
                session, organization_id=organization_id, version_id=version_id
            )
            if locked.status == "published":
                session.rollback()
                return "already_published"
            locked.status = "published"
            session.commit()
            return "published"

        def write(session: Session, barrier: threading.Barrier) -> str:
            barrier.wait(timeout=20)
            locked = pricebook_versions.lock_version(
                session, organization_id=organization_id, version_id=version_id
            )
            if locked.status == "published":
                session.rollback()
                return "version_published"
            from metreo_api.models import PriceItem

            session.add(
                PriceItem(
                    organization_id=organization_id,
                    price_book_version_id=version_id,
                    code="CONCURRENT-001",
                    label="Écriture concurrente",
                    unit_code="m3",
                    resource_kind="material",
                    unit_price="1.0000000000",
                    currency="EUR",
                    confidence="declared",
                )
            )
            session.commit()
            return "written"

        barrier = threading.Barrier(2)
        results: dict[str, Any] = {}

        def run(name: str, work: Callable[[Session, threading.Barrier], Any]) -> None:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    results[name] = work(session, barrier)
            except BaseException as exc:
                results[name] = exc
            finally:
                engine.dispose()

        threads = [
            threading.Thread(target=run, args=("publish", publish)),
            threading.Thread(target=run, args=("write", write)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not any(thread.is_alive() for thread in threads)
        assert not any(isinstance(value, BaseException) for value in results.values()), results

        # Le résultat doit être séquentiel et explicite, dans un sens ou l'autre.
        outcome = (results["publish"], results["write"])
        assert outcome in {
            ("published", "version_published"),  # la publication a gagné
            ("published", "written"),  # l'écriture a gagné, puis publication
        }, outcome

        engine = create_engine(database_url)
        with Session(engine) as session:
            final = session.get(PriceBookVersion, version_id)
            assert final is not None
            assert final.status == "published"
            from metreo_api.models import PriceItem

            written = session.scalar(
                select(func.count())
                .select_from(PriceItem)
                .where(PriceItem.code == "CONCURRENT-001")
            )
        engine.dispose()
        # Un prix écrit après publication serait une modification d'une version figée.
        assert written == (1 if results["write"] == "written" else 0)

    def test_two_simultaneous_freezes_leave_one_winner(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        """Un seul gel réussit, un seul événement d'audit est écrit."""
        from metreo_api.models import AuditEvent, Estimate, EstimateVersion
        from metreo_api.services import estimating
        from metreo_api.services.estimating import FreezeRefused

        engine = create_engine(database_url)
        with Session(engine) as session:
            version = session.scalars(
                select(EstimateVersion).where(EstimateVersion.status == "draft").limit(1)
            ).one()
            version_id = version.id
            estimate_id = version.estimate_id
            organization_id = version.organization_id
            # Le sujet du test est la concurrence, pas la règle des postes sans
            # prix : sans cela le refus viendrait de `missing_prices` dans les
            # deux fils et ne prouverait rien sur la course. La politique
            # appliquée au calcul vient des réglages de l'organisation.
            from metreo_api.models import OrganizationSettings

            settings = session.get(OrganizationSettings, organization_id)
            assert settings is not None
            settings.missing_price_policy = "warn"
            version.missing_price_policy = "warn"
            session.commit()
        engine.dispose()

        def freeze(session: Session, barrier: threading.Barrier) -> str:
            from metreo_api.services import audit

            estimate = session.get(Estimate, estimate_id)
            assert estimate is not None
            barrier.wait(timeout=20)
            locked = estimating.lock_version(
                session, organization_id=organization_id, version_id=version_id
            )
            try:
                frozen, _ = estimating.freeze_version(
                    session, estimate=estimate, version=locked, actor_user_id=None
                )
            except FreezeRefused as exc:
                session.rollback()
                return exc.reason
            audit.record(
                session,
                organization_id=organization_id,
                action="estimate_version.frozen",
                object_type="estimate_version",
                object_id=frozen.id,
                summary="Gel concurrent",
            )
            session.commit()
            return "frozen"

        outcomes = run_in_parallel(freeze, database_url)
        errors = [item for item in outcomes if isinstance(item, BaseException)]
        assert errors == [], f"un gel a échoué autrement qu'en 409 : {errors}"
        assert sorted(outcomes) == ["already_frozen", "frozen"], outcomes

        engine = create_engine(database_url)
        with Session(engine) as session:
            events = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "estimate_version.frozen")
                .where(AuditEvent.object_id == version_id)
            )
        engine.dispose()
        assert events == 1, f"{events} événements de gel pour une seule version"
