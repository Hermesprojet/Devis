"""Le résultat d'une suppression ne doit dépendre d'aucun ordre de création.

Chaque relation protégée porte deux clés : une simple, qui garde son action
référentielle, et une composite, qui tient la frontière multi-tenant. Tant que
la composite ne portait aucune action, le résultat dépendait de laquelle des
deux PostgreSQL déclenchait en premier — c'est-à-dire de leur ordre de
création, l'ordre des OID.

**Le défaut, mesuré.** Sur deux tables construites pour l'essai : clé simple
créée d'abord, la suppression du parent réussit ; clé composite créée d'abord,
elle est refusée. Les noms n'y sont pour rien — l'expérience a été refaite en
croisant l'ordre alphabétique et l'ordre de création, et seul le second
compte.

**Pourquoi cela ne s'était jamais vu.** Les migrations créent les clés simples
avant les composites, et `pg_dump` réémet les contraintes par ordre
alphabétique : `boq_items_price_item_id_fkey` trie avant
`fk_boq_items_price_item_tenant`. Le bon ordre sortait d'un accident de
nommage. Renommer cette seule clé en `zz_…`, exporter, restaurer, et la
suppression d'un prix passait de « référence mise à NULL » à
`ForeignKeyViolation` — sur le schéma réel, de bout en bout.

**La correction.** La composite reflète l'action de la simple qu'elle double.
Quel que soit l'ordre, l'état final est le même.

**SQLite n'est pas concerné**, et ce n'est pas une supposition : les deux
ordres de déclaration y donnent le même résultat, SQLite appliquant les actions
avant de vérifier ce qui reste. Il ne sait d'ailleurs pas analyser
`ON DELETE SET NULL (colonne)`. Les contrôles de catalogue sont donc
PostgreSQL-only ; ceux qui portent sur le RÉSULTAT tournent partout.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from .conftest import running_on_postgresql

#: enfant, colonne, contrainte composite, action attendue — recopiée de la clé
#: simple de la même relation, jamais choisie ici.
EXPECTED: tuple[tuple[str, str, str, str], ...] = (
    ("bills_of_quantities", "project_id", "fk_bills_of_quantities_project_tenant", "CASCADE"),
    (
        "price_book_versions",
        "price_book_id",
        "fk_price_book_versions_price_book_tenant",
        "CASCADE",
    ),
    (
        "price_items",
        "price_book_version_id",
        "fk_price_items_price_book_version_tenant",
        "CASCADE",
    ),
    ("boq_items", "boq_id", "fk_boq_items_boq_tenant", "CASCADE"),
    ("boq_items", "price_item_id", "fk_boq_items_price_item_tenant", "SET NULL"),
    ("boq_items", "composite_price_id", "fk_boq_items_composite_price_tenant", "SET NULL"),
    (
        "composite_components",
        "price_item_id",
        "fk_composite_components_price_item_tenant",
        "SET NULL",
    ),
    ("estimates", "boq_id", "fk_estimates_boq_tenant", "CASCADE"),
    # Sa clé simple ne porte aucune action : supprimer une version tarifaire
    # référencée par un devis est refusé dans les deux ordres. Rien à refléter.
    (
        "estimates",
        "price_book_version_id",
        "fk_estimates_price_book_version_tenant",
        "NO ACTION",
    ),
)

CODES = {"a": "NO ACTION", "c": "CASCADE", "n": "SET NULL", "r": "RESTRICT", "d": "SET DEFAULT"}

ONLY_PG = pytest.mark.skipif(
    not running_on_postgresql(),
    reason=(
        "Le catalogue et l'ordre des déclencheurs n'existent que sur PostgreSQL ; "
        "SQLite applique les actions avant de vérifier, dans les deux ordres."
    ),
)


def _graph(session: Any, models: Any, name: str) -> dict[str, str]:
    org = models.Organization(name=name)
    session.add(org)
    session.flush()
    project = models.Project(organization_id=org.id, reference=f"D-{name}", name=name)
    book = models.PriceBook(organization_id=org.id, name=f"L-{name}")
    session.add_all([project, book])
    session.flush()
    boq = models.BillOfQuantities(organization_id=org.id, project_id=project.id, name=name)
    version = models.PriceBookVersion(organization_id=org.id, price_book_id=book.id)
    session.add_all([boq, version])
    session.flush()
    price = models.PriceItem(
        organization_id=org.id,
        price_book_version_id=version.id,
        code=f"C-{name}",
        label=name,
        unit_code="m3",
        unit_price=Decimal("10.00"),
    )
    session.add(price)
    session.flush()
    session.commit()
    return {"org": org.id, "boq": boq.id, "price": price.id}


@pytest.fixture()
def graph(migrated: None) -> Any:
    from metreo_api import models
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session, models, _graph(session, models, "Alpha")
    finally:
        session.rollback()
        session.close()


def _item(models: Any, own: dict[str, str], **overrides: Any) -> Any:
    base: dict[str, Any] = {
        "organization_id": own["org"],
        "boq_id": own["boq"],
        "position": "1.1",
        "designation": "ligne",
        "unit_code": "m3",
        "quantity": Decimal("1"),
        "kind": "item",
        "status": "proposed",
    }
    return models.BoqItem(**(base | overrides))


@ONLY_PG
class TestEachCompositeMirrorsItsSimpleKey:
    """L'invariant, relu dans le catalogue à chaque exécution."""

    @pytest.mark.parametrize(
        ("child", "column", "name", "action"),
        EXPECTED,
        ids=[f"{c}.{col}" for c, col, _, _ in EXPECTED],
    )
    def test_the_composite_carries_the_action_of_the_simple_key(
        self, migrated: None, child: str, column: str, name: str, action: str
    ) -> None:
        from metreo_api.db import get_engine

        with get_engine().connect() as connection:
            composite = connection.execute(
                text(
                    "SELECT confdeltype, confdelsetcols IS NOT NULL "
                    "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE c.conname = :name "
                    "  AND t.relnamespace = ("
                    "        SELECT relnamespace FROM pg_class WHERE oid = 'boq_items'::regclass)"
                ),
                {"name": name},
            ).one()
            simple = connection.execute(
                text(
                    "SELECT c.confdeltype "
                    "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = :child AND c.contype = 'f' AND c.conname <> :name "
                    "  AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute "
                    "        WHERE attrelid = t.oid AND attname = :column)]::smallint[] "
                    "  AND t.relnamespace = ("
                    "        SELECT relnamespace FROM pg_class WHERE oid = 'boq_items'::regclass)"
                ),
                {"child": child, "name": name, "column": column},
            ).one()

        assert CODES[composite[0]] == action, f"{name} porte {CODES[composite[0]]}"
        assert CODES[simple[0]] == action, (
            f"la clé simple de {child}.{column} porte {CODES[simple[0]]} : "
            "la composite ne la reflète plus"
        )
        assert composite[1] == (action == "SET NULL"), (
            f"{name} : une liste de colonnes est exigée pour SET NULL et interdite ailleurs — "
            "sans elle, `organization_id`, NOT NULL, serait vidée aussi"
        )


