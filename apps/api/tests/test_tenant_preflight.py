"""La migration refuse de poser une contrainte sur des données déjà incohérentes.

Poser une clé étrangère sur une table qui viole déjà la règle échoue — mais
l'échec vient alors de PostgreSQL, au milieu de la migration, avec un message
qui nomme une contrainte et rien d'autre. L'opérateur ne sait ni combien de
lignes sont en cause, ni lesquelles des neuf relations, ni quoi faire.

Le préflight interroge chaque relation séparément **avant** toute écriture, et
s'arrête en nommant la relation et le nombre. Il ne corrige rien : réattribuer
une ligne à une autre organisation ou la rattacher à un autre parent change un
montant de devis, et c'est une décision d'exploitation.

Rien d'autre que des compteurs ne sort : ni identifiant, ni référence de
projet, ni nom de client n'atteint un journal de migration.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260827_0001_tenant_composite_keys.py"
)


def _revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tenant_composite_keys", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


revision = _revision()


def _two_graphs(session: Any, models: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    graphs = []
    for name in ("Alpha", "Beta"):
        org = models.Organization(name=f"Preflight {name}")
        session.add(org)
        session.flush()
        project = models.Project(
            organization_id=org.id, reference=f"PF-{name}", name=f"Projet {name}"
        )
        book = models.PriceBook(organization_id=org.id, name=f"Lib {name}")
        session.add_all([project, book])
        session.flush()
        boq = models.BillOfQuantities(
            organization_id=org.id, project_id=project.id, name=f"Métré {name}"
        )
        version = models.PriceBookVersion(organization_id=org.id, price_book_id=book.id)
        session.add_all([boq, version])
        session.flush()
        graphs.append({"org": org.id, "project": project.id, "boq": boq.id, "version": version.id})
    session.commit()
    return graphs[0], graphs[1]


@pytest.fixture()
def prepared(migrated: None) -> Any:
    from metreo_api import models
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        alpha, beta = _two_graphs(session, models)
        yield session, models, alpha, beta
    finally:
        session.rollback()
        session.close()


def _force_cross_tenant(session: Any, models: Any, count: int, alpha: dict, beta: dict) -> None:
    """Créer `count` incohérences en contournant la contrainte, le temps du test.

    Les contraintes composites existent déjà à ce stade : on les neutralise pour
    fabriquer l'état que le préflight doit détecter dans une base **antérieure**
    à sa propre migration, puis on les rétablit. C'est la seule façon d'éprouver
    ce garde.

    La ligne est construite par l'ORM : elle porte donc toutes ses valeurs par
    défaut et franchit toutes les autres contraintes. Un seul champ est faux —
    celui qui désigne le parent — pour que le préflight n'ait qu'une raison de
    la signaler.
    """
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        session.execute(
            text(
                "ALTER TABLE bills_of_quantities "
                "DROP CONSTRAINT fk_bills_of_quantities_project_tenant"
            )
        )
    else:
        session.execute(text("PRAGMA foreign_keys=OFF"))

    for index in range(count):
        session.add(
            models.BillOfQuantities(
                organization_id=alpha["org"],
                project_id=beta["project"],
                name=f"croisé {index}",
            )
        )
    session.commit()


class TestThePreflightCounts:
    def test_a_clean_database_reports_nothing(self, prepared: Any) -> None:
        session, _, _, _ = prepared
        assert revision.inconsistencies(session.connection()) == {}

    def test_one_inconsistency_is_reported_once(self, prepared: Any) -> None:
        session, models, alpha, beta = prepared
        _force_cross_tenant(session, models, 1, alpha, beta)
        found = revision.inconsistencies(session.connection())
        assert found == {"fk_bills_of_quantities_project_tenant": 1}, found

    def test_several_inconsistencies_are_counted(self, prepared: Any) -> None:
        session, models, alpha, beta = prepared
        _force_cross_tenant(session, models, 4, alpha, beta)
        found = revision.inconsistencies(session.connection())
        assert found == {"fk_bills_of_quantities_project_tenant": 4}, found

    def test_one_query_exists_per_relation(self) -> None:
        """Neuf relations, neuf interrogations — pas un contrôle global vague."""
        assert len(revision.RELATIONS) == 9
        assert len({name for *_, name in revision.RELATIONS}) == 9


class TestTheRefusalIsUsableAndDiscreet:
    def test_the_refusal_names_the_relation_and_the_count(self) -> None:
        with pytest.raises(RuntimeError) as raised:
            revision._refuse({"fk_estimates_boq_tenant": 3})
        message = str(raised.value)
        assert "fk_estimates_boq_tenant" in message
        assert "3 ligne(s)" in message
        assert "Procédure" in message, "l'opérateur doit savoir quoi faire"

    def test_the_refusal_says_nothing_was_modified(self) -> None:
        with pytest.raises(RuntimeError) as raised:
            revision._refuse({"fk_estimates_boq_tenant": 1})
        assert "Aucune donnée n'a été modifiée" in str(raised.value)

    def test_the_counting_query_selects_only_a_count(self) -> None:
        """Aucun contenu métier ne doit pouvoir remonter d'une requête de préflight."""
        source = MIGRATION.read_text(encoding="utf-8")
        body = source[source.index("def inconsistencies") : source.index("def _refuse")]
        assert "SELECT count(*)" in body
        for forbidden in ("SELECT *", "c.name", "c.reference", "c.label", "c.designation"):
            assert forbidden not in body, f"la requête peut remonter « {forbidden} »"


