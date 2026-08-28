"""Ce que les clés composites tiennent quand deux écritures se croisent.

Les tests d'intégrité prouvent qu'un lien inter-tenant est refusé quand il est
posé seul. Ils ne disent rien de la fenêtre : entre le moment où une session
lit un parent et celui où elle insère l'enfant, une autre session peut déplacer
ce parent, changer son organisation ou le supprimer. C'est exactement là qu'une
protection purement applicative cède.

**Ce que ce fichier sépare, et pourquoi.** Huit scénarios. Cinq ne demandent
aucune concurrence : le lot mixte, l'import qui contourne les services, la
session ORM qui porte deux organisations, et les deux moitiés d'un déplacement
— changer la clé étrangère sans l'organisation, changer l'organisation sans la
clé. Ceux-là valent sur les deux moteurs et tournent partout.

Trois demandent deux transactions réellement simultanées. Ils sont **réservés à
PostgreSQL** et sautés ailleurs. SQLite sérialise les écritures sur un verrou
de fichier : y faire tourner ces scénarios donnerait du vert sans rien
démontrer, et un vert qui ne démontre rien est pire qu'un test absent, parce
qu'on s'y fie. Ce qui est prouvé sous PostgreSQL n'est pas présenté comme
prouvé sous SQLite.

**Le mécanisme, sur PostgreSQL.** La vérification d'une clé étrangère prend un
`FOR KEY SHARE` sur la ligne parente. Une fois `uq_projects_id_organization`
posée, `organization_id` devient une colonne de clé : la modifier prend un
`FOR UPDATE`, incompatible, et l'écrivain concurrent **attend** au lieu de
passer. Sans la clé composite, `organization_id` n'est une colonne de clé de
personne et le déplacement passe sans jamais croiser l'insertion — l'enfant
inter-tenant est créé, et rien ne s'en aperçoit. C'est la course que ces
contraintes ferment, et c'est ce que le premier test montre.

Aucun `sleep` n'arbitre ces tests : deux barrières et un fil qui doit rester
bloqué. Le résultat est le même à chaque exécution.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from .conftest import running_on_postgresql

CONCURRENT = pytest.mark.skipif(
    not running_on_postgresql(),
    reason=(
        "Deux transactions simultanées n'existent que sur PostgreSQL ; SQLite "
        "sérialise les écritures et le test passerait sans rien démontrer."
    ),
)


# --------------------------------------------------------------------------
# Deux organisations complètes, construites par l'ORM
# --------------------------------------------------------------------------


def _graph(session: Any, models: Any, name: str) -> dict[str, str]:
    """Un graphe valide et complet pour une organisation.

    Construit par l'ORM, jamais par du SQL écrit à la main : toutes les
    contraintes métier — `kind`, `status`, `component_type`, `confidence` — sont
    franchies, pour que le seul refus possible soit le refus multi-tenant.
    """
    org = models.Organization(name=name)
    session.add(org)
    session.flush()
    project = models.Project(organization_id=org.id, reference=f"P-{name}", name=name)
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
    session.add_all([price])
    session.flush()
    session.commit()
    return {
        "org": org.id,
        "project": project.id,
        "boq": boq.id,
        "book": book.id,
        "version": version.id,
        "price": price.id,
    }


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


# --------------------------------------------------------------------------
# Deux transactions tenues ouvertes, synchronisées par barrières
# --------------------------------------------------------------------------


class Ran:
    """Le résultat d'un fil : ce qu'il a fait, ou ce qui l'a arrêté."""

    def __init__(self) -> None:
        self.error: BaseException | None = None
        self.finished = threading.Event()
        #: Le PID serveur de SA connexion, publié dès qu'elle est ouverte.
        #: C'est lui qui rend l'observation du blocage exacte : on ne demande
        #: pas « quelqu'un attend-il un verrou ? » mais « CETTE connexion-ci
        #: attend-elle un verrou ? ».
        self.pid: int | None = None
        self.connected = threading.Event()

    @property
    def failure(self) -> str:
        return "" if self.error is None else f"{type(self.error).__name__}: {self.error}"


def _in_its_own_connection(database_url: str, work: Callable[[Any], None]) -> Ran:
    """Lance ``work`` sur une connexion à elle, dans un fil à elle.

    Une connexion neuve et non une session partagée : deux transactions
    simultanées demandent deux connexions distinctes, sinon il n'y a pas de
    concurrence à observer.
    """
    ran = Ran()

    def target() -> None:
        engine = create_engine(database_url, future=True)
        try:
            with engine.connect() as connection:
                ran.pid = int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
                # Cette lecture ouvre une transaction implicite ; la refermer
                # tout de suite, sinon le `begin()` explicite du scénario échoue.
                connection.rollback()
                ran.connected.set()
                work(connection)
        except BaseException as error:  # rapporté au test, pas avalé
            ran.error = error
        finally:
            engine.dispose()
            ran.connected.set()
            ran.finished.set()

    threading.Thread(target=target, daemon=True).start()
    return ran


