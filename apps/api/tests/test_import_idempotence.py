"""Un import validé deux fois n'est appliqué qu'une fois.

`commit_import` lisait le statut du lot **avant** de prendre le verrou et ne le
relisait jamais. Deux requêtes franchissaient la garde ensemble ; la seconde
attendait le verrou de version, puis rejouait l'import sur son objet resté
« previewed » en mémoire. Un double clic suffisait : `committed_at` écrasé,
deux événements `price_import.committed` pour un seul lot, et selon la
stratégie des prix réécrits une seconde fois.

L'identifiant d'idempotence est le lot lui-même : son statut, relu sous le
verrou, décide. Ces tests exigent un vrai PostgreSQL — sur SQLite les deux
requêtes se sérialisent d'elles-mêmes et ne prouveraient rien.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from .conftest import login, running_on_postgresql

pytestmark = pytest.mark.skipif(
    not running_on_postgresql(),
    reason="La course ne s'observe que sur PostgreSQL ; SQLite sérialise ses écritures.",
)

CSV = (
    "code,label,unit_code,resource_kind,unit_price,currency\n"
    "IDEM-001,Béton idempotent,m3,material,120.50,EUR\n"
    "IDEM-002,Sable idempotent,t,material,35.00,EUR\n"
)


def _previewed_batch(client: TestClient, headers: dict[str, str], version_id: str) -> str:
    response = client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("prix.csv", CSV, "text/csv")},
        data={"strategy": "create"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["batch_id"])


class TestDoubleClick:
    def test_committing_twice_applies_the_import_once(
        self, seeded: dict[str, str], seeded_client: TestClient
    ) -> None:
        """Deux validations successives : la seconde est refusée, pas rejouée."""
        headers = login(seeded_client, "admin@dubois.demo")
        version_id = seeded["price_book_version_a"]
        batch_id = _previewed_batch(seeded_client, headers, version_id)

        first = seeded_client.post(
            f"/api/v1/price-books/imports/{batch_id}/commit",
            headers=headers,
            json={"confirm": True, "strategy": "create"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 2, first.json()

        second = seeded_client.post(
            f"/api/v1/price-books/imports/{batch_id}/commit",
            headers=headers,
            json={"confirm": True, "strategy": "create"},
        )
        # Déterministe, et jamais un 500.
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "batch_not_pending", second.json()

        from metreo_api.db import get_session_factory
        from metreo_api.models import AuditEvent, PriceItem

        session = get_session_factory()()
        try:
            prices = session.scalar(
                select(func.count())
                .select_from(PriceItem)
                .where(PriceItem.code.in_(["IDEM-001", "IDEM-002"]))
            )
            events = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "price_import.committed")
                .where(AuditEvent.object_id == batch_id)
            )
        finally:
            session.close()
        assert prices == 2, f"{prices} prix pour un import de deux lignes"
        assert events == 1, f"{events} événements d'audit pour une seule validation"


class TestConcurrentCommits:
    def test_two_concurrent_commits_leave_one_logical_winner(
        self, seeded: dict[str, str], seeded_client: TestClient, database_url: str
    ) -> None:
        """Deux requêtes simultanées passant par la VRAIE route.

        Une première version réimplémentait ici la séquence verrou-relecture :
        elle testait `lock_owned`, pas `commit_import`. Retirer la relecture
        sous verrou de la route la laissait verte — le même défaut que celui
        rencontré sur les postes approuvés. La route est donc appelée
        directement, avec de vraies sessions.
        """
        from fastapi import HTTPException
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from metreo_api.models import (
            AuditEvent,
            Membership,
            Organization,
            PriceItem,
            User,
        )
        from metreo_api.routers.pricebooks import commit_import
        from metreo_api.schemas import ImportCommitRequest
        from metreo_api.security.auth import TenantContext

        headers = login(seeded_client, "admin@dubois.demo")
        version_id = seeded["price_book_version_a"]
        batch_id = _previewed_batch(seeded_client, headers, version_id)
        organization_id = seeded["organization_a"]

        barrier = threading.Barrier(2)
        outcomes: dict[int, Any] = {}

        def commit(index: int) -> None:
            engine = create_engine(database_url)
            try:
                with Session(engine) as session:
                    organization = session.get(Organization, organization_id)
                    assert organization is not None
                    membership = session.scalars(
                        select(Membership)
                        .where(Membership.organization_id == organization_id)
                        .limit(1)
                    ).one()
                    user = session.get(User, membership.user_id)
                    assert user is not None
                    context = TenantContext(
                        user=user, organization=organization, membership=membership
                    )
                    barrier.wait(timeout=20)
                    try:
                        commit_import(
                            batch_id,
                            ImportCommitRequest(confirm=True, strategy="create"),
                            context,
                            session,
                        )
                    except HTTPException as exc:
                        session.rollback()
                        detail = exc.detail
                        outcomes[index] = str(
                            detail.get("code") if isinstance(detail, dict) else detail
                        )
                        return
                    session.commit()
                    outcomes[index] = "committed"
            except BaseException as exc:
                outcomes[index] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
            finally:
                engine.dispose()

        threads = [threading.Thread(target=commit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(thread.is_alive() for thread in threads)

        # Un gagnant logique, une réponse déterministe pour l'autre, jamais 500.
        assert sorted(outcomes.values()) == ["batch_not_pending", "committed"], outcomes

        engine = create_engine(database_url)
        with Session(engine) as session:
            prices = session.scalar(
                select(func.count())
                .select_from(PriceItem)
                .where(PriceItem.code.in_(["IDEM-001", "IDEM-002"]))
            )
            events = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "price_import.committed")
                .where(AuditEvent.object_id == batch_id)
            )
        engine.dispose()
        assert prices == 2, f"{prices} prix : l'import a été appliqué deux fois"
        assert events == 1, f"{events} événements d'audit pour un seul lot"
