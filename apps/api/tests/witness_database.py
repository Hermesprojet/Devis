"""Une base témoin que le test crée, possède, et est donc en droit de supprimer.

Les preuves qui montraient qu'une base préexistante survit commençaient par
`DROP DATABASE IF EXISTS "metreo_roundtrip_deadbeefdeadbeef"`. Sur un serveur
partagé — celui d'un développeur, une instance commune — cette préparation
supprimait la base qu'elle prétendait épargner, puis en recréait une du même
nom et concluait qu'elle avait survécu. La preuve était circulaire, et la
perte réelle. Deux suites lancées ensemble se supprimaient mutuellement.

Le code de production a fermé cette classe de défauts ; ces tests la
rouvraient. La règle est la même des deux côtés : **on ne détruit qu'une
ressource qu'on a soi-même créée et dont on possède l'identité.**

Deux conséquences dans ce module :

- aucun nom fixe. Chaque base porte un identifiant tiré au hasard, propre au
  test et à l'exécution, donc deux suites concurrentes ne se croisent pas ;
- aucune suppression de préparation. Si le nom est pris, on en tire un autre —
  on ne « fait pas de la place ». Et `created_by_test` ne passe à vrai qu'après
  un `CREATE DATABASE` réussi : sans lui, ni terminaison de connexions ni
  `DROP`.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

#: Préfixe des bases témoins. Il ne donne aucun droit — `created_by_test` seul
#: le fait — mais rend un résidu éventuel identifiable dans `\l`.
PREFIX = "metreo_temoin_"

#: Nombre de noms essayés avant d'abandonner. Une collision sur 64 bits ne se
#: produit pas ; la boucle existe pour que l'échec soit un nouveau tirage et
#: jamais une suppression.
ATTEMPTS = 5


def witness_name() -> str:
    """Un nom que cette exécution invente, imprévisible et non partagé."""
    return f"{PREFIX}{secrets.token_hex(8)}"


@dataclass(frozen=True)
class Witness:
    """Une base témoin vivante, et la preuve que ce test l'a créée."""

    name: str
    url: str
    sentinels: int
    created_by_test: bool


def _terminate(admin: object, name: str) -> None:
    with admin.connect() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )


def count_sentinels(url: str) -> int:
    """Combien de lignes témoins subsistent dans cette base."""
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return int(connection.execute(text("SELECT count(*) FROM temoin")).scalar_one())
    finally:
        engine.dispose()


def exists(admin_url: str, name: str) -> bool:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": name},
                ).scalar_one()
            )
    finally:
        engine.dispose()


@contextmanager
def owned_witness(
    admin_url: str,
    *,
    name_factory: Callable[[], str] = witness_name,
    sentinels: int = 3,
) -> Iterator[Witness]:
    """Créer une base témoin, y écrire des sentinelles, et ne détruire qu'elle.

    ``name_factory`` permet au test de collision d'emprunter le générateur du
    script sous test, pour que la base témoin porte un nom que celui-ci
    engendrerait — sans jamais écrire ce nom en dur.
    """
    created_by_test = False
    name = ""
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        for _ in range(ATTEMPTS):
            candidate = name_factory()
            try:
                with admin.connect() as connection:
                    connection.execute(text(f'CREATE DATABASE "{candidate}"'))
            except ProgrammingError as exc:
                if "already exists" not in str(exc):
                    raise
                # Le nom est pris : on en tire un autre. On ne supprime pas
                # une base dont on ne sait rien pour se faire de la place.
                continue
            name, created_by_test = candidate, True
            break
        if not created_by_test:
            raise RuntimeError(
                f"aucun nom libre après {ATTEMPTS} tirages — rien n'a été créé, "
                "donc rien ne sera supprimé"
            )

        url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        if sentinels:
            engine = create_engine(url, future=True)
            try:
                with engine.begin() as connection:
                    connection.execute(text("CREATE TABLE temoin (id integer)"))
                    for number in range(1, sentinels + 1):
                        connection.execute(text("INSERT INTO temoin VALUES (:id)"), {"id": number})
            finally:
                engine.dispose()

        yield Witness(name=name, url=url, sentinels=sentinels, created_by_test=True)
    finally:
        try:
            if created_by_test:
                _terminate(admin, name)
                with admin.connect() as connection:
                    # Pas de `IF EXISTS` : ce test l'a créée, elle doit être là.
                    # Si elle a disparu, quelqu'un a supprimé notre base et on
                    # veut le savoir bruyamment.
                    connection.execute(text(f'DROP DATABASE "{name}"'))
        finally:
            admin.dispose()