#: Cette connexion précise attend-elle un verrou ?
#:
#: La première version comptait `pg_locks WHERE NOT granted` sans rien filtrer.
#: C'était prendre l'attente de n'importe quelle transaction du serveur pour la
#: preuve que NOTRE écrivain est bloqué. Mesuré : deux schémas migrés dans une
#: même base, un blocage fabriqué dans le premier — un observateur travaillant
#: dans le second comptait « 1 verrou en attente » et repartait, sans que rien
#: ne le bloque. La suite est séquentielle aujourd'hui, donc le défaut ne s'est
#: jamais manifesté ; il aurait rendu ces trois courses vertes pour la mauvaise
#: raison dès la première exécution parallèle. Même classe de défaut que celui
#: rattrapé par la CI sur `pg_constraint` : une interrogation de catalogue à
#: portée globale invoquée pour prouver quelque chose de local.
#:
#: Filtrer `pg_locks` par schéma ne suffisait pas non plus, et le mesurer l'a
#: montré : une transaction qui attend la ligne verrouillée par une autre
#: n'attend pas sur la RELATION mais sur le `transactionid` de sa bloqueuse, et
#: `pg_locks.relation` y est NULL. La jointure sur `pg_class` la faisait
#: disparaître, et les deux courses tombaient.
#:
#: Le PID serveur tranche : il désigne exactement la connexion dont ce test
#: attend le blocage, quels que soient le schéma, le type de verrou et ce que
#: fait le reste du serveur.
WAITING_ON_A_LOCK = (
    "SELECT count(*) FROM pg_stat_activity WHERE pid = :pid AND wait_event_type = 'Lock'"
)


def _wait_until_blocked(session: Any, ran: Ran, *, timeout: float = 30.0) -> None:
    """Attend que `ran` soit réellement en attente d'un verrou.

    Sondage du catalogue plutôt qu'un `sleep` d'une durée choisie au jugé : le
    test ne dépend d'aucune temporisation, et il échoue franchement si le
    blocage attendu ne se produit jamais.
    """
    assert ran.connected.wait(timeout=timeout), "la connexion concurrente ne s'est pas ouverte"
    assert ran.pid is not None, "le PID serveur n'a pas été publié"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session.rollback()
        waiting = session.execute(text(WAITING_ON_A_LOCK), {"pid": ran.pid}).scalar_one()
        if waiting:
            return
        if ran.finished.is_set():
            raise AssertionError(
                "l'écriture concurrente s'est terminée sans jamais attendre de verrou"
            )
    raise AssertionError(
        "la transaction concurrente n'attend aucun verrou : elle n'a pas été bloquée"
    )


# --------------------------------------------------------------------------
# 1 — mise à jour concurrente du parent, sans changer sa clé
# --------------------------------------------------------------------------


@CONCURRENT
class TestAnOrdinaryParentUpdateDoesNotBlockTheChild:
    """Renommer un projet pendant qu'on lui ajoute un bordereau ne bloque rien.

    Le contraire serait un coût inacceptable : si toute vérification de clé
    composite sérialisait les modifications du parent, poser ces contraintes
    rendrait l'application inutilisable sous charge. `FOR KEY SHARE` ne
    s'oppose qu'aux modifications de colonnes de clé — un nom n'en est pas une.
    """

    def test_renaming_the_parent_and_inserting_a_child_both_pass(
        self, tenants: Any, database_url: str
    ) -> None:
        session, _models, alpha, _ = tenants
        both_started = threading.Barrier(2, timeout=30)

        def rename(connection: Any) -> None:
            transaction = connection.begin()
            connection.execute(
                text("UPDATE projects SET name = 'renommé' WHERE id = :i"), {"i": alpha["project"]}
            )
            both_started.wait()
            transaction.commit()

        renamer = _in_its_own_connection(database_url, rename)

        def insert(connection: Any) -> None:
            transaction = connection.begin()
            both_started.wait()
            connection.execute(
                text(
                    "INSERT INTO bills_of_quantities "
                    "(id, organization_id, project_id, name, source, revision, "
                    " created_at, updated_at) "
                    "VALUES ('concurrent-1', :o, :p, 'métré', 'manual', 1, now(), now())"
                ),
                {"o": alpha["org"], "p": alpha["project"]},
            )
            transaction.commit()

        inserter = _in_its_own_connection(database_url, insert)

        assert renamer.finished.wait(timeout=30), "le renommage n'a pas rendu la main"
        assert inserter.finished.wait(timeout=30), "l'insertion n'a pas rendu la main"
        assert renamer.error is None, renamer.failure
        assert inserter.error is None, inserter.failure

        kept = session.execute(
            text("SELECT organization_id FROM bills_of_quantities WHERE id = 'concurrent-1'")
        ).scalar_one()
        assert kept == alpha["org"]


