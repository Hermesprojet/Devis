"""Le jeu de démonstration écrit, il ne détruit pas.

`seed` est la seule commande d'écriture qui reste utilisable sur une base de
travail : elle est additive et ses lignes portent `is_demo_data`. Trois
propriétés doivent tenir pour que cette exception reste défendable.

Elle refuse une base de production. Elle n'efface jamais rien qu'elle n'ait
pas semé : `--reset` supprimait **toutes** les organisations et **tous** les
utilisateurs — un DELETE général qui aurait emporté des données réelles sur
une base peuplée. Et un second passage ne double rien.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class TestSeedRefusesProduction:
    @pytest.mark.parametrize("environment", ["production", "staging"])
    def test_seeding_a_production_database_is_refused(
        self, migrated: None, monkeypatch: pytest.MonkeyPatch, environment: str
    ) -> None:
        from metreo_api import config
        from metreo_api.db import get_session_factory
        from metreo_api.seed import SeedRefused, seed

        monkeypatch.setenv("METREO_ENVIRONMENT", environment)
        config.get_settings.cache_clear()
        session = get_session_factory()()
        try:
            with pytest.raises(SeedRefused, match=environment):
                seed(session)
        finally:
            session.close()
            config.get_settings.cache_clear()

    def test_seeding_a_development_database_is_allowed(self, migrated: None) -> None:
        from metreo_api.db import get_session_factory
        from metreo_api.seed import seed

        session = get_session_factory()()
        try:
            assert seed(session)["status"] == "seeded"
        finally:
            session.close()


class TestSeedIsAdditive:
    def test_a_second_pass_changes_nothing(self, migrated: None) -> None:
        from metreo_api.db import get_session_factory
        from metreo_api.models import Organization, PriceItem
        from metreo_api.seed import seed

        session = get_session_factory()()
        try:
            seed(session)
            organizations = session.scalar(select(func.count()).select_from(Organization))
            prices = session.scalar(select(func.count()).select_from(PriceItem))
            assert seed(session)["status"] == "already_seeded"
            assert session.scalar(select(func.count()).select_from(Organization)) == organizations
            assert session.scalar(select(func.count()).select_from(PriceItem)) == prices
        finally:
            session.close()

    def test_every_seeded_price_is_marked_as_demonstration_data(self, migrated: None) -> None:
        from metreo_api.db import get_session_factory
        from metreo_api.models import PriceItem
        from metreo_api.seed import seed

        session = get_session_factory()()
        try:
            seed(session)
            real = session.scalar(
                select(func.count()).select_from(PriceItem).where(~PriceItem.is_demo_data)
            )
        finally:
            session.close()
        assert real == 0, f"{real} prix semés ne sont pas marqués is_demo_data"

    def test_reset_never_touches_a_row_it_did_not_seed(self, migrated: None) -> None:
        """`--reset` supprimait toutes les organisations, pas seulement les siennes."""
        from metreo_api.db import get_session_factory
        from metreo_api.models import Organization, User
        from metreo_api.seed import seed

        session: Session = get_session_factory()()
        try:
            seed(session)
            # Une organisation réelle, qui n'a rien à voir avec la démonstration.
            session.add(
                Organization(
                    name="Entreprise réelle",
                    legal_name="Entreprise réelle SA",
                    region_code="BE-WAL",
                    locale="fr-BE",
                )
            )
            session.commit()

            seed(session, reset=True)
            session.commit()

            survivors = set(session.scalars(select(Organization.name)).all())
            users = session.scalar(select(func.count()).select_from(User))
        finally:
            session.close()
        assert "Entreprise réelle" in survivors, (
            f"une organisation réelle a été supprimée par --reset : {survivors}"
        )
        assert users and users > 0
