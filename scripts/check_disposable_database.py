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
from urllib.parse import unquote, urlsplit

#: A database name carrying one of these tokens is understood as throw-away.
DISPOSABLE_TOKENS = frozenset({"test", "tests", "gate", "ci", "tmp", "temp", "scratch"})

#: These win over the list above, whatever else the name carries.
FORBIDDEN_TOKENS = frozenset({"prod", "prods", "production", "live", "prd", "real"})


def database_name(url: str) -> str:
    """The database name alone — no host, no user, no password, no query.

    SQLAlchemy URLs carry a driver in the scheme (``postgresql+psycopg``),
    which ``urlsplit`` handles, and the test harness appends
    ``?options=-csearch_path=…``, which must not be read as part of the name.
    """
    parts = urlsplit(url)
    path = unquote(parts.path).lstrip("/")
    # A SQLite file URL names a path; its final component is the database.
    return path.rsplit("/", 1)[-1]


def tokens(name: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9]+", name.lower()) if token]


def refusal(url: str) -> str | None:
    """Return why this URL is refused, or ``None`` when it is acceptable."""
    if not url.strip():
        return "aucune URL fournie"

    name = database_name(url)
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