# --------------------------------------------------------------------------
# 2 — changement concurrent d'organisation du parent
# --------------------------------------------------------------------------


@CONCURRENT
class TestMovingTheParentWhileAChildArrives:
    """La course que ces contraintes ferment, et la preuve qu'elles la ferment.

    L'insertion de l'enfant prend `FOR KEY SHARE` sur le projet. Le
    déplacement du projet vers une autre organisation touche
    `organization_id`, devenue colonne de clé par `uq_projects_id_organization` :
    il demande `FOR UPDATE`, et **attend**. Il ne repart qu'une fois l'enfant
    commité, voit alors une ligne qui le référence, et échoue.

    Sans la clé composite, il n'attendrait pas : le projet partirait chez Beta
    et le bordereau tout neuf resterait chez Alpha, pointant un projet qui ne
    lui appartient plus.
    """

    def test_the_move_waits_then_is_refused(self, tenants: Any, database_url: str) -> None:
        session, _models, alpha, beta = tenants
        child_inserted = threading.Barrier(2, timeout=30)
        release = threading.Event()

        def insert(connection: Any) -> None:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO bills_of_quantities "
                    "(id, organization_id, project_id, name, source, revision, "
                    " created_at, updated_at) "
                    "VALUES ('concurrent-2', :o, :p, 'métré', 'manual', 1, now(), now())"
                ),
                {"o": alpha["org"], "p": alpha["project"]},
            )
            child_inserted.wait()
            # L'autre fil demande maintenant `FOR UPDATE` sur ce projet. Le test
            # principal constate le blocage dans `pg_locks`, puis libère.
            assert release.wait(timeout=30), "le test principal n'a jamais libéré"
            transaction.commit()

        inserter = _in_its_own_connection(database_url, insert)

        def move(connection: Any) -> None:
            transaction = connection.begin()
            child_inserted.wait()
            connection.execute(
                text("UPDATE projects SET organization_id = :b WHERE id = :i"),
                {"b": beta["org"], "i": alpha["project"]},
            )
            transaction.commit()

        mover = _in_its_own_connection(database_url, move)

        _wait_until_blocked(session, mover)
        assert not mover.finished.is_set(), "le déplacement aurait dû attendre l'enfant"
        release.set()

        assert inserter.finished.wait(timeout=30), "l'insertion n'a pas rendu la main"
        assert inserter.error is None, inserter.failure
        assert mover.finished.wait(timeout=30), "le déplacement n'a pas rendu la main"

        assert isinstance(mover.error, IntegrityError), (
            "le déplacement du projet devait être refusé — " + (mover.failure or "il a réussi")
        )
        assert "fk_bills_of_quantities_project_tenant" in str(mover.error), mover.failure

        stayed = session.execute(
            text("SELECT organization_id FROM projects WHERE id = :i"), {"i": alpha["project"]}
        ).scalar_one()
        assert stayed == alpha["org"], "le projet ne doit pas avoir changé d'organisation"


# --------------------------------------------------------------------------
# 3 — suppression concurrente du parent
# --------------------------------------------------------------------------


