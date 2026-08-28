"""PostgreSQL doit refuser lui-même un lien entre deux organisations.

Neuf relations de la Phase 1 laissaient la base indifférente à l'organisation
du parent. Mesuré avant ce travail, sur PostgreSQL 16, dans une base créée pour
l'expérience : **neuf sur neuf acceptées** par des `INSERT` directs. Un poste
pouvait pointer le prix d'un autre tenant, un devis se figer sur la
bibliothèque d'un autre, un métré se rattacher au projet d'un autre.

Les routes tenaient la frontière — elles répondent 404 — mais rien d'autre. Un
script d'exploitation, une correction manuelle en base, un import mal écrit
passaient au travers, et le calcul de prix produisait alors un montant tiré des
tarifs de quelqu'un d'autre, sans que rien ne le signale.

Ces tests exigent PostgreSQL : SQLite n'applique pas les clés étrangères sans
`PRAGMA foreign_keys=ON`, et ce n'est pas lui l'arbitre.

Chaque cas construit un graphe **entièrement valide** par l'ORM — `kind`,
`status`, `component_type`, `confidence` compris — pour que le seul refus
possible soit celui de la contrainte multi-tenant, et le vérifie par son nom.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _graph(session: Session, models: Any, name: str) -> dict[str, Any]:
    """Un jeu complet et valide de lignes appartenant à UNE organisation."""
    org = models.Organization(name=name)
    session.add(org)
    session.flush()

    project = models.Project(organization_id=org.id, reference=f"P-{name}", name=f"Projet {name}")
    book = models.PriceBook(organization_id=org.id, name=f"Bibliothèque {name}")
    session.add_all([project, book])
    session.flush()

    boq = models.BillOfQuantities(
        organization_id=org.id, project_id=project.id, name=f"Métré {name}"
    )
    version = models.PriceBookVersion(organization_id=org.id, price_book_id=book.id)
    session.add_all([boq, version])
    session.flush()

    price = models.PriceItem(
        organization_id=org.id,
        price_book_version_id=version.id,
        code=f"C-{name}",
        label=f"Poste {name}",
        unit_code="m3",
        unit_price=Decimal("10.00"),
    )
    composite = models.CompositePriceRow(
        organization_id=org.id,
        price_book_version_id=version.id,
        code=f"S-{name}",
        label=f"Sous-détail {name}",
        unit_code="m3",
    )
    session.add_all([price, composite])
    session.flush()

    return {
        "org": org.id,
        "project": project.id,
        "boq": boq.id,
        "book": book.id,
        "version": version.id,
        "price": price.id,
        "composite": composite.id,
    }


#: Les neuf relations, et le nom de la contrainte qui doit les tenir.
#: (clé, modèle enfant, champ portant le parent, clé du graphe parent, contrainte)
RELATIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "boq_items.price_item_id",
        "BoqItem",
        "price_item_id",
        "price",
        "fk_boq_items_price_item_tenant",
    ),
    ("boq_items.boq_id", "BoqItem", "boq_id", "boq", "fk_boq_items_boq_tenant"),
    (
        "boq_items.composite_price_id",
        "BoqItem",
        "composite_price_id",
        "composite",
        "fk_boq_items_composite_price_tenant",
    ),
    (
        "bills_of_quantities.project_id",
        "BillOfQuantities",
        "project_id",
        "project",
        "fk_bills_of_quantities_project_tenant",
    ),
    ("estimates.boq_id", "Estimate", "boq_id", "boq", "fk_estimates_boq_tenant"),
    (
        "estimates.price_book_version_id",
        "Estimate",
        "price_book_version_id",
        "version",
        "fk_estimates_price_book_version_tenant",
    ),
    (
        "price_items.price_book_version_id",
        "PriceItem",
        "price_book_version_id",
        "version",
        "fk_price_items_price_book_version_tenant",
    ),
    (
        "composite_components.price_item_id",
        "CompositeComponentRow",
        "price_item_id",
        "price",
        "fk_composite_components_price_item_tenant",
    ),
    (
        "price_book_versions.price_book_id",
        "PriceBookVersion",
        "price_book_id",
        "book",
        "fk_price_book_versions_price_book_tenant",
    ),
)


def _child(models: Any, kind: str, own: dict[str, Any], field: str, parent_id: str) -> Any:
    """Une ligne enfant valide de bout en bout, dont un seul champ est détourné.

    Toutes les autres valeurs franchissent les contraintes CHECK du dépôt :
    `kind`, `status`, `component_type` et `confidence` portent des valeurs
    réelles. Sans cela, un refus pourrait venir d'ailleurs et la preuve ne
    porterait sur rien.
    """
    base: dict[str, Any] = {"organization_id": own["org"]}
    if kind == "BoqItem":
        base |= {
            "boq_id": own["boq"],
            "position": f"1.{abs(hash(field)) % 900 + 10}",
            "designation": "ligne",
            "unit_code": "m3",
            "quantity": Decimal("1"),
            "kind": "item",
            "status": "proposed",
        }
    elif kind == "BillOfQuantities":
        base |= {"project_id": own["project"], "name": "métré"}
    elif kind == "Estimate":
        base |= {
            "project_id": own["project"],
            "boq_id": own["boq"],
            "price_book_version_id": own["version"],
            "name": "devis",
        }
    elif kind == "PriceItem":
        base |= {
            "price_book_version_id": own["version"],
            "code": f"X{abs(hash(field)) % 9000 + 1000}",
            "label": "prix",
            "unit_code": "m3",
            "unit_price": Decimal("1.00"),
            "confidence": "declared",
        }
    elif kind == "CompositeComponentRow":
        base |= {
            "composite_price_id": own["composite"],
            "component_type": "consumption",
            "label": "composant",
        }
    elif kind == "PriceBookVersion":
        # Un numéro distinct : `uq_pbv_book_number` refuserait un doublon, et
        # ce refus-là n'aurait rien à voir avec le tenant.
        base |= {"price_book_id": own["book"], "version_number": 7}
    base[field] = parent_id
    return getattr(models, kind)(**base)


@pytest.fixture()
def two_tenants(migrated: None) -> Any:
    from metreo_api import models
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        alpha = _graph(session, models, "Alpha")
        beta = _graph(session, models, "Beta")
        session.commit()
        yield session, models, alpha, beta
    finally:
        session.rollback()
        session.close()


class TestTheDatabaseRefusesACrossTenantParent:
    @pytest.mark.parametrize(
        ("label", "child", "field", "parent_key", "constraint"),
        RELATIONS,
        ids=[relation[0] for relation in RELATIONS],
    )
    def test_a_parent_of_the_same_tenant_is_accepted(
        self, two_tenants: Any, label: str, child: str, field: str, parent_key: str, constraint: str
    ) -> None:
        session, models, alpha, _ = two_tenants
        row = _child(models, child, alpha, field, alpha[parent_key])
        session.add(row)
        session.flush()
        assert row.id, label
        session.rollback()

    @pytest.mark.parametrize(
        ("label", "child", "field", "parent_key", "constraint"),
        RELATIONS,
        ids=[relation[0] for relation in RELATIONS],
    )
    def test_a_parent_of_another_tenant_is_refused_by_the_named_constraint(
        self, two_tenants: Any, label: str, child: str, field: str, parent_key: str, constraint: str
    ) -> None:
        session, models, alpha, beta = two_tenants
        row = _child(models, child, alpha, field, beta[parent_key])
        session.add(row)
        with pytest.raises(IntegrityError) as raised:
            session.flush()
        message = str(raised.value)
        if session.bind.dialect.name == "postgresql":
            # PostgreSQL nomme la contrainte fautive : on exige la bonne, sans
            # quoi un refus venu d'ailleurs passerait pour une preuve.
            assert constraint in message, (
                f"{label} : refusé, mais pas par la contrainte attendue. "
                f"Attendu « {constraint} », obtenu : {message.splitlines()[0][:160]}"
            )
        else:
            # SQLite applique la clé — `PRAGMA foreign_keys=ON` est posé par
            # `db.py` — mais ne la nomme pas. Le refus est réel ; seule
            # l'imputation manque.
            assert "FOREIGN KEY constraint failed" in message, message
        session.rollback()

    def test_a_valid_row_stays_readable_after_the_constraints_exist(self, two_tenants: Any) -> None:
        """Les contraintes n'ont pas rendu illisible ce qui était déjà là."""
        session, models, alpha, beta = two_tenants
        for tenant in (alpha, beta):
            price = session.scalars(
                select(models.PriceItem).where(models.PriceItem.id == tenant["price"])
            ).one()
            assert price.organization_id == tenant["org"]
            assert price.unit_price == Decimal("10.0000000000")


