"""Refuse un schéma qui ne correspond plus aux modèles, dans une base à ce run.

`alembic check` seul ne suffit pas comme porte de CI, et lancé contre une base
arbitraire il est même dangereux : il faut d'abord une base **dont ce run est
propriétaire**, sans quoi la porte s'exécute sur ce qui traîne — le schéma d'une
autre étape, une base de production mal pointée, ou rien du tout.

Ce que cette porte enchaîne, dans cet ordre :

1. l'URL d'administration doit mener à un PostgreSQL **prouvé** — dialecte
   vérifié, connexion réellement ouverte, `SELECT version()` interrogé ;
2. une base est créée par ce run. Le `CREATE DATABASE` **est** la preuve de
   propriété : PostgreSQL le refuse si le nom existe déjà, donc atteindre la
   suite signifie que ce run l'a créée ;
3. la chaîne doit avoir **exactement une tête**. Deux têtes font échouer
   `alembic upgrade head` avec « Multiple head revisions are present », et
   toutes les commandes du dépôt utilisent le singulier ;
4. `alembic upgrade head` ;
5. `alembic check` : la moindre opération proposée est un échec ;
6. la base est détruite en sortie, succès ou échec — c'est un `finally`, et
   seule une base que ce run a créée est supprimée.

Aucun identifiant ne sort d'ici : les URL ne paraissent que passées par
`redacted()`, qui ne garde que dialecte, hôte, port et nom de base.

Pourquoi PostgreSQL et pas SQLite : trois clés composites portent
`ON DELETE SET NULL (colonne)`, une syntaxe que SQLite refuse d'analyser, et
dont la forme nue y échouerait sur `organization_id`, NOT NULL. SQLite ne
représente donc pas fidèlement ces actions, et une porte qui l'accepterait
laisserait passer exactement la dérive qu'elle est censée fermer.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _url_safety import NotPostgreSQL, UnsafeUrl, redacted, verified_postgresql_dialect
from migration_roundtrip import API, owned_database


def _alembic(url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Lancer Alembic sur cette URL, sans jamais l'écrire dans la sortie.

    Un sous-processus plutôt qu'un appel direct : `alembic check` signale une
    dérive par une exception que l'on veut traduire en code de sortie, et non
    voir remonter en trace. L'URL passe par l'environnement, jamais par la
    ligne de commande — une ligne de commande se retrouve dans les journaux et
    dans `ps`.
    """
    import os

    environment = dict(os.environ, METREO_DATABASE_URL=url, PYTHONPATH="src")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=API,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def heads() -> list[str]:
    """Les têtes de la chaîne, lues sans toucher à aucune base."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))
    return list(ScriptDirectory.from_config(cfg).get_heads())


def gate(admin_url: str) -> int:
    try:
        banner = verified_postgresql_dialect(admin_url)
    except NotPostgreSQL as error:
        print(f"porte refusée — {error}", file=sys.stderr)
        print(
            "Cette porte exige un PostgreSQL réel : trois clés composites portent "
            "`ON DELETE SET NULL (colonne)`, que SQLite ne sait pas représenter.",
            file=sys.stderr,
        )
        return 2
    print(f"serveur vérifié : {banner.split(' on ')[0]}")

    presentes = heads()
    if len(presentes) != 1:
        print(
            f"porte refusée — la chaîne des migrations a {len(presentes)} têtes : "
            f"{sorted(presentes)}. `alembic upgrade head` échouerait sur « Multiple "
            "head revisions are present », et toutes les commandes du dépôt "
            "utilisent le singulier.",
            file=sys.stderr,
        )
        return 3
    print(f"tête unique : {presentes[0]}")

    try:
        return _run_in_a_database_this_run_owns(admin_url)
    except UnsafeUrl as error:
        # Une URL capable de déplacer la connexion ailleurs que là où son chemin
        # le dit ferait tourner la porte — et le DROP final — sur une base que
        # ce run n'a pas créée. Refus nommé, jamais une trace.
        print(f"porte refusée — {error}", file=sys.stderr)
        return 6


def _run_in_a_database_this_run_owns(admin_url: str) -> int:
    with owned_database(admin_url) as url:
        print(f"base de ce run : {redacted(url)}")

        montee = _alembic(url, "upgrade", "head")
        if montee.returncode != 0:
            print("porte refusée — `alembic upgrade head` a échoué :", file=sys.stderr)
            print(montee.stderr.strip()[-2000:], file=sys.stderr)
            return 4

        controle = _alembic(url, "check")
        if controle.returncode != 0:
            print(
                "porte refusée — le schéma migré ne correspond pas aux modèles.\n"
                "Une action référentielle, une colonne ou une contrainte existe "
                "d'un seul côté. Corriger le modèle OU écrire la révision qui "
                "manque ; ne pas ignorer ce contrôle.",
                file=sys.stderr,
            )
            print(controle.stdout.strip()[-4000:], file=sys.stderr)
            print(controle.stderr.strip()[-4000:], file=sys.stderr)
            return 5

    print("porte franchie : une tête, montée propre, aucune opération proposée.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url",
        required=True,
        help=(
            "URL PostgreSQL d'administration. Ce run y CRÉE sa propre base et ne "
            "détruit que celle-là."
        ),
    )
    return gate(parser.parse_args().admin_url)


if __name__ == "__main__":
    raise SystemExit(main())
