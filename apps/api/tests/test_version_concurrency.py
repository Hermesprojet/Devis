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


class TestDocumentRevisionNumbering:
    def test_four_simultaneous_creations_get_distinct_contiguous_numbers(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        from metreo_api.models import Document, DocumentRevision, Project
        from metreo_api.services import documents

        engine = create_engine(database_url)
        with Session(engine) as session:
            project = session.scalars(select(Project).limit(1)).one()
            document = Document(
                organization_id=project.organization_id,
                project_id=project.id,
                title="Document concurrent",
            )
            session.add(document)
            session.commit()
            document_id = document.id
            organization_id = document.organization_id
        engine.dispose()

        def create(session: Session, barrier: threading.Barrier) -> int:
            barrier.wait(timeout=20)
            number = documents.next_revision_number(
                session,
                organization_id=organization_id,
                document_id=document_id,
            )
            session.add(
                DocumentRevision(
                    organization_id=organization_id,
                    document_id=document_id,
                    revision_number=number,
                    sha256=f"{number:064x}",
                    byte_size=number,
                    media_type="application/pdf",
                    storage_key=f"concurrency/{document_id}/{number}",
                    original_filename=f"revision-{number}.pdf",
                    status="draft",
                )
            )
            session.commit()
            return number

        outcomes = run_in_parallel(create, database_url, threads=4)
        errors = [item for item in outcomes if isinstance(item, BaseException)]
        assert errors == [], f"une création de révision a échoué : {errors}"
        assert sorted(outcomes) == [1, 2, 3, 4], outcomes

        engine = create_engine(database_url)
        with Session(engine) as session:
            persisted = list(
                session.scalars(
                    select(DocumentRevision.revision_number)
                    .where(
                        DocumentRevision.organization_id == organization_id,
                        DocumentRevision.document_id == document_id,
                    )
                    .order_by(DocumentRevision.revision_number)
                ).all()
            )
        engine.dispose()
        assert persisted == [1, 2, 3, 4]


class TestNeighbouringRaces:
    """Deux courses voisines, à vérifier avant de conclure."""

    @pytest.mark.parametrize("first", ["publication", "écriture"])
    def test_publishing_and_writing_a_price_are_sequential(
        self, seeded: dict[str, str], database_url: str, first: str
    ) -> None:
        """Deux ordres imposés, pas une course dont on devine l'issue.

        La version précédente déduisait l'ordre des transactions de
        `time.monotonic()` appelé **après** le retour de `commit()`. Un fil
        peut être suspendu entre la validation en base et cet appel : l'ordre
        des horodatages Python n'est donc pas l'ordre réel des commits. Cela
        autorisait aussi bien un faux positif qu'un échec intermittent.

        Ici l'ordre est **imposé** par des `Event`. Le gagnant prend le verrou,
        signale, et ne valide qu'une fois le perdant lancé : le perdant doit
        donc réellement attendre. On ne déduit plus rien d'un horodatage — on
        observe ce que chaque transaction a lu avant d'écrire.

        Portée exacte des deux scénarios, mesurée en neutralisant le verrou :
        « publication d'abord » tombe alors cinq fois sur cinq, parce que
        l'écriture lit `draft` au lieu de `published` et crée un prix dans une
        version publiée. « écriture d'abord » reste vert sans le verrou, et
        c'est normal : l'écriture ne change pas le statut, donc la publication
        lit `draft` dans les deux cas. Ce second scénario atteste le
        comportement métier attendu, pas la présence du verrou.
        """
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        from metreo_api.models import PriceBookVersion, PriceItem
        from metreo_api.services import pricebook_versions

        engine = create_engine(database_url)
        with Session(engine) as session:
            version = session.scalars(
                select(PriceBookVersion).where(PriceBookVersion.status == "draft").limit(1)
            ).one()
            version_id, organization_id = version.id, version.organization_id
        engine.dispose()

        code = f"CONCURRENT-{first[:3]}"
        holds_the_lock = threading.Event()
        loser_attempted = threading.Event()
        observed: dict[str, Any] = {}

        def publish(is_first: bool) -> str:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    if not is_first:
                        # Ouvrir la connexion AVANT la poignée de main : sans
                        # cela, l'établissement de la connexion du perdant
                        # coûtait plus cher que la validation du gagnant, et
                        # masquait la course qu'on cherche à observer.
                        session.execute(text("SELECT 1"))
                        holds_the_lock.wait(timeout=20)
                        loser_attempted.set()
                    locked = pricebook_versions.lock_version(
                        session, organization_id=organization_id, version_id=version_id
                    )
                    observed["publication a lu"] = locked.status
                    if is_first:
                        holds_the_lock.set()
                        # Le gagnant tient le verrou et ne valide qu'une fois
                        # le perdant lancé : sans verrou, celui-ci lirait donc
                        # l'état d'AVANT la validation, et le test tomberait.
                        loser_attempted.wait(timeout=20)
                    if locked.status == "published":
                        session.rollback()
                        return "already_published"
                    locked.status = "published"
                    session.commit()
                    return "published"
            finally:
                engine.dispose()

        def write(is_first: bool) -> str:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    if not is_first:
                        # Ouvrir la connexion AVANT la poignée de main : sans
                        # cela, l'établissement de la connexion du perdant
                        # coûtait plus cher que la validation du gagnant, et
                        # masquait la course qu'on cherche à observer.
                        session.execute(text("SELECT 1"))
                        holds_the_lock.wait(timeout=20)
                        loser_attempted.set()
                    locked = pricebook_versions.lock_version(
                        session, organization_id=organization_id, version_id=version_id
                    )
                    observed["écriture a lu"] = locked.status
                    if is_first:
                        holds_the_lock.set()
                        loser_attempted.wait(timeout=20)
                    if locked.status == "published":
                        session.rollback()
                        return "version_published"
                    session.add(
                        PriceItem(
                            organization_id=organization_id,
                            price_book_version_id=version_id,
                            code=code,
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
            finally:
                engine.dispose()

        results: dict[str, Any] = {}

        def run(name: str, work: Callable[[bool], str], is_first: bool) -> None:
            try:
                results[name] = work(is_first)
            except BaseException as exc:
                results[name] = exc

        publish_first = first == "publication"
        threads = [
            threading.Thread(target=run, args=("publish", publish, publish_first)),
            threading.Thread(target=run, args=("write", write, not publish_first)),
        ]
        # Le gagnant démarre seul ; le perdant n'entre en lice qu'une fois le
        # verrou pris, ce qui garantit qu'il l'attendra.
        for thread in threads if publish_first else reversed(threads):
            thread.start()
        for thread in threads:
            thread.join(timeout=40)
        assert not any(thread.is_alive() for thread in threads)
        assert not any(isinstance(value, BaseException) for value in results.values()), results

        engine = create_engine(database_url)
        with Session(engine) as session:
            final = session.get(PriceBookVersion, version_id)
            assert final is not None
            written = session.scalar(
                select(func.count()).select_from(PriceItem).where(PriceItem.code == code)
            )
            status_final = final.status
        engine.dispose()

        if publish_first:
            # La publication gagne : l'écriture doit avoir LU « published »,
            # donc être refusée, et ne créer aucun prix.
            assert results["publish"] == "published", results
            assert results["write"] == "version_published", results
            assert observed["écriture a lu"] == "published", observed
            assert written == 0, "un prix a été ajouté à une version publiée"
        else:
            # L'écriture gagne : la publication doit avoir LU « draft » puis
            # publier l'état final, prix compris.
            assert results["write"] == "written", results
            assert results["publish"] == "published", results
            assert observed["publication a lu"] == "draft", observed
            assert written == 1, "le prix écrit avant publication a disparu"
        assert status_final == "published", status_final

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


class TestApprovedQuantityLock:
    """Modifier la quantité d'un poste approuvé exige la permission d'approuver.

    `update_item` lisait `item.status` pour décider si la dérogation était
    exigée, puis écrivait sans avoir retenu la ligne. Une approbation
    concurrente s'intercalait : le poste passait à « approuvé » entre la
    lecture et l'écriture, et la quantité était modifiée sans dérogation, sans
    motif, sans `BOQ_APPROVE` et sans l'événement d'audit
    `boq_item.approved_quantity_overridden`. Le journal affirmait alors
    l'approbation d'une quantité qui n'était plus celle du poste.
    """

    def test_the_route_holds_the_row_between_the_read_and_the_write(
        self, seeded: dict[str, str], database_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La propriété, observée dans la fenêtre où elle compte.

        Trois tentatives ont échoué avant celle-ci, et chacune pour une raison
        différente qu'il vaut mieux écrire que réapprendre :

        * la première réimplémentait la séquence au lieu d'appeler la route,
          donc elle testait `lock_owned` et non le correctif — le retirer de
          `routers/boq.py` la laissait verte ;
        * la deuxième passait par la route mais dépendait d'une course : le fil
          d'écriture gagnait à tous les coups, une dizaine de millisecondes
          d'avance mesurée, si bien que l'entrelacement fautif ne survenait
          jamais ;
        * la troisième sondait le verrou *après* le retour de la route, alors
          que l'`UPDATE` lui-même verrouille la ligne : la sonde voyait un
          verrou tenu même sans le correctif.

        La fenêtre à observer est celle qui sépare la lecture du statut de
        l'écriture. `_check_price_links` y est appelé : on s'y greffe pour
        tenter, depuis une autre connexion, le verrou qu'une approbation
        concurrente prendrait. `lock_timeout` transforme l'attente en preuve.
        """
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import OperationalError
        from sqlalchemy.orm import Session

        from metreo_api.models import BoqItem, Membership, Organization, User
        from metreo_api.routers import boq as boq_router
        from metreo_api.schemas import BoqItemUpdate
        from metreo_api.security.auth import TenantContext
        from metreo_api.services.locking import lock_owned

        engine = create_engine(database_url)
        with Session(engine) as session:
            item = session.scalars(select(BoqItem).limit(1)).one()
            item.status = "verified"
            item.quantity = "0.0000000000"
            session.commit()
            item_id, organization_id = item.id, item.organization_id
        engine.dispose()

        observed: dict[str, bool] = {}
        original = boq_router._check_price_links

        def probe(*args: Any, **kwargs: Any) -> Any:
            """Ce qu'une approbation concurrente rencontrerait, ici et maintenant."""
            rival_engine = create_engine(database_url)
            try:
                with Session(rival_engine) as rival:
                    rival.execute(text("SET LOCAL lock_timeout = '400ms'"))
                    try:
                        lock_owned(rival, BoqItem, organization_id, item_id, label="Poste")
                        observed["held"] = False
                    except OperationalError:
                        observed["held"] = True
                        rival.rollback()
            finally:
                rival_engine.dispose()
            return original(*args, **kwargs)

        monkeypatch.setattr(boq_router, "_check_price_links", probe)

        writer_engine = create_engine(database_url)
        try:
            with Session(writer_engine) as writer:
                organization = writer.get(Organization, organization_id)
                assert organization is not None
                membership = writer.scalars(
                    select(Membership).where(Membership.organization_id == organization_id).limit(1)
                ).one()
                user = writer.get(User, membership.user_id)
                assert user is not None
                boq_router.update_item(
                    item_id,
                    BoqItemUpdate(quantity="999"),
                    TenantContext(user=user, organization=organization, membership=membership),
                    writer,
                )
                writer.rollback()
        finally:
            writer_engine.dispose()

        assert observed.get("held") is True, (
            "entre sa lecture du statut et son écriture, la route ne retient pas "
            "la ligne : une approbation concurrente s'y intercale, et la quantité "
            "d'un poste approuvé change sans dérogation ni motif"
        )