@ONLY_PG
class TestTheOutcomeSurvivesTheWorstConstraintOrder:
    """La preuve qui tombait avant le correctif.

    Les deux clés de `boq_items.price_item_id` sont détruites puis recréées
    dans l'ordre **inverse**, celui qu'une restauration après renommage
    produit. La suppression d'un prix doit rendre exactement le même résultat.
    Tout est rétabli ensuite ; le schéma du test lui appartient.
    """

    def test_deleting_a_price_behaves_the_same_in_either_order(self, graph: Any) -> None:
        session, models, own = graph
        session.add(_item(models, own, price_item_id=own["price"]))
        session.commit()

        session.execute(text("ALTER TABLE boq_items DROP CONSTRAINT boq_items_price_item_id_fkey"))
        session.execute(
            text("ALTER TABLE boq_items DROP CONSTRAINT fk_boq_items_price_item_tenant")
        )
        # L'ordre inverse : la composite d'abord.
        session.execute(
            text(
                "ALTER TABLE boq_items ADD CONSTRAINT fk_boq_items_price_item_tenant "
                "FOREIGN KEY (price_item_id, organization_id) "
                "REFERENCES price_items (id, organization_id) ON DELETE SET NULL (price_item_id)"
            )
        )
        session.execute(
            text(
                "ALTER TABLE boq_items ADD CONSTRAINT boq_items_price_item_id_fkey "
                "FOREIGN KEY (price_item_id) REFERENCES price_items (id) ON DELETE SET NULL"
            )
        )
        session.commit()

        session.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": own["price"]})
        session.commit()

        row = session.execute(text("SELECT price_item_id, organization_id FROM boq_items")).one()
        assert row[0] is None, "la référence devait passer à NULL, quel que soit l'ordre"
        assert row[1] == own["org"], "l'organisation ne doit jamais être vidée"

    def test_without_the_mirrored_action_the_inverse_order_refuses(self, graph: Any) -> None:
        """Le pendant : c'est bien l'action reflétée qui fait la différence.

        Mêmes gestes, mais la composite recréée SANS action — l'état d'avant le
        correctif. La suppression doit alors être refusée. Sans ce test, le
        précédent pourrait passer pour une propriété de PostgreSQL.
        """
        session, models, own = graph
        session.add(_item(models, own, price_item_id=own["price"]))
        session.commit()

        session.execute(text("ALTER TABLE boq_items DROP CONSTRAINT boq_items_price_item_id_fkey"))
        session.execute(
            text("ALTER TABLE boq_items DROP CONSTRAINT fk_boq_items_price_item_tenant")
        )
        session.execute(
            text(
                "ALTER TABLE boq_items ADD CONSTRAINT fk_boq_items_price_item_tenant "
                "FOREIGN KEY (price_item_id, organization_id) "
                "REFERENCES price_items (id, organization_id)"
            )
        )
        session.execute(
            text(
                "ALTER TABLE boq_items ADD CONSTRAINT boq_items_price_item_id_fkey "
                "FOREIGN KEY (price_item_id) REFERENCES price_items (id) ON DELETE SET NULL"
            )
        )
        session.commit()

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError) as raised:
            session.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": own["price"]})
            session.commit()
        session.rollback()
        assert "fk_boq_items_price_item_tenant" in str(raised.value)


