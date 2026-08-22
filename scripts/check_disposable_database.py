"""Refuse a database URL that does not name a disposable database.

`make release-gate` and `make migrations` both destroy what they touch: the
first runs the full suite, which creates and drops a schema per test; the
second runs `alembic downgrade base`, which removes every application table.
Pointing either at a real database loses data.

The first version of this check looked for `test`, `gate`, `ci`, `tmp` or
`scratch` **anywhere in the URL**, which is not a check at all: a production
URL whose host is `db-prod.cimenteries-sa.be` contains `ci`, a user named
`tester` contains `test`, a generated password may contain `tmp`. The check
now reads the database name alone, splits it into tokens, and demands that
one token be a disposability marker — `metreo_gate` passes, `metreo_ci`
passes, `metreo_production` does not, and neither does anything hosted on a
machine whose name happens to contain one of those letters.

A denylist wins over the allowlist: `metreo_ci_production` is refused even
though it carries `ci`, because naming a database after production is a
stronger signal than naming it after a pipeline.

Usage: python scripts/check_disposable_database.py <url> [--label make-target]
Exit status is 0 when the database is disposable, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

#: A database name carrying one of these tokens is understood as throw-away.
DISPOSABLE_TOKENS = frozenset({"test", "tests", "gate", "ci", "tmp", "temp", "scratch"})

#: These win over the list above, whatever else the name carries.
FORBIDDEN_TOKENS = frozenset({"prod", "prods", "production", "live", "prd", "real"})


#: Query parameters that move the connection somewhere other than where the
#: URL path says. libpq reads them, SQLAlchemy passes them through, and the
#: path then names a database nobody connects to.
REDIRECTING_PARAMETERS = frozenset(
    {"dbname", "host", "hostaddr", "port", "user", "service", "passfile"}
)


def effective_database(url: str) -> str:
    """The database the driver will actually open — not the one the path names.

    Reading ``urlsplit(url).path`` is reading the wrong component. The psycopg
    dialect merges the URL query into its connection arguments, so
    ``…/metreo_gate?dbname=metreo`` names ``metreo_gate`` in its path and opens
    ``metreo``. A guard that validates the path validates a database nobody
    touches, and prints its reassuring name while another one is destroyed.
    That was reproduced: a victim database went from two organisations to zero
    while the announced disposable database stayed untouched.

    So the dialect is asked what it will do. When it cannot be asked — an
    unknown driver, an unparsable URL — the caller is told, rather than being
    given a guess.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        # A SQLite URL names a file; its final component is the database.
        return (parsed.database or "").rsplit("/", 1)[-1]
    arguments = parsed.get_dialect()().create_connect_args(parsed)[1]
    return str(arguments.get("dbname") or arguments.get("database") or parsed.database or "")


class UnknownDialect(Exception):
    """The driver cannot be loaded, so nothing can be said about the target."""


def database_name(url: str) -> str:
    """Kept for readability at call sites; the effective name is what counts."""
    return effective_database(url)


def tokens(name: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9]+", name.lower()) if token]


def refusal(url: str) -> str | None:
    """Return why this URL is refused, or ``None`` when it is acceptable."""
    if not url.strip():
        return "aucune URL fournie"

    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as exc:
        return f"URL illisible ({exc})"

    # Un paramètre de requête qui déplace la connexion rend le contrôle
    # inopérant : ce qui est validé n'est plus ce qui est ouvert. Plutôt que de
    # tenter de suivre chaque redirection, on les refuse — aucune n'a de raison
    # d'être dans l'URL d'une commande destructrice.
    redirecting = sorted(set(parsed.query) & REDIRECTING_PARAMETERS)
    if redirecting:
        return (
            f"l'URL porte {redirecting} dans sa chaîne de requête, ce qui déplace la "
            "connexion ailleurs que là où son chemin le dit : le nom contrôlé ne serait "
            "pas celui de la base ouverte"
        )

    # Sans le dialecte, on ne peut pas savoir quelle base sera ouverte. Le
    # refus doit donc être explicite : un traceback n'est pas un diagnostic, et
    # la documentation prétendait traiter ce cas.
    try:
        name = database_name(url)
    except NoSuchModuleError as exc:
        return (
            f"dialecte inconnu ({exc}) : impossible de savoir quelle base serait "
            "ouverte, donc impossible de garantir qu'elle est jetable"
        )
    if not name:
        return "l'URL ne nomme aucune base de données"

    found = tokens(name)
    forbidden = sorted(set(found) & FORBIDDEN_TOKENS)
    if forbidden:
        return (
            f"la base « {name} » porte {forbidden} : ce nom désigne une base réelle, "
            "et cette commande détruit ce qu'elle touche"
        )

    if not (set(found) & DISPOSABLE_TOKENS):
        return (
            f"la base « {name} » ne se déclare pas jetable — son nom doit porter "
            f"l'un de ces mots : {sorted(DISPOSABLE_TOKENS)}. "
            "Le contrôle porte sur le NOM DE LA BASE seul, jamais sur l'hôte, "
            "l'utilisateur ni le mot de passe, qui peuvent contenir ces lettres "
            "par accident"
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--label", default="cette commande")
    args = parser.parse_args()

    problem = refusal(args.url)
    if problem is None:
        print(f"base jetable acceptée : {database_name(args.url)}")
        return 0

    print(f"{args.label} : refusé — {problem}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
