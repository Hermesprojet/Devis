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

Une troisième règle s'est ajoutée après coup, et pour la même raison que le
code de production l'avait apprise : **posséder le nom ne suffit pas, il faut
posséder la cible réellement ouverte.** Ce helper construisait son URL avec
`make_url(admin_url).set(database=name)`, qui remplace le chemin et conserve la
chaîne de requête. Avec une URL d'administration portant `?dbname=victime`, il
créait bien sa base aléatoire, mais écrivait `CREATE TABLE temoin` et ses trois
sentinelles dans « victime », les y relisait, et concluait au vert en ayant
modifié une base étrangère — puis supprimait sa propre base restée vide. Le
constructeur sûr est celui du dépôt, `_url_safety.safe_target_url` : une
seconde implémentation redivergerait, ce qui est exactement ce qui vient
d'arriver.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

# Le dépôt n'a qu'une définition des paramètres redirecteurs et qu'un
# constructeur d'URL cible. Les tests s'en servent au lieu d'en écrire un
# second : c'est la divergence, pas l'ignorance, qui a rouvert le défaut.
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _url_safety import (  # noqa: E402 - après l'ajustement de sys.path ci-dessus
    UnsafeUrl,
    refuse_redirection,
    safe_target_url,
)

__all__ = [
    "PREFIX",
    "UnsafeUrl",
    "Witness",
    "count_sentinels",
    "exists",
    "has_table",
    "names_with_prefix",
    "owned_witness",
    "witness_name",
]

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


def has_table(url: str, table: str = "temoin") -> bool:
    """Cette base porte-t-elle la table témoin ?

    `count_sentinels` ne distingue pas « trois lignes ici » de « trois lignes
    là-bas » quand l'URL ment sur sa cible. Celle-ci répond sur une base nommée
    explicitement, et sert donc à prouver l'endroit où l'écriture n'a **pas** eu
    lieu.
    """
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"),
                    {"name": table},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def names_with_prefix(admin_url: str, prefix: str = PREFIX) -> set[str]:
    """Les bases témoins présentes sur ce serveur, pour repérer un résidu.

    Comparée avant et après un refus, cette liste dit si le helper a créé
    quelque chose avant de refuser — un refus qui laisse une base derrière lui
    n'est pas un refus.
    """
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text("SELECT datname FROM pg_database WHERE datname LIKE :pattern"),
                    {"pattern": f"{prefix}%"},
                ).scalars()
            )
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

    Le refus d'une URL redirigée vient **avant** `create_engine` : une URL qui
    peut ouvrir autre chose que ce que son chemin nomme n'est pas exploitable
    par un helper qui va créer, écrire, puis détruire.
    """
    # Premier garde, avant toute connexion.
    refuse_redirection(
        admin_url,
        doing=(
            "la base témoin serait créée ici et ses sentinelles écrites ailleurs, "
            "dans une base que ce test ne possède pas"
        ),
    )

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

        # Second garde : le constructeur partagé retire les redirections et
        # demande au dialecte ce qu'il ouvrira. Écrire dans une autre base que
        # celle qu'on vient de créer serait aussi grave que d'en détruire une
        # qu'on n'a pas créée.
        url = safe_target_url(admin_url, name)
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
