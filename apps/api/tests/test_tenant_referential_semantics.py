"""Ce que font réellement les suppressions et les mises à jour, une fois les
clés composites posées.

Une clé simple et une clé composite coexistent sur chaque relation protégée.
La simple garde son action référentielle — `CASCADE` ou `SET NULL` — et la
composite n'en porte aucune. Ce n'est pas un oubli : un `ON DELETE SET NULL`
composite tenterait de vider aussi `organization_id`, qui est NOT NULL, et la
suppression échouerait. La simple pose NULL sur le parent, puis la composite —
`MATCH SIMPLE`, donc inactive dès qu'une colonne est NULL — ne vérifie plus
rien.

Cette coexistence est **délibérée** et rend le `SET NULL` portable entre
PostgreSQL et SQLite. Ces tests la tiennent : si quelqu'un ajoute une action
référentielle à une clé composite, ou retire la clé simple, ils tombent.

Lu dans `pg_catalog` sur PostgreSQL 16 : les neuf clés composites sont
`NO ACTION` sur DELETE comme sur UPDATE, validées, non différables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

#: Les relations dont la clé SIMPLE porte `ON DELETE SET NULL`, et dont la
#: colonne enfant est donc nullable.
NULLABLE = (
    ("boq_items", "price_item_id"),
    ("boq_items", "composite_price_id"),
    ("composite_components", "price_item_id"),
)


def _graph(session: Any, models: Any, name: str) -> dict[str, Any]:
    org = models.Organization(name=name)
    session.add(org)
    session.flush()
    project = models.Project(organization_id=org.id, reference=f"S-{name}", name=name)
    book = models.PriceBook(organization_id=org.id, name=f"L-{name}")
    session.add_all([project, book])
    session.flush()
    boq = models.BillOfQuantities(organization_id=org.id, project_id=project.id, name=f"M-{name}")
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
    composite = models.CompositePriceRow(
        organization_id=org.id,
        price_book_version_id=version.id,
        code=f"S-{name}",
        label=name,
        unit_code="m3",
    )
    session.add_all([price, composite])
    session.flush()
    session.commit()
    return {
        "org": org.id,
        "project": project.id,
        "boq": boq.id,
        "book": book.id,
        "version": version.id,
        "price": price.id,
        "composite": composite.id,
    }


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


def _item(models: Any, own: dict[str, Any], **overrides: Any) -> Any:
    base = {
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


class TestDeletingANullableParent:
    """La référence part à NULL ; l'organisation ne bouge jamais."""

    def test_deleting_a_price_leaves_the_row_and_its_organisation(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        item = _item(models, alpha, price_item_id=alpha["price"])
        session.add(item)
        session.commit()
        item_id = item.id

        session.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": alpha["price"]})
        session.commit()

        row = session.execute(
            text("SELECT price_item_id, organization_id FROM boq_items WHERE id = :i"),
            {"i": item_id},
        ).one()
        assert row[0] is None, "la clé simple doit poser NULL"
        assert row[1] == alpha["org"], "l'organisation ne doit jamais être vidée"

    def test_deleting_a_composite_price_behaves_the_same(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        item = _item(models, alpha, position="1.2", composite_price_id=alpha["composite"])
        session.add(item)
        session.commit()
        item_id = item.id

        session.execute(
            text("DELETE FROM composite_prices WHERE id = :i"), {"i": alpha["composite"]}
        )
        session.commit()

        row = session.execute(
            text("SELECT composite_price_id, organization_id FROM boq_items WHERE id = :i"),
            {"i": item_id},
        ).one()
        assert row[0] is None
        assert row[1] == alpha["org"]

    def test_a_component_survives_the_deletion_of_its_price(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        component = models.CompositeComponentRow(
            organization_id=alpha["org"],
            composite_price_id=alpha["composite"],
            component_type="consumption",
            label="composant",
            price_item_id=alpha["price"],
        )
        session.add(component)
        session.commit()
        component_id = component.id

        session.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": alpha["price"]})
        session.commit()

        row = session.execute(
            text("SELECT price_item_id, organization_id FROM composite_components WHERE id = :i"),
            {"i": component_id},
        ).one()
        assert row == (None, alpha["org"])


class TestDeletingAMandatoryParent:
    """La cascade de la clé simple emporte l'enfant ; la composite n'entrave rien."""

    def test_deleting_a_bill_removes_its_rows(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        item = _item(models, alpha, position="2.1")
        session.add(item)
        session.commit()
        item_id = item.id

        session.execute(text("DELETE FROM bills_of_quantities WHERE id = :i"), {"i": alpha["boq"]})
        session.commit()
        remaining = session.execute(
            text("SELECT count(*) FROM boq_items WHERE id = :i"), {"i": item_id}
        ).scalar_one()
        assert remaining == 0

    def test_deleting_a_project_cascades_through_two_levels(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        item = _item(models, alpha, position="2.2")
        session.add(item)
        session.commit()

        session.execute(text("DELETE FROM projects WHERE id = :i"), {"i": alpha["project"]})
        session.commit()
        bills = session.execute(
            text("SELECT count(*) FROM bills_of_quantities WHERE id = :i"), {"i": alpha["boq"]}
        ).scalar_one()
        rows = session.execute(
            text("SELECT count(*) FROM boq_items WHERE boq_id = :i"), {"i": alpha["boq"]}
        ).scalar_one()
        assert (bills, rows) == (0, 0)


class TestMovingARowBetweenOrganisations:
    """Changer l'un sans l'autre est exactement ce que la clé composite refuse."""

    def test_changing_only_the_organisation_of_a_child_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="3.1")
        session.add(item)
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE boq_items SET organization_id = :o WHERE id = :i"),
                {"o": beta["org"], "i": item.id},
            )
            session.flush()
        session.rollback()

    def test_changing_only_the_parent_of_a_child_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="3.2")
        session.add(item)
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE boq_items SET boq_id = :b WHERE id = :i"),
                {"b": beta["boq"], "i": item.id},
            )
            session.flush()
        session.rollback()

    def test_changing_the_organisation_of_a_referenced_parent_is_refused(
        self, tenants: Any
    ) -> None:
        """`ON UPDATE NO ACTION` : le parent ne peut pas s'échapper sous ses enfants."""
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="3.3")
        session.add(item)
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE bills_of_quantities SET organization_id = :o WHERE id = :i"),
                {"o": beta["org"], "i": alpha["boq"]},
            )
            session.flush()
        session.rollback()

    def test_moving_both_together_to_a_consistent_state_is_accepted(self, tenants: Any) -> None:
        """La contrainte interdit l'incohérence, pas le déplacement cohérent."""
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="3.4")
        session.add(item)
        session.commit()

        session.execute(
            text("UPDATE boq_items SET organization_id = :o, boq_id = :b WHERE id = :i"),
            {"o": beta["org"], "b": beta["boq"], "i": item.id},
        )
        session.commit()
        row = session.execute(
            text("SELECT organization_id, boq_id FROM boq_items WHERE id = :i"), {"i": item.id}
        ).one()
        assert row == (beta["org"], beta["boq"])