@CONCURRENT
class TestDeletingTheParentWhileAChildArrives:
    """La suppression attend, puis applique son action référentielle.

    Le prix est supprimé pendant qu'une ligne de bordereau vient de le
    référencer. La suppression attend le commit de l'enfant, puis `SET NULL` de
    la clé simple s'applique à la ligne toute neuve. Ce qui compte : aucune
    ligne orpheline, et `organization_id` reste rempli — ce que ferait échouer
    un `SET NULL` porté par la clé composite.
    """

    def test_the_deletion_waits_then_nulls_the_new_row(
        self, tenants: Any, database_url: str
    ) -> None:
        session, _models, alpha, _ = tenants
        child_inserted = threading.Barrier(2, timeout=30)
        release = threading.Event()

        def insert(connection: Any) -> None:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO boq_items "
                    "(id, organization_id, boq_id, price_item_id, sort_index, position, "
                    " designation, unit_code, quantity, kind, status, created_at, updated_at) "
                    "VALUES ('concurrent-3', :o, :b, :p, 0, '1.1', 'ligne', 'm3', 1, "
                    "        'item', 'proposed', now(), now())"
                ),
                {"o": alpha["org"], "b": alpha["boq"], "p": alpha["price"]},
            )
            child_inserted.wait()
            assert release.wait(timeout=30), "le test principal n'a jamais libéré"
            transaction.commit()

        inserter = _in_its_own_connection(database_url, insert)

        def delete(connection: Any) -> None:
            transaction = connection.begin()
            child_inserted.wait()
            connection.execute(text("DELETE FROM price_items WHERE id = :i"), {"i": alpha["price"]})
            transaction.commit()

        deleter = _in_its_own_connection(database_url, delete)

        _wait_until_blocked(session, deleter)
        assert not deleter.finished.is_set(), "la suppression aurait dû attendre l'enfant"
        release.set()

        assert inserter.finished.wait(timeout=30), "l'insertion n'a pas rendu la main"
        assert deleter.finished.wait(timeout=30), "la suppression n'a pas rendu la main"
        assert inserter.error is None, inserter.failure
        assert deleter.error is None, deleter.failure

        row = session.execute(
            text("SELECT price_item_id, organization_id FROM boq_items WHERE id = 'concurrent-3'")
        ).one()
        assert row[0] is None, "la clé simple devait poser NULL sur la ligne toute neuve"
        assert row[1] == alpha["org"], "l'organisation ne doit jamais être vidée"


# --------------------------------------------------------------------------
# 4 à 8 — sans concurrence, vrais sur les deux moteurs
# --------------------------------------------------------------------------


def _refusal(session: Any, work: Callable[[], None], *constraints: str) -> None:
    """Exige un refus de la base, et le bon quand la base sait le nommer.

    Plusieurs noms sont acceptés quand l'écriture viole plusieurs contraintes à
    la fois : PostgreSQL en signale une, et laquelle n'est pas garanti. Exiger
    un nom précis là où deux sont légitimes rendrait le test instable sans rien
    prouver de plus. SQLite ne nomme rien et se contente de la violation.
    """
    with pytest.raises(IntegrityError) as raised:
        work()
    session.rollback()
    message = str(raised.value)
    if session.bind.dialect.name == "postgresql":
        assert any(name in message for name in constraints), (
            f"refus obtenu, mais pas celui attendu ({', '.join(constraints)}) : {message}"
        )
    else:
        assert "FOREIGN KEY constraint failed" in message, message


