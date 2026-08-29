"""Le premier compte d'un déploiement neuf.

Sans cette commande, une installation vierge n'a aucun utilisateur : le
fournisseur d'identité authentifie quelqu'un que l'application ne connaît pas,
et le refuse — correctement, mais définitivement.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from metreo_api.bootstrap import bootstrap
from metreo_api.models import Membership, Organization, OrganizationSettings, User


def _session():
    from metreo_api.db import get_session_factory

    return get_session_factory()()


def test_it_creates_the_organisation_its_admin_and_the_link(migrated: None) -> None:
    with _session() as session:
        organisation, utilisateur, cree = bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="Admin@Example.Invalid",
            admin_full_name="Première administratrice",
        )
        session.commit()

        assert cree is True
        # L'adresse est normalisée : sinon deux comptes pour la même personne.
        assert utilisateur.email == "admin@example.invalid"
        appartenance = session.scalars(
            select(Membership).where(Membership.user_id == utilisateur.id)
        ).one()
        assert appartenance.role == "org_admin"
        assert appartenance.is_active is True
        # Les réglages sont indispensables au moteur : une organisation sans
        # eux échoue au premier chiffrage, pas à sa création.
        assert (
            session.scalars(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == organisation.id
                )
            ).one()
            is not None
        )


def test_it_is_idempotent(migrated: None) -> None:
    """Rejouée, elle ne duplique rien : c'est ce qui permet de l'automatiser."""
    with _session() as session:
        bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="admin@example.invalid",
            admin_full_name="Première administratrice",
        )
        session.commit()
        organisations = len(session.scalars(select(Organization)).all())
        utilisateurs = len(session.scalars(select(User)).all())

        _, _, cree = bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="admin@example.invalid",
            admin_full_name="Première administratrice",
        )
        session.commit()

        assert cree is False
        assert len(session.scalars(select(Organization)).all()) == organisations
        assert len(session.scalars(select(User)).all()) == utilisateurs
        assert len(session.scalars(select(Membership)).all()) == 1


def test_it_reactivates_a_membership_that_was_switched_off(migrated: None) -> None:
    """Relancer le bootstrap est le geste naturel pour se débloquer."""
    with _session() as session:
        _, utilisateur, _ = bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="admin@example.invalid",
            admin_full_name="A",
        )
        session.commit()
        appartenance = session.scalars(
            select(Membership).where(Membership.user_id == utilisateur.id)
        ).one()
        appartenance.is_active = False
        session.commit()

        _, _, cree = bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="admin@example.invalid",
            admin_full_name="A",
        )
        session.commit()
        assert cree is True
        session.refresh(appartenance)
        assert appartenance.is_active is True


def test_it_creates_no_password_and_no_demonstration_data(migrated: None) -> None:
    """Aucun mot de passe, aucune donnée fictive.

    Le jeu de démonstration porte une entreprise qui n'existe pas ; il n'a
    rien à faire dans un déploiement réel, et `seed --reset` effacerait le
    travail des utilisateurs.
    """
    with _session() as session:
        bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email="admin@example.invalid",
            admin_full_name="A",
        )
        session.commit()

        utilisateur = session.scalars(select(User)).one()
        assert not any(
            "password" in colonne.name or "hash" in colonne.name
            for colonne in User.__table__.columns
        ), "le modèle utilisateur ne doit porter aucun secret d'authentification"
        assert utilisateur.is_active is True

        organisations = session.scalars(select(Organization)).all()
        assert len(organisations) == 1
        assert organisations[0].name == "Organisation initiale"
        # Rien de « demo » n'a été semé.
        from metreo_api.models import Project

        assert session.scalars(select(Project)).all() == []


@pytest.mark.parametrize(
    "email",
    ["", "   ", "pas-une-adresse"],
)
def test_an_invalid_administrator_address_is_refused(migrated: None, email: str) -> None:
    with _session() as session, pytest.raises(ValueError):
        bootstrap(
            session,
            organization_name="Organisation initiale",
            admin_email=email,
            admin_full_name="A",
        )


def test_an_empty_organisation_name_is_refused(migrated: None) -> None:
    with _session() as session, pytest.raises(ValueError):
        bootstrap(
            session,
            organization_name="   ",
            admin_email="admin@example.invalid",
            admin_full_name="A",
        )
