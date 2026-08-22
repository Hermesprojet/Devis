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
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "api"

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


@contextmanager
def owned_database(admin_url: str) -> Iterator[str]:
    """Create a database, yield its URL, and drop it — whatever happens.

    ``CREATE DATABASE`` is the proof of ownership: PostgreSQL refuses it if the
    name already exists, so reaching the body means this run created it. The
    drop is in a ``finally`` so an exception, an assertion or a keyboard
    interrupt still cleans up.
    """
    parsed = make_url(admin_url)
    name = generated_name()
    # AUTOCOMMIT: CREATE/DROP DATABASE cannot run inside a transaction block.
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        print(f"base créée par ce run : {name}")
        # `str()` sur une URL SQLAlchemy remplace le mot de passe par « *** » :
        # l'URL rendue serait inutilisable, avec une erreur d'authentification
        # pour tout diagnostic.
        yield parsed.set(database=name).render_as_string(hide_password=False)
    finally:
        if not owns(name):  # pragma: no cover - defensive, unreachable by design
            raise RuntimeError(f"refus de supprimer « {name} » : ce run ne l'a pas créée")
        try:
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