class TestTheMigrationNeverRepairs:
    def test_the_revision_contains_no_write_statement(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
        body = code[code.index("def upgrade") :]
        for forbidden in ("UPDATE ", "DELETE ", "INSERT ", "op.execute("):
            assert forbidden not in body, (
                f"la migration écrit ({forbidden.strip()}) : elle doit refuser, jamais réparer"
            )


class TestTheReferentialActionsStillWork:
    """Le piège identifié : un `SET NULL` composite viderait `organization_id`."""

    def test_deleting_a_price_sets_the_reference_to_null_without_error(self, prepared: Any) -> None:
        session, models, alpha, _ = prepared
        price = models.PriceItem(
            organization_id=alpha["org"],
            price_book_version_id=alpha["version"],
            code="DEL-1",
            label="à supprimer",
            unit_code="m3",
            unit_price=Decimal("5.00"),
        )
        session.add(price)
        session.flush()
        item = models.BoqItem(
            organization_id=alpha["org"],
            boq_id=alpha["boq"],
            position="8.1",
            designation="ligne",
            unit_code="m3",
            quantity=Decimal("1"),
            price_item_id=price.id,
        )
        session.add(item)
        session.commit()

        session.delete(price)
        session.commit()
        session.refresh(item)
        assert item.price_item_id is None, "la clé simple doit poser NULL"
        assert item.organization_id == alpha["org"], (
            "l'organisation ne doit jamais être vidée : c'est le piège que la clé "
            "composite éviterait mal si elle portait ON DELETE SET NULL"
        )

    def test_deleting_a_bill_cascades_its_rows(self, prepared: Any) -> None:
        session, models, alpha, _ = prepared
        boq = models.BillOfQuantities(
            organization_id=alpha["org"], project_id=alpha["project"], name="jetable"
        )
        session.add(boq)
        session.flush()
        item = models.BoqItem(
            organization_id=alpha["org"],
            boq_id=boq.id,
            position="8.2",
            designation="ligne",
            unit_code="m3",
            quantity=Decimal("1"),
        )
        session.add(item)
        session.commit()
        item_id = item.id

        session.delete(boq)
        session.commit()
        remaining = session.execute(
            text("SELECT count(*) FROM boq_items WHERE id = :i"), {"i": item_id}
        ).scalar_one()
        assert remaining == 0, "la cascade doit emporter la ligne"
