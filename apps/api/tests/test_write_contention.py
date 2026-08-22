"""Deux écritures concurrentes dans la même organisation ne s'interbloquent pas.

Trouvé par une relecture adversariale du correctif du bloquant J, et non par
la revue de ce correctif : le verrou d'audit lui-même créait un interblocage.

`audit.record` prenait un `FOR UPDATE` sur la ligne `organizations` pour
sérialiser l'allocation de la séquence. Mais toute insertion d'une ligne
portant `organization_id` fait vérifier la clé étrangère par PostgreSQL, ce
qui prend un `FOR KEY SHARE` sur cette même ligne — un verrou faible, que deux
transactions obtiennent ensemble. Chacune demandait ensuite le `FOR UPDATE`,
incompatible avec le `FOR KEY SHARE` de l'autre : montée de verrou croisée,
cycle, et PostgreSQL en tue une avec `40P01 deadlock detected`.

Rien de métier n'était en jeu : deux créations de prix dans **deux versions
différentes** suffisaient, et une route sans aucun verrou métier — la création
de projet — le produisait tout autant. Le service rendu était un HTTP 500.

La correction tient au mode de verrou : `FOR NO KEY UPDATE` sérialise entre
eux les allocateurs de séquence sans entrer en conflit avec le `FOR KEY SHARE`
des vérifications de clé étrangère. La table de compatibilité de PostgreSQL
est explicite : `FOR KEY SHARE` ne s'oppose qu'à `FOR UPDATE`.

Ces tests exigent un vrai PostgreSQL : SQLite ne connaît ni ces modes de
verrou ni les interblocages qu'ils produisent.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from .conftest import login, running_on_postgresql

pytestmark = pytest.mark.skipif(
    not running_on_postgresql(),
    reason="L'interblocage ne s'observe que sur PostgreSQL ; SQLite n'a pas ces verrous.",
)


def hammer(work: Callable[[int], Any], *, threads: int, waves: int) -> list[Any]:
    """Run ``work`` under plain concurrent load.

    **Ce que cette charge ne prouve pas.** Vérifié : les trois tests qui
    l'utilisent restent verts avec le `FOR UPDATE` fautif rétabli. La fenêtre
    est trop étroite pour qu'une charge de cette taille la rencontre de façon
    fiable. Ils gardent leur place comme filet de sécurité sur les routes
    réelles — un 500 sous charge reste un 500 — mais **seul le test à barrière
    ci-dessus démontre que l'interblocage est fermé**, et c'est lui qui tombe
    si le mode de verrou régresse.

    Le dire ici plutôt que de laisser croire l'inverse : une assurance qu'un
    test ne fournit pas est pire que son absence, parce qu'on s'y fie.
    """
    results: list[Any] = []
    guard = threading.Lock()

    def target(index: int) -> None:
        for wave in range(waves):
            outcome = work(index * waves + wave)
            with guard:
                results.append(outcome)

    workers = [threading.Thread(target=target, args=(index,)) for index in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=120)
    assert not any(worker.is_alive() for worker in workers), "un fil ne s'est pas terminé"
    return results


class TestTheAuditLockDoesNotDeadlock:
    """Reproduction déterministe, à travers la couche service réelle.

    La charge seule ne suffit pas : la fenêtre est étroite et un essai qui
    passe ne prouve rien. La barrière place les deux transactions exactement
    dans l'état qui déclenche la montée de verrou — insertion faite, donc
    `FOR KEY SHARE` détenu de part et d'autre — avant que l'audit ne demande
    son verrou.
    """

    def test_two_independent_business_writes_do_not_deadlock(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        """Le défaut le plus grave de tout ce cycle, sous sa forme nominative.

        Deux écritures métier **sans rapport** — un projet d'un côté, un prix
        de l'autre, tables différentes, aucune relation entre elles — ne
        partageant que leur organisation. Elles s'interbloquaient : l'insertion
        prend un `FOR KEY SHARE` sur la ligne `organizations` par vérification
        de clé étrangère, `audit.record` demandait ensuite un `FOR UPDATE` sur
        cette même ligne, et les deux montées croisées formaient un cycle.
        PostgreSQL en tuait une avec `40P01`, la route rendait un HTTP 500.

        La barrière place les deux transactions exactement dans l'état qui
        déclenche la montée : insertion faite de part et d'autre, donc
        `FOR KEY SHARE` détenu des deux côtés, avant que l'audit ne demande son
        verrou. Rétablir `FOR UPDATE` rend ce test rouge de façon
        déterministe.
        """
        from sqlalchemy import create_engine, func, select
        from sqlalchemy.orm import Session

        from metreo_api.models import AuditEvent, PriceBookVersion, PriceItem, Project
        from metreo_api.services import audit

        organization_id = seeded["organization_a"]
        version_id = seeded["price_book_version_a"]
        barrier = threading.Barrier(2)
        outcomes: dict[str, Any] = {}

        def write_project(session: Session) -> None:
            session.add(
                Project(
                    organization_id=organization_id,
                    reference="INDEP-PROJET",
                    name="Chantier indépendant",
                )
            )

        def write_price(session: Session) -> None:
            session.add(
                PriceItem(
                    organization_id=organization_id,
                    price_book_version_id=version_id,
                    code="INDEP-PRIX",
                    label="Prix indépendant",
                    unit_code="m3",
                    resource_kind="material",
                    unit_price="1.0000000000",
                    currency="EUR",
                    confidence="declared",
                )
            )

        def run(name: str, write: Callable[[Session], None]) -> None:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    write(session)
                    # L'insertion fait vérifier la clé étrangère vers
                    # organizations : PostgreSQL y prend un FOR KEY SHARE, que
                    # les deux transactions obtiennent ensemble.
                    session.flush()
                    barrier.wait(timeout=20)
                    # Puis l'audit demande son verrou sur la même ligne.
                    audit.record(
                        session,
                        organization_id=organization_id,
                        action=f"test.independent.{name}",
                        object_type="test",
                        summary=f"Écriture indépendante — {name}",
                    )
                    session.commit()
                    outcomes[name] = "ok"
            except BaseException as exc:
                outcomes[name] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:140]}"
            finally:
                engine.dispose()

        threads = [
            threading.Thread(target=run, args=("projet", write_project)),
            threading.Thread(target=run, args=("prix", write_price)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        # 5. Les deux terminent.
        assert not any(thread.is_alive() for thread in threads), "un fil ne s'est pas terminé"
        # 6. Aucun interblocage.
        deadlocks = {
            name: value
            for name, value in outcomes.items()
            if isinstance(value, str) and "Deadlock" in value
        }
        assert deadlocks == {}, f"interblocage 40P01 : {deadlocks}"
        # 7. Aucune erreur du tout — une route en rendrait un HTTP 500.
        assert outcomes == {"projet": "ok", "prix": "ok"}, outcomes

        # 8. Les deux événements d'audit existent, avec des séquences distinctes.
        engine = create_engine(database_url)
        with Session(engine) as session:
            actions = set(
                session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.action.in_(["test.independent.projet", "test.independent.prix"])
                    )
                ).all()
            )
            sequences = session.scalar(
                select(func.count(func.distinct(AuditEvent.sequence))).where(
                    AuditEvent.organization_id == organization_id
                )
            )
            total = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == organization_id)
            )
            written = session.scalar(
                select(func.count()).select_from(PriceItem).where(PriceItem.code == "INDEP-PRIX")
            )
        engine.dispose()
        assert actions == {"test.independent.projet", "test.independent.prix"}, actions
        assert total == sequences, f"{total} événements pour {sequences} séquences distinctes"
        assert written == 1
        assert version_id  # la version semée existe bien
        assert PriceBookVersion  # importée pour la lisibilité du scénario

    def test_the_sequence_is_still_serialised(
        self, seeded: dict[str, str], database_url: str
    ) -> None:
        """Le verrou plus faible doit encore empêcher deux séquences identiques.

        `FOR NO KEY UPDATE` s'oppose à lui-même : c'est ce qui fait qu'il
        remplace `FOR UPDATE` sans rien perdre. Si on l'avait affaibli jusqu'à
        `FOR KEY SHARE`, ce test tomberait sur `uq_audit_org_sequence`.
        """
        from sqlalchemy import create_engine, func, select
        from sqlalchemy.orm import Session

        from metreo_api.models import AuditEvent
        from metreo_api.services import audit

        organization_id = seeded["organization_a"]
        barrier = threading.Barrier(4)
        outcomes: list[Any] = []
        guard = threading.Lock()

        def write(index: int) -> None:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    barrier.wait(timeout=20)
                    audit.record(
                        session,
                        organization_id=organization_id,
                        action="test.sequence",
                        object_type="test",
                        summary=f"Séquence {index}",
                    )
                    session.commit()
                    with guard:
                        outcomes.append("ok")
            except BaseException as exc:
                with guard:
                    outcomes.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:100]}")
            finally:
                engine.dispose()

        threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert all(value == "ok" for value in outcomes), outcomes
        session_factory = create_engine(database_url)
        with Session(session_factory) as session:
            total = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == organization_id)
            )
            distinct = session.scalar(
                select(func.count(func.distinct(AuditEvent.sequence))).where(
                    AuditEvent.organization_id == organization_id
                )
            )
        session_factory.dispose()
        assert total == distinct, f"{total} événements pour {distinct} séquences distinctes"


class TestConcurrentWritesInOneOrganization:
    def test_creating_prices_in_parallel_never_returns_a_server_error(
        self, seeded: dict[str, str], seeded_client: TestClient
    ) -> None:
        """Deux versions différentes : aucune contention métier, donc aucun 500."""
        from metreo_api.db import get_session_factory
        from metreo_api.models import PriceBook, PriceBookVersion

        headers = login(seeded_client, "admin@dubois.demo")
        session = get_session_factory()()
        try:
            book = session.scalars(
                select(PriceBook)
                .where(PriceBook.organization_id == seeded["organization_a"])
                .limit(1)
            ).one()
            organization_id = book.organization_id
            versions = [
                PriceBookVersion(
                    organization_id=organization_id,
                    price_book_id=book.id,
                    version_number=900 + index,
                    status="draft",
                )
                for index in range(2)
            ]
            session.add_all(versions)
            session.commit()
            version_ids = [version.id for version in versions]
        finally:
            session.close()

        def create(index: int) -> int:
            response = seeded_client.post(
                f"/api/v1/price-books/versions/{version_ids[index % 2]}/items",
                headers=headers,
                json={
                    "code": f"CONC-{index:04d}",
                    "label": "Écriture concurrente",
                    "unit_code": "m3",
                    "resource_kind": "material",
                    "unit_price": "12.5",
                },
            )
            return response.status_code

        codes = hammer(create, threads=8, waves=12)
        assert 500 not in codes, (
            f"écritures concurrentes en erreur serveur : {sorted(set(codes))} — "
            f"{codes.count(500)} sur {len(codes)}"
        )

    def test_creating_projects_in_parallel_never_returns_a_server_error(
        self, seeded: dict[str, str], seeded_client: TestClient
    ) -> None:
        """Une route sans aucun verrou métier : seul l'audit y verrouille."""
        headers = login(seeded_client, "admin@dubois.demo")

        def create(index: int) -> int:
            response = seeded_client.post(
                "/api/v1/projects",
                headers=headers,
                json={
                    "reference": f"CONC-{index:04d}",
                    "name": f"Chantier concurrent {index}",
                },
            )
            return response.status_code

        codes = hammer(create, threads=6, waves=10)
        assert 500 not in codes, (
            f"créations concurrentes en erreur serveur : {sorted(set(codes))} — "
            f"{codes.count(500)} sur {len(codes)}"
        )

    def test_the_audit_chain_survives_the_load(
        self, seeded: dict[str, str], seeded_client: TestClient
    ) -> None:
        """Sans interblocage, la chaîne reste continue et vérifiable."""
        headers = login(seeded_client, "admin@dubois.demo")

        def create(index: int) -> int:
            return seeded_client.post(
                "/api/v1/projects",
                headers=headers,
                json={"reference": f"CHAIN-{index:04d}", "name": f"Chantier {index}"},
            ).status_code

        hammer(create, threads=4, waves=5)
        response = seeded_client.get("/api/v1/audit/verify", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["valid"] is True, response.json()
