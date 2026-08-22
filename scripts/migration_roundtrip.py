"""Round-trip the migrations inside a database this run creates and owns.

`make migrations` used to run `alembic downgrade base` — which drops every
application table — against whatever database the caller named. Three separate
defects came out of that one shape: the guard read the wrong URL component, a
second variable overrode the validated one, and the name-based check accepted
a production database. Each was fixed; each fix was a patch on a command that
should not have existed.

The rule here removes the class instead of guarding it: **the process may only
destroy a resource it created itself and whose identity it holds.** A random
name is generated, `CREATE DATABASE` proves this run created it — the
statement fails if the name is taken — and only that name is ever dropped. No
URL supplied by a caller is accepted as a destruction target, so a mistyped
variable, a hidden `dbname=`, a diverted host or a confusion between
development and production databases cannot reach anything real.

The admin URL is a *connection* target, never a destruction target: it is used
to create and drop the throw-away database, and nothing else.

Two later defects showed that stating the rule is not applying it. First, the
ephemeral URL was built with `parsed.set(database=name)`, which replaces the
path and keeps the query string — so `…/postgres?dbname=metreo_victim_a` ran
the whole round-trip, `downgrade base` included, on the victim: two
organisations became zero while the script announced success and dropped its
own empty database. Second, the cleanup ran on the way out of a *failed*
`CREATE DATABASE`; `owns()` only proves a name looks generated, so a
pre-existing database of the same name — a leftover from an interrupted run —
was terminated and dropped, three witness rows with it. Redirecting query
parameters are now refused before anything is opened, the built URL is checked
against `create_connect_args()`, and nothing is dropped without the proof that
this run created it.

Usage:
    python scripts/migration_roundtrip.py --admin-url postgresql+psycopg://…/postgres
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "api"

# Le module voisin porte la seule liste de paramètres redirecteurs du dépôt.
# Deux listes divergeraient — celle du contrôle de nom n'avait pas `database`.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _url_safety import (  # noqa: E402 - après l'ajustement de sys.path ci-dessus
    REDIRECTING_PARAMETERS,
    effective_database,
    redirecting_parameters,
)

# `alembic/env.py` importe la configuration de l'application : sans cela, il
# faudrait lancer alembic depuis apps/api avec PYTHONPATH=src, et ce script
# perdrait la maîtrise du cycle de vie de la base qu'il possède.
if str(API / "src") not in sys.path:
    sys.path.insert(0, str(API / "src"))

#: Prefix carried by every database this script creates. It is not a
#: permission — the identity check below is — but it makes a leaked database
#: obvious in `\l`, and it makes the intent readable in a server log.
PREFIX = "metreo_roundtrip_"


def generated_name() -> str:
    """A name this run invents, unpredictable enough not to collide."""
    return f"{PREFIX}{secrets.token_hex(8)}"


def owns(name: str) -> bool:
    """Would this script accept to drop ``name``?

    The only names it accepts are the ones its own generator can produce.
    Anything else — a developer's database, a production database, a name
    passed on the command line — is refused, because this process cannot have
    created it.
    """
    return bool(re.fullmatch(PREFIX + "[0-9a-f]{16}", name))


class UnsafeAdminUrl(RuntimeError):
    """L'URL d'administration ne désigne pas de façon univoque ce qu'on ouvrira."""


def ephemeral_url(parsed: URL, name: str) -> str:
    """L'URL de la base éphémère, débarrassée de toute redirection.

    `parsed.set(database=name)` ne suffit pas : il change le CHEMIN et conserve
    la chaîne de requête, à laquelle libpq obéit. Reproduit avec destruction
    réelle : `…/postgres?dbname=metreo_victim_a` a fait tourner
    `upgrade head`, `downgrade base` puis `upgrade head` sur `metreo_victim_a`,
    passée de deux organisations à zéro, pendant que le script supprimait sa
    propre base jetable, restée vide.

    On retire donc les paramètres redirecteurs, puis on demande au dialecte ce
    qu'il ouvrira. Les deux couches sont distinctes : la première refuse en
    amont, la seconde vérifie l'URL réellement construite.
    """
    candidate = parsed.set(database=name).difference_update_query(sorted(REDIRECTING_PARAMETERS))
    opened = effective_database(candidate)
    if opened != name:
        raise UnsafeAdminUrl(
            f"l'URL construite ouvrirait « {opened} » et non « {name} » : "
            "refus, aucune migration ne doit toucher une autre base"
        )
    # `str()` sur une URL SQLAlchemy remplace le mot de passe par « *** » :
    # l'URL rendue serait inutilisable, avec une erreur d'authentification
    # pour tout diagnostic.
    return candidate.render_as_string(hide_password=False)