class TestTheConstraintsAreDeclaredWhereTheyShouldBe:
    """Le schéma réellement posé porte les contraintes, pas seulement le modèle."""

    def test_every_parent_carries_the_tenant_unique_index(self, migrated: None) -> None:
        from metreo_api.db import get_engine

        expected = {
            "projects": "uq_projects_id_organization",
            "bills_of_quantities": "uq_bills_of_quantities_id_organization",
            "price_books": "uq_price_books_id_organization",
            "price_book_versions": "uq_price_book_versions_id_organization",
            "price_items": "uq_price_items_id_organization",
            "composite_prices": "uq_composite_prices_id_organization",
        }
        inspector = inspect(get_engine())
        missing = {}
        for table, name in expected.items():
            found = {constraint["name"] for constraint in inspector.get_unique_constraints(table)}
            if name not in found:
                missing[table] = sorted(found)
        assert missing == {}, f"unicités (id, organization_id) manquantes : {missing}"

    def test_every_relation_carries_its_composite_foreign_key(self, migrated: None) -> None:
        from metreo_api.db import get_engine

        inspector = inspect(get_engine())
        by_table: dict[str, set[str]] = {}
        for _, child, _, _, constraint in RELATIONS:
            from metreo_api import models

            table = getattr(models, child).__tablename__
            by_table.setdefault(table, set()).add(constraint)

        missing = {}
        for table, expected in by_table.items():
            found = {key["name"] for key in inspector.get_foreign_keys(table)}
            absent = expected - found
            if absent:
                missing[table] = sorted(absent)
        assert missing == {}, f"clés étrangères composites manquantes : {missing}"

    def test_the_composite_keys_reference_the_organization_column(self, migrated: None) -> None:
        """Une FK composite qui ne porterait pas `organization_id` ne prouverait rien."""
        from metreo_api import models
        from metreo_api.db import get_engine

        inspector = inspect(get_engine())
        wrong = {}
        for _, child, _, _, constraint in RELATIONS:
            table = getattr(models, child).__tablename__
            for key in inspector.get_foreign_keys(table):
                if key["name"] != constraint:
                    continue
                if "organization_id" not in key["constrained_columns"]:
                    wrong[constraint] = key["constrained_columns"]
                if "organization_id" not in key["referred_columns"]:
                    wrong[constraint] = key["referred_columns"]
        assert wrong == {}, f"clés composites ne portant pas l'organisation : {wrong}"