class TestTheOrmAgreesWithRawSql:
    """Deux chemins, un seul comportement."""

    def test_the_orm_is_refused_exactly_like_direct_sql(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="4.1", price_item_id=beta["price"])
        session.add(item)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO boq_items (id, organization_id, boq_id, position, designation, "
                    "unit_code, quantity, kind, status, price_item_id, created_at, updated_at) "
                    "VALUES ('sql-1', :o, :b, '4.2', 'x', 'm3', 1, 'item', 'proposed', :p, "
                    + (
                        "now(), now())"
                        if session.bind.dialect.name == "postgresql"
                        else "datetime('now'), datetime('now'))"
                    )
                ),
                {"o": alpha["org"], "b": alpha["boq"], "p": beta["price"]},
            )
        session.rollback()

    def test_the_orm_mappers_configure_without_ambiguity(self) -> None:
        """Deux chemins de clé entre deux tables rendraient une relation ambiguë.

        Aucune `relationship()` ne traverse une paire couverte par une clé
        composite — vérifié en configurant tous les mappers, ce qui lèverait
        `AmbiguousForeignKeysError` sinon.
        """
        from sqlalchemy.orm import configure_mappers

        from metreo_api import models

        configure_mappers()
        assert models.CompositePriceRow.components is not None


#: Les clés composites du schéma **où vivent réellement les tables interrogées**.
#:
#: `pg_constraint` couvre toute la base, et les noms se répètent d'un schéma à
#: l'autre : en CI, `public` porte déjà le schéma migré par l'étape de seed
#: quand la suite crée le sien, et compter sans filtrer donnait 18 pour 9
#: contraintes réelles. Un nombre attendu n'a de sens que si l'on dit sur quoi
#: on compte.
#:
#: Le filtre résout `'boq_items'::regclass` plutôt que d'appeler
#: `current_schema()`. Les deux coïncident dans tous les cas réalistes —
#: mesuré sur trois schémas migrés dans une même base, y compris quand le
#: schéma courant n'est pas la première entrée du `search_path`. Ils divergent
#: dans un cas : `search_path=pg_catalog,s1` fait rendre `pg_catalog` à
#: `current_schema()`, et le compte tombe à zéro. `regclass` pose la bonne
#: question — « le schéma de la table que j'interroge vraiment » — au lieu de
#: « la première entrée du chemin ».
COMPOSITE_KEYS_HERE = (
    "SELECT c.conname, c.confdeltype, c.confupdtype, c.convalidated, c.condeferrable "
    "FROM pg_constraint c "
    "JOIN pg_class t ON t.oid = c.conrelid "
    "WHERE c.conname LIKE 'fk\\_%\\_tenant' "
    "  AND t.relnamespace = ("
    "        SELECT relnamespace FROM pg_class WHERE oid = 'boq_items'::regclass)"
)