class TestABatchIsRefusedWholeOrNotAtAll:
    """Un lot dont une seule ligne traverse la frontière ne passe pas du tout.

    C'est le scénario réel de l'import : cent lignes correctes et une qui
    pointe le tarif d'un autre. Accepter les cent premières et refuser la
    dernière laisserait un bordereau à moitié importé ; le refus doit porter sur
    le lot entier, et rien ne doit rester.
    """

    def test_one_crossing_row_rejects_the_whole_insert(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        good = [_item(models, alpha, position=f"1.{n}") for n in range(1, 6)]
        crossing = _item(models, alpha, position="1.6", price_item_id=beta["price"])

        _refusal(
            session,
            lambda: (session.add_all([*good, crossing]), session.commit()),
            "fk_boq_items_price_item_tenant",
        )

        remaining = session.execute(
            text("SELECT count(*) FROM boq_items WHERE boq_id = :b"), {"b": alpha["boq"]}
        ).scalar_one()
        assert remaining == 0, "aucune ligne du lot ne doit subsister"

    def test_the_same_batch_without_the_crossing_row_passes(self, tenants: Any) -> None:
        session, models, alpha, _ = tenants
        session.add_all([_item(models, alpha, position=f"2.{n}") for n in range(1, 6)])
        session.commit()

        remaining = session.execute(
            text("SELECT count(*) FROM boq_items WHERE boq_id = :b"), {"b": alpha["boq"]}
        ).scalar_one()
        assert remaining == 5, "le lot propre doit passer entièrement"


class TestBypassingTheServicesChangesNothing:
    """Ni l'ORM sans service, ni le SQL direct ne franchissent la frontière.

    Les routes répondent 404 sur un identifiant d'une autre organisation. Cette
    protection-là ne vaut que pour ce qui passe par les routes. Un seed, un
    script de reprise, une correction manuelle en base : rien de tout cela ne
    voit `get_owned`. La base, elle, refuse dans les trois cas.
    """

    def test_the_orm_without_any_service_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        _refusal(
            session,
            lambda: (
                session.add(_item(models, alpha, price_item_id=beta["price"])),
                session.commit(),
            ),
            "fk_boq_items_price_item_tenant",
        )

    def test_raw_sql_is_refused_the_same_way(self, tenants: Any) -> None:
        session, _models, alpha, beta = tenants
        _refusal(
            session,
            lambda: session.execute(
                text(
                    "INSERT INTO boq_items "
                    "(id, organization_id, boq_id, price_item_id, sort_index, position, "
                    " designation, unit_code, quantity, kind, status, created_at, updated_at) "
                    "VALUES ('seed-1', :o, :b, :p, 0, '9.1', 'ligne', 'm3', 1, "
                    "        'item', 'proposed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"o": alpha["org"], "b": alpha["boq"], "p": beta["price"]},
            ),
            "fk_boq_items_price_item_tenant",
        )


class TestASessionHoldingTwoOrganisations:
    """Une session peut porter deux organisations ; elle ne peut pas les mêler.

    Un traitement de fond légitime charge Alpha et Beta ensemble. Ce n'est pas
    interdit, et rien ici ne cherche à l'interdire. Ce qui est interdit, c'est
    qu'une ligne d'Alpha pointe un objet de Beta — et le refus doit tomber au
    `flush`, pas plus tard.
    """

    def test_both_organisations_can_be_written_in_one_session(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        session.add_all([_item(models, alpha, position="3.1"), _item(models, beta, position="3.2")])
        session.commit()

        counted = session.execute(text("SELECT count(*) FROM boq_items")).scalar_one()
        assert counted == 2, "deux organisations dans une session est légitime"

    def test_mixing_them_in_one_row_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        session.add(_item(models, alpha, position="3.3"))
        session.flush()
        _refusal(
            session,
            lambda: (
                session.add(_item(models, beta, position="3.4", price_item_id=alpha["price"])),
                session.flush(),
            ),
            "fk_boq_items_price_item_tenant",
        )


class TestHalfAMoveIsAlwaysRefused:
    """Déplacer une ligne demande de changer les deux colonnes, ou aucune.

    Ce sont les deux moitiés du même geste. Changer la clé étrangère seule
    rattache la ligne au parent d'un autre ; changer `organization_id` seule
    laisse la ligne pointer un parent qu'elle ne possède plus. La base refuse
    les deux, et n'accepte que le changement cohérent.
    """

    def test_changing_the_foreign_key_alone_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="4.1", price_item_id=alpha["price"])
        session.add(item)
        session.commit()

        _refusal(
            session,
            lambda: session.execute(
                text("UPDATE boq_items SET price_item_id = :p WHERE id = :i"),
                {"p": beta["price"], "i": item.id},
            ),
            "fk_boq_items_price_item_tenant",
        )

    def test_changing_the_organisation_alone_is_refused(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="4.2", price_item_id=alpha["price"])
        session.add(item)
        session.commit()

        _refusal(
            session,
            lambda: session.execute(
                text("UPDATE boq_items SET organization_id = :o WHERE id = :i"),
                {"o": beta["org"], "i": item.id},
            ),
            "fk_boq_items_boq_tenant",
            "fk_boq_items_price_item_tenant",
        )

    def test_changing_both_coherently_is_accepted(self, tenants: Any) -> None:
        session, models, alpha, beta = tenants
        item = _item(models, alpha, position="4.3", price_item_id=alpha["price"])
        session.add(item)
        session.commit()

        session.execute(
            text(
                "UPDATE boq_items SET organization_id = :o, boq_id = :b, price_item_id = :p "
                "WHERE id = :i"
            ),
            {"o": beta["org"], "b": beta["boq"], "p": beta["price"], "i": item.id},
        )
        session.commit()

        row = session.execute(
            text("SELECT organization_id, boq_id FROM boq_items WHERE id = :i"), {"i": item.id}
        ).one()
        assert row[0] == beta["org"]
        assert row[1] == beta["boq"]


class TestNoConcurrentClaimIsMadeUnderSqlite:
    """Garde-fou sur ce fichier lui-même.

    Les trois classes concurrentes doivent rester conditionnées à un vrai
    PostgreSQL. Si quelqu'un retire la garde pour « faire tourner plus de tests
    partout », ce test tombe : sous SQLite ces scénarios passeraient sans rien
    prouver.
    """

    def test_the_three_racing_classes_are_gated(self) -> None:
        from pathlib import Path

        source = Path(__file__).read_text(encoding="utf-8").splitlines()
        decorated = [line for line in source if line.strip() == "@CONCURRENT"]
        assert len(decorated) == 3, "les trois classes de course doivent être gardées"
        assert any("not running_on_postgresql()" in line for line in source), (
            "la garde doit rester conditionnelle au moteur réellement vérifié"
        )