@contextmanager
def owned_database(admin_url: str) -> Iterator[str]:
    """Create a database, yield its URL, and drop it — if this run created it.

    ``CREATE DATABASE`` is the proof of ownership: PostgreSQL refuses it if the
    name already exists, so reaching the body means this run created it. That
    proof is recorded in ``created``; without it nothing is dropped, because
    ``owns()`` only establishes that a name *looks* generated, not that this
    process generated it. A pre-existing database of the same name — a leftover
    from an interrupted run — used to be terminated and dropped on the way out
    of a failed creation. Reproduced: three witness rows lost.

    The drop is still in a ``finally`` so an exception, a refusal or a keyboard
    interrupt cleans up what this run really did create, and ``dispose()`` is
    guaranteed either way.
    """
    parsed = make_url(admin_url)

    # Avant toute connexion, création, migration ou suppression : une URL qui
    # peut se déplacer ailleurs qu'où son chemin le dit n'est pas exploitable.
    redirecting = redirecting_parameters(parsed)
    if redirecting:
        raise UnsafeAdminUrl(
            f"l'URL d'administration porte {redirecting} dans sa chaîne de requête, "
            "ce qui déplace la connexion ailleurs que là où son chemin le dit : "
            "les migrations tourneraient sur une base que ce run n'a pas créée"
        )

    name = generated_name()
    url = ephemeral_url(parsed, name)

    # AUTOCOMMIT: CREATE/DROP DATABASE cannot run inside a transaction block.
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        print(f"base créée par ce run : {name}")
        yield url
    finally:
        try:
            if created:
                if not owns(name):  # pragma: no cover - defensive, unreachable by design
                    raise RuntimeError(f"refus de supprimer « {name} » : ce run ne l'a pas créée")
                with admin.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :name AND pid <> pg_backend_pid()"
                        ),
                        {"name": name},
                    )
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                print(f"base supprimée : {name}")
        finally:
            admin.dispose()


def alembic(url: str, *arguments: str) -> None:
    from alembic import command
    from alembic.config import Config

    configuration = Config(str(API / "alembic.ini"))
    configuration.set_main_option("script_location", str(API / "alembic"))
    configuration.set_main_option("sqlalchemy.url", url)
    import os

    previous = os.environ.get("METREO_DATABASE_URL")
    os.environ["METREO_DATABASE_URL"] = url
    try:
        getattr(command, arguments[0])(configuration, *arguments[1:])
    finally:
        if previous is None:
            os.environ.pop("METREO_DATABASE_URL", None)
        else:
            os.environ["METREO_DATABASE_URL"] = previous


def table_count(url: str) -> int:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin-url",
        required=True,
        help=(
            "URL d'un serveur PostgreSQL où ce script peut CRÉER une base. "
            "Cible de connexion, jamais cible de destruction."
        ),
    )
    parser.add_argument("--seed", action="store_true", help="charger le jeu de démonstration")
    arguments = parser.parse_args()

    if make_url(arguments.admin_url).get_backend_name() != "postgresql":
        print(
            "migration-roundtrip : refusé — cet aller-retour exige PostgreSQL. "
            "SQLite ne prouve rien sur le DDL transactionnel.",
            file=sys.stderr,
        )
        return 1

    try:
        return roundtrip(arguments)
    except UnsafeAdminUrl as refusal:
        # Un traceback n'est pas un diagnostic : la raison du refus doit se lire.
        print(f"migration-roundtrip : refusé — {refusal}.", file=sys.stderr)
        return 1


def roundtrip(arguments: argparse.Namespace) -> int:
    with owned_database(arguments.admin_url) as url:
        print("upgrade head…")
        alembic(url, "upgrade", "head")
        after_upgrade = table_count(url)
        if after_upgrade < 10:
            print(f"schéma anormalement pauvre après upgrade : {after_upgrade}", file=sys.stderr)
            return 1

        print("downgrade base…")
        alembic(url, "downgrade", "base")
        after_downgrade = table_count(url)
        # `alembic_version` survit au downgrade : c'est la table d'Alembic.
        if after_downgrade > 1:
            print(
                f"{after_downgrade} tables subsistent après downgrade base — "
                "une migration ne défait pas ce qu'elle a fait",
                file=sys.stderr,
            )
            return 1

        print("upgrade head…")
        alembic(url, "upgrade", "head")
        if table_count(url) != after_upgrade:
            print("le second upgrade ne reproduit pas le premier schéma", file=sys.stderr)
            return 1

        if arguments.seed:
            import os

            os.environ["METREO_DATABASE_URL"] = url
            os.environ.setdefault("METREO_ENVIRONMENT", "test")
            os.environ.setdefault("METREO_AUTH_MODE", "dev")
            os.environ.setdefault(
                "METREO_JWT_SECRET", "roundtrip-secret-not-used-in-production-0123456789"
            )
            from metreo_api.db import get_session_factory, reset_engine

            reset_engine()
            from metreo_api.seed import seed

            session = get_session_factory()()
            try:
                print(f"seed : {seed(session)['status']}")
            finally:
                session.close()
                reset_engine()

        print(f"aller-retour des migrations valide — {after_upgrade} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