class TestTheOutcomeIsTheSameOnBothEngines:
    """Ce que voit l'application, quel que soit le moteur.

    Ces trois-là ne lisent aucun catalogue : elles regardent le résultat. Elles
    tournent donc partout, et c'est le point — la correction ne devait rien
    changer au comportement observable.
    """

    def test_deleting_a_price_nulls_only_the_reference(self, graph: Any) -> None:
        session, models, own = graph
        session.add(_item(models, own, price_item_id=own["price"]))
        session.commit()

        session.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": own["price"]})
        session.commit()

        row = session.execute(text("SELECT price_item_id, organization_id FROM boq_items")).one()
        assert row[0] is None
        assert row[1] == own["org"]

    def test_deleting_several_parents_in_one_statement_behaves_the_same(self, graph: Any) -> None:
        """Une seule instruction qui emporte plusieurs parents.

        Le report en fin d'instruction de `NO ACTION` rendait ce cas
        particulièrement suspect : plusieurs lignes touchées, un seul contrôle
        final. Le résultat doit rester ligne à ligne.
        """
        session, models, own = graph
        from metreo_api import models as m

        second = m.PriceItem(
            organization_id=own["org"],
            price_book_version_id=session.execute(
                text("SELECT price_book_version_id FROM price_items WHERE id = :i"),
                {"i": own["price"]},
            ).scalar_one(),
            code="C-2",
            label="deux",
            unit_code="m3",
            unit_price=Decimal("20.00"),
        )
        session.add(second)
        session.flush()
        session.add_all(
            [
                _item(models, own, position="9.1", price_item_id=own["price"]),
                _item(models, own, position="9.2", price_item_id=second.id),
            ]
        )
        session.commit()

        session.execute(text("DELETE FROM price_items"))
        session.commit()

        rows = session.execute(
            text("SELECT price_item_id, organization_id FROM boq_items ORDER BY position")
        ).all()
        assert [row[0] for row in rows] == [None, None]
        assert {row[1] for row in rows} == {own["org"]}

    def test_deleting_a_bill_still_carries_its_rows_away(self, graph: Any) -> None:
        """L'autre famille : `CASCADE` doit rester `CASCADE`."""
        session, models, own = graph
        session.add(_item(models, own, position="8.1"))
        session.commit()

        session.execute(text("DELETE FROM bills_of_quantities WHERE id = :i"), {"i": own["boq"]})
        session.commit()

        assert session.execute(text("SELECT count(*) FROM boq_items")).scalar_one() == 0