class TestNoIdentifierOfTheOtherTenantLeaks:
    """Un message d'erreur ne doit pas révéler l'identifiant d'en face."""

    def test_the_api_refusal_never_echoes_the_other_tenants_id(self, seeded_client: Any) -> None:
        from .conftest import login

        alpha = login(seeded_client, "admin@dubois.demo")
        beta = login(seeded_client, "admin@janssens.demo")
        beta_project = seeded_client.post(
            "/api/v1/projects", headers=beta, json={"reference": "P-B", "name": "Projet B"}
        )
        assert beta_project.status_code == 201
        secret_id = beta_project.json()["id"]

        response = seeded_client.post(
            f"/api/v1/projects/{secret_id}/boqs", headers=alpha, json={"name": "croisé"}
        )
        assert response.status_code == 404
        body = response.text
        # L'identifiant demandé est repris — c'est celui que l'appelant a fourni.
        # Ce qui ne doit pas sortir, c'est le reste : nom, référence, organisation.
        assert "Projet B" not in body, body
        assert "P-B" not in body, body
        for forbidden in ("organization", "organisation_id", "owner"):
            assert forbidden not in body.lower() or "not_found" in body, body


class TestTheDataStaysUntouched:
    """La migration ne répare rien : elle refuse, ou elle passe."""

    def test_no_row_was_reassigned_to_another_organization(self, two_tenants: Any) -> None:
        session, _, alpha, beta = two_tenants
        counts = session.execute(
            text(
                "SELECT organization_id, count(*) FROM price_items "
                "WHERE organization_id IN (:a, :b) GROUP BY organization_id"
            ),
            {"a": alpha["org"], "b": beta["org"]},
        ).all()
        assert dict(counts) == {alpha["org"]: 1, beta["org"]: 1}