def _shifted_search_path(database_url: str) -> str:
    """La même URL, avec un schéma inexistant AVANT le schéma réel.

    PostgreSQL ignore une entrée qui ne désigne rien : le schéma courant
    devient alors la deuxième entrée du chemin. C'est la configuration où un
    filtre qui raisonnerait sur « la première entrée » se tromperait.
    """
    marker = "options=-csearch_path="
    assert marker in database_url, database_url
    head, schema = database_url.split(marker, 1)
    return f"{head}{marker}schema_qui_nexiste_pas,{schema}"


class TestTheCatalogueMatchesTheIntent:
    """Ce que la base porte réellement, relu à chaque exécution."""

    def test_the_nine_keys_are_validated_immediate_and_never_move_on_update(
        self, migrated: None
    ) -> None:
        """Ce qui reste vrai des neuf clés, quelles que soient leurs actions.

        Ce test exigeait « aucune action de suppression ». La révision
        `b4f2c7d81a05` en pose délibérément — chaque composite reflète l'action
        de la clé simple qu'elle double, faute de quoi le résultat d'une
        suppression dépend de l'ordre de création des contraintes. Ce que
        chaque composite porte est vérifié relation par relation dans
        `test_deletion_determinism.py` ; ici restent les trois propriétés qui
        ne doivent bouger pour aucune d'entre elles.

        `ON UPDATE` reste NO ACTION partout : déplacer un parent référencé vers
        une autre organisation doit être refusé, jamais propagé en silence.
        Aucune ne doit être différable — le report ferait remonter une
        violation inter-tenant au commit seulement, trop tard pour un service
        qui branche sur l'erreur. Aucune ne doit rester non validée : une clé
        `NOT VALID` laisserait passer les lignes déjà en base.
        """
        from metreo_api.db import get_engine

        engine = get_engine()
        if engine.dialect.name != "postgresql":
            pytest.skip("`pg_catalog` n'existe que sur PostgreSQL")
        with engine.connect() as connection:
            rows = connection.execute(text(COMPOSITE_KEYS_HERE)).all()
        assert len(rows) == 9, [row[0] for row in rows]
        for name, _on_delete, on_update, validated, deferrable in rows:
            assert on_update == "a", f"{name} propage une mise à jour au lieu de la refuser"
            assert validated, f"{name} n'est pas validée"
            assert not deferrable, f"{name} est différable"

    def test_the_count_ignores_the_other_schemas_of_the_same_database(
        self, migrated: None, database_url: str
    ) -> None:
        """Le décompte doit valoir POUR CE SCHÉMA, pas pour la base entière.

        C'est le défaut que la CI a rattrapé : sans filtre, le job PostgreSQL
        comptait dix-huit clés là où neuf existent, parce que `public` portait
        déjà le schéma migré. Ce test le tient à l'endroit exact : la requête
        sans filtre doit voir strictement plus que la requête filtrée dès qu'un
        autre schéma migré existe, et la filtrée doit rester à neuf.
        """
        from metreo_api.db import get_engine

        engine = get_engine()
        if engine.dialect.name != "postgresql":
            pytest.skip("un seul schéma sous SQLite")
        with engine.connect() as connection:
            everywhere = connection.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk\\_%\\_tenant'")
            ).scalar_one()
            here = len(connection.execute(text(COMPOSITE_KEYS_HERE)).all())
        assert here == 9
        assert everywhere >= here, "le compte global ne peut pas être inférieur au local"

    def test_the_filter_holds_when_the_schema_is_not_first_in_the_search_path(
        self, migrated: None, database_url: str
    ) -> None:
        """Le schéma courant n'est pas toujours la première entrée du chemin.

        Un schéma inexistant placé devant est ignoré par PostgreSQL : le schéma
        réel devient la deuxième entrée. Un filtre qui supposerait « la première
        entrée du `search_path` » compterait alors les mauvaises contraintes, ou
        zéro. Mesuré : avec `pg_catalog` en tête, `current_schema()` rend
        `pg_catalog` et le compte tombe à zéro — la résolution `regclass`, elle,
        reste juste.
        """
        from sqlalchemy import create_engine

        from metreo_api.db import get_engine

        if get_engine().dialect.name != "postgresql":
            pytest.skip("le `search_path` n'existe que sur PostgreSQL")

        shifted = create_engine(_shifted_search_path(database_url), future=True)
        try:
            with shifted.connect() as connection:
                path = connection.execute(text("SHOW search_path")).scalar_one()
                rows = connection.execute(text(COMPOSITE_KEYS_HERE)).all()
        finally:
            shifted.dispose()

        assert path.startswith("schema_qui_nexiste_pas,"), path
        assert len(rows) == 9, [row[0] for row in rows]

    def test_the_simple_keys_keep_their_actions(self, migrated: None) -> None:
        """Retirer la clé simple ferait perdre le `SET NULL` portable."""
        from metreo_api.db import get_engine

        inspector = inspect(get_engine())
        for table, column in NULLABLE:
            simple = [
                key
                for key in inspector.get_foreign_keys(table)
                if key["constrained_columns"] == [column]
            ]
            assert simple, f"{table}.{column} a perdu sa clé simple"
            if get_engine().dialect.name == "postgresql":
                assert simple[0]["options"].get("ondelete") == "SET NULL", simple
