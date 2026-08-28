"""Les sept relations de la seconde tranche, éprouvées comme les neuf premières.

Mesuré avant la migration `c9d3a5e71b62`, sur PostgreSQL 16 : **sept sur sept**
acceptaient un parent d'une autre organisation. Aucune n'était atteignable par
une route — les services valident leurs parents par `get_owned` — mais un
script de reprise, un seed ou une correction manuelle passaient au travers.

Chaque ligne est construite par l'ORM pour franchir toutes les autres
contraintes : un seul champ est détourné, celui qui désigne le parent. Un refus
venu d'une contrainte `kind`, `status` ou `component_type` ne prouverait rien
sur le tenant, et c'est exactement l'erreur qu'un premier passage avait faite
sur la tranche précédente.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

#: enfant.colonne, contrainte, et l'action que la composite doit refléter.
RELATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "composite_prices.price_book_version_id",
        "fk_composite_prices_price_book_version_tenant",
        "CASCADE",
    ),
    (
        "composite_components.composite_price_id",
        "fk_composite_components_composite_price_tenant",
        "CASCADE",
    ),
    (
        "import_batches.price_book_version_id",
        "fk_import_batches_price_book_version_tenant",
        "CASCADE",
    ),
    ("boq_items.parent_id", "fk_boq_items_parent_tenant", "CASCADE"),
    ("estimates.project_id", "fk_estimates_project_tenant", "CASCADE"),
    ("estimate_versions.estimate_id", "fk_estimate_versions_estimate_tenant", "CASCADE"),
    (
        "estimate_versions.price_book_version_id",
        "fk_estimate_versions_price_book_version_tenant",
        "NO ACTION",
    ),
)


def _graph(session: Any, models: Any, name: str) -> dict[str, str]:
    org = models.Organization(name=name)
    session.add(org)
    session.flush()
    project = models.Project(organization_id=org.id, reference=f"T2-{name}", name=name)
    book = models.PriceBook(organization_id=org.id, name=f"L-{name}")
    session.add_all([project, book])
    session.flush()
    boq = models.BillOfQuantities(organization_id=org.id, project_id=project.id, name=name)
    version = models.PriceBookVersion(organization_id=org.id, price_book_id=book.id)
    session.add_all([boq, version])
    session.flush()
    composite = models.CompositePriceRow(
        organization_id=org.id,
        price_book_version_id=version.id,
        code=f"S-{name}",
        label=name,
        unit_code="m3",
    )
    session.add(composite)
    session.flush()
    item = models.BoqItem(
        organization_id=org.id,
        boq_id=boq.id,
        position="1.1",
        designation="ligne",
        unit_code="m3",
        quantity=Decimal("1"),
        kind="item",
        status="proposed",
    )
    session.add(item)
    session.flush()
    estimate = models.Estimate(
        organization_id=org.id,
        project_id=project.id,
        boq_id=boq.id,
        price_book_version_id=version.id,
        name=name,
    )
    session.add(estimate)
    session.flush()
    session.commit()
    return {
        "org": org.id,
        "project": project.id,
        "boq": boq.id,
        "version": version.id,
        "composite": composite.id,
        "item": item.id,
        "estimate": estimate.id,
    }


def _build(models: Any, own: dict[str, str], parent: dict[str, str], relation: str) -> Any:
    """Un enfant entièrement valide, dont SEUL le parent est détourné."""
    tag = abs(hash(relation)) % 9973
    if relation == "composite_prices.price_book_version_id":
        return models.CompositePriceRow(
            organization_id=own["org"],
            price_book_version_id=parent["version"],
            code=f"X{tag}",
            label="x",
            unit_code="m3",
        )
    if relation == "composite_components.composite_price_id":
        return models.CompositeComponentRow(
            organization_id=own["org"],
            composite_price_id=parent["composite"],
            component_type="consumption",
            label="x",
            consumption=Decimal("1"),
            resource_unit_code="m3",
        )
    if relation == "import_batches.price_book_version_id":
        return models.ImportBatch(
            organization_id=own["org"],
            price_book_version_id=parent["version"],
            filename="f.csv",
            sha256="0" * 64,
            column_mapping={},
        )
    if relation == "boq_items.parent_id":
        return models.BoqItem(
            organization_id=own["org"],
            boq_id=own["boq"],
            parent_id=parent["item"],
            position=f"9.{tag % 9}",
            designation="ligne",
            unit_code="m3",
            quantity=Decimal("1"),
            kind="item",
            status="proposed",
        )
    if relation == "estimates.project_id":
        return models.Estimate(
            organization_id=own["org"],
            project_id=parent["project"],
            boq_id=own["boq"],
            price_book_version_id=own["version"],
            name="x",
        )
    if relation == "estimate_versions.estimate_id":
        return models.EstimateVersion(
            organization_id=own["org"],
            estimate_id=parent["estimate"],
            price_book_version_id=own["version"],
            version_number=tag % 97 + 2,
        )
    if relation == "estimate_versions.price_book_version_id":
        return models.EstimateVersion(
            organization_id=own["org"],
            estimate_id=own["estimate"],
            price_book_version_id=parent["version"],
            version_number=tag % 97 + 100,
        )
    raise AssertionError(relation)


@pytest.fixture()
def tenants(migrated: None) -> Any:
    from metreo_api import models
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session, models, _graph(session, models, "Alpha"), _graph(session, models, "Beta")
    finally:
        session.rollback()
        session.close()


class TestTheDatabaseRefusesACrossTenantParent:
    @pytest.mark.parametrize(
        ("relation", "constraint", "_action"),
        RELATIONS,
        ids=[relation for relation, _, _ in RELATIONS],
    )
    def test_a_parent_of_another_tenant_is_refused(
        self, tenants: Any, relation: str, constraint: str, _action: str
    ) -> None:
        session, models, alpha, beta = tenants
        with pytest.raises(IntegrityError) as raised:
            session.add(_build(models, alpha, beta, relation))
            session.commit()
        session.rollback()
        message = str(raised.value)
        if session.bind.dialect.name == "postgresql":
            assert constraint in message, f"refus obtenu, mais pas celui attendu : {message}"
        else:
            assert "FOREIGN KEY constraint failed" in message, message

    @pytest.mark.parametrize(
        ("relation", "_constraint", "_action"),
        RELATIONS,
        ids=[relation for relation, _, _ in RELATIONS],
    )
    def test_the_same_row_inside_one_tenant_is_accepted(
        self, tenants: Any, relation: str, _constraint: str, _action: str
    ) -> None:
        """Le pendant indispensable : la contrainte ne doit rien casser.

        Sans lui, une contrainte qui refuserait TOUT passerait pour une preuve.
        """
        session, models, alpha, _beta = tenants
        session.add(_build(models, alpha, alpha, relation))
        session.commit()


class TestTheConstraintsAreDeclaredWhereTheyShouldBe:
    def test_every_relation_carries_its_composite_key(self, migrated: None) -> None:
        from sqlalchemy import inspect

        from metreo_api.db import get_engine

        inspector = inspect(get_engine())
        missing = []
        for relation, constraint, _action in RELATIONS:
            table = relation.split(".", 1)[0]
            names = {key["name"] for key in inspector.get_foreign_keys(table) if key.get("name")}
            if constraint not in names:
                missing.append(constraint)
        assert missing == [], missing

    def test_the_two_new_parent_uniques_exist(self, migrated: None) -> None:
        from sqlalchemy import inspect

        from metreo_api.db import get_engine

        inspector = inspect(get_engine())
        for table, name in (
            ("boq_items", "uq_boq_items_id_organization"),
            ("estimates", "uq_estimates_id_organization"),
        ):
            names = {c["name"] for c in inspector.get_unique_constraints(table)}
            assert name in names, f"{name} manque sur {table} : {sorted(names)}"


class TestTheReferentialActionsAreDecidedRelationByRelation:
    """Recopier l'action de la première tranche aurait été une faute.

    `estimate_versions.price_book_version_id` ne porte AUCUNE action : sa clé
    simple n'en porte pas, et supprimer une version tarifaire gelée dans un
    devis doit rester refusé. Lui mettre `CASCADE` par symétrie détruirait des
    versions de devis gelées.
    """

    def test_deleting_a_frozen_price_book_version_is_still_refused(self, tenants: Any) -> None:
        session, models, alpha, _beta = tenants
        session.add(
            models.EstimateVersion(
                organization_id=alpha["org"],
                estimate_id=alpha["estimate"],
                price_book_version_id=alpha["version"],
                version_number=42,
            )
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM price_book_versions WHERE id = :i"), {"i": alpha["version"]}
            )
            session.commit()
        session.rollback()

    def test_deleting_an_estimate_carries_its_versions_away(self, tenants: Any) -> None:
        session, models, alpha, _beta = tenants
        session.add(
            models.EstimateVersion(
                organization_id=alpha["org"],
                estimate_id=alpha["estimate"],
                price_book_version_id=alpha["version"],
                version_number=43,
            )
        )
        session.commit()

        session.execute(text("DELETE FROM estimates WHERE id = :i"), {"i": alpha["estimate"]})
        session.commit()
        assert session.execute(text("SELECT count(*) FROM estimate_versions")).scalar_one() == 0

    def test_a_child_row_still_deletes_with_its_parent_bill(self, tenants: Any) -> None:
        """`boq_items.parent_id` se référence elle-même : la cascade doit tenir."""
        session, models, alpha, _beta = tenants
        child = models.BoqItem(
            organization_id=alpha["org"],
            boq_id=alpha["boq"],
            parent_id=alpha["item"],
            position="1.2",
            designation="fille",
            unit_code="m3",
            quantity=Decimal("1"),
            kind="item",
            status="proposed",
        )
        session.add(child)
        session.commit()

        session.execute(text("DELETE FROM boq_items WHERE id = :i"), {"i": alpha["item"]})
        session.commit()
        # Compté sur le bordereau d'Alpha seulement : Beta a le sien, et une
        # assertion globale confondrait « la cascade a fonctionné » avec « la
        # table est vide ».
        remaining = session.execute(
            text("SELECT count(*) FROM boq_items WHERE boq_id = :b"), {"b": alpha["boq"]}
        ).scalar_one()
        assert remaining == 0


class TestTheOrmAmbiguityIsResolvedExplicitly:
    """Deux chemins de clé entre deux tables déjà liées par une `relationship()`.

    Sans `foreign_keys=`, SQLAlchemy refuse de configurer ses mappers. Il a
    raison de refuser : c'est à nous de dire lequel des deux chemins porte la
    relation.
    """

    def test_the_mappers_configure(self) -> None:
        from sqlalchemy.orm import configure_mappers

        configure_mappers()

    def test_the_relationship_still_loads_and_cascades(self, tenants: Any) -> None:
        session, models, alpha, _beta = tenants
        component = models.CompositeComponentRow(
            organization_id=alpha["org"],
            composite_price_id=alpha["composite"],
            component_type="consumption",
            label="x",
            consumption=Decimal("1"),
            resource_unit_code="m3",
        )
        session.add(component)
        session.commit()

        composite = session.get(models.CompositePriceRow, alpha["composite"])
        assert len(composite.components) == 1, "la relation doit encore charger"

        session.delete(composite)
        session.commit()
        assert session.execute(text("SELECT count(*) FROM composite_components")).scalar_one() == 0

    def test_both_sides_declare_the_path(self) -> None:
        """Un seul côté déclaré laisserait l'autre ambigu."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "src" / "metreo_api" / "models.py"
        ).read_text(encoding="utf-8")
        assert source.count('foreign_keys="CompositeComponentRow.composite_price_id"') == 2, (
            "les deux côtés de la relation doivent nommer le chemin de clé"
        )
