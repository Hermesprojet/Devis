#!/usr/bin/env python3
"""La montée du schéma depuis la version EN SERVICE, éprouvée et non supposée.

    python3 scripts/epreuve_montee_depuis_preproduction.py \\
        [--depuis origin/main] [--admin-url postgresql+psycopg://…]

**Pourquoi cette épreuve existe, et ce qu'elle ajoute aux autres.**

Trois contrôles voisins vivent déjà dans le dépôt, et aucun ne pose cette
question-ci :

- `scripts/migration_roundtrip.py` monte une base **vide** jusqu'à la tête,
  redescend et remonte. Il prouve que la chaîne est réversible, pas qu'elle
  s'applique à ce qui tourne ;
- `scripts/schema_drift_gate.py` compare le schéma migré aux modèles. Il
  prouve l'arrivée, pas le chemin ;
- `scripts/epreuve_retour_arriere.py` fait tourner l'ANCIEN code sur le schéma
  NEUF. Il prouve la sortie de secours, pas l'entrée.

Ce qui manquait : **une base au schéma de la préproduction, migrée par les
fichiers du candidat.** C'est le geste exact du déploiement, et c'est le seul
qui échoue si une révision du candidat suppose une table que la version en
service n'a pas — ou si un `down_revision` cite un maillon au lieu d'une tête,
ce qui laisse la base à deux têtes sans que rien d'autre ne le voie.

**Ce que le script fait, dans l'ordre.**

1. Il sort la révision `--depuis` dans un arbre de travail séparé, et monte une
   base vide jusqu'à la tête de CETTE révision. C'est le schéma en service.
2. Il relève cette tête : c'est le point de départ réel, et non un numéro
   recopié à la main.
3. Il monte la MÊME base jusqu'à la tête du candidat, **avec les fichiers de
   l'arbre courant**, et liste les révisions appliquées.
4. Il vérifie qu'une seule tête existe, et que la base est bien arrivée dessus.

Sans `--admin-url`, l'épreuve se joue sur un fichier SQLite jetable, et se
rejoue en quelques secondes sans rien installer. Avec, elle se joue sur un vrai
PostgreSQL, dans une base **créée par ce run** et détruite dans un `finally` :
c'est le seul moteur qui porte `ON DELETE SET NULL (colonne)` et les
déclencheurs d'immuabilité, donc le seul où la montée prouve tout.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import jeu_d_essai_de_montee

RACINE = Path(__file__).resolve().parents[1]
API = RACINE / "apps" / "api"

#: La révision dont on part. `origin/main` est ce qui tourne en préproduction
#: tant que la tranche des plans n'est pas fusionnée.
DEPUIS_PAR_DEFAUT = "origin/main"

#: `METREO_ENVIRONMENT=test` garde le mode d'authentification de développement.
#: Le secret n'ouvre rien : aucune API n'est démarrée ici, mais la
#: configuration refuse de se charger sans lui.
ENVIRONNEMENT = {
    "METREO_ENVIRONMENT": "test",
    "METREO_AUTH_MODE": "dev",
    "METREO_JWT_SECRET": "epreuve-de-montee-sans-valeur-0123456789",
}


def _alembic(arbre: Path, *arguments: str, url: str) -> subprocess.CompletedProcess[str]:
    """Lance Alembic avec les fichiers d'UN arbre donné, sur UNE base donnée.

    C'est tout le mécanisme de l'épreuve : les deux appels ne diffèrent que par
    l'arbre, et la base est la même.
    """
    api = arbre / "apps" / "api"
    environnement = {
        **os.environ,
        **ENVIRONNEMENT,
        "METREO_DATABASE_URL": url,
        "PYTHONPATH": str(api / "src"),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(api / "alembic.ini"), *arguments],
        cwd=api,
        env=environnement,
        capture_output=True,
        text=True,
        check=False,
    )


def _exiger(resultat: subprocess.CompletedProcess[str], quoi: str) -> str:
    if resultat.returncode != 0:
        print(f"ÉCHEC — {quoi}", file=sys.stderr)
        print(resultat.stdout, file=sys.stderr)
        print(resultat.stderr, file=sys.stderr)
        raise SystemExit(1)
    return (resultat.stdout or "").strip()


def _revisions_appliquees(sortie: str) -> list[str]:
    """Les révisions qu'une montée vient de jouer, dans l'ordre.

    Lues dans le journal d'Alembic plutôt que déduites de l'historique : ce
    qu'on veut savoir est ce qui s'est VRAIMENT appliqué à cette base-ci.
    """
    appliquees: list[str] = []
    for ligne in sortie.splitlines():
        if "Running upgrade" not in ligne:
            continue
        morceau = ligne.split("Running upgrade", 1)[1].strip()
        fleche = morceau.split("->", 1)
        if len(fleche) == 2:
            appliquees.append(fleche[1].split(",", 1)[0].strip())
    return appliquees


def _lignes(releve: dict[str, Any]) -> int:
    """Combien de lignes métier porte un relevé, toutes tables confondues."""
    return sum(len(v) for v in releve.values() if isinstance(v, list))


def _arbre_de(revision: str, dossier: Path) -> None:
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(dossier), revision],
        cwd=RACINE,
        check=True,
    )


def _creer_la_base(admin_url: str) -> tuple[str, str]:
    """Une base PostgreSQL au nom tiré au hasard, créée par ce run.

    Le `CREATE DATABASE` est la preuve d'appartenance : PostgreSQL le refuse si
    le nom existe déjà. C'est le même geste que `migration_roundtrip.py`, et
    pour la même raison — une épreuve qui détruirait une base préexistante
    serait une arme, pas un contrôle.
    """
    from sqlalchemy import create_engine, text

    nom = f"metreo_montee_{secrets.token_hex(8)}"
    moteur = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with moteur.connect() as connexion:
        connexion.execute(text(f'CREATE DATABASE "{nom}"'))
    moteur.dispose()
    base, _, _ = admin_url.rpartition("/")
    return nom, f"{base}/{nom}"


def _detruire_la_base(admin_url: str, nom: str) -> None:
    from sqlalchemy import create_engine, text

    moteur = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with moteur.connect() as connexion:
        connexion.execute(text(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)'))
    moteur.dispose()


def _jeu_d_essai(
    arbre: Path, geste: str, url: str, *options: str
) -> subprocess.CompletedProcess[str]:
    """Un geste du jeu d'essai, joué avec les fichiers d'UN arbre donné.

    L'arbre décide tout : `creer` lancé sur l'arbre de la préproduction écrit
    avec l'ANCIEN code — c'est ce qui fait de ces données des données
    existantes, et non des données que le candidat vient de poser. `verifier`
    lancé sur l'arbre courant relit avec le NEUF.

    Le SCRIPT, lui, reste celui de l'arbre courant : c'est un outil, pas une
    donnée. S'il appelait un jour une API que la préproduction n'a pas, il
    échouerait bruyamment ici — ce qui est le bon endroit pour l'apprendre.
    """
    environnement = {
        **os.environ,
        **ENVIRONNEMENT,
        "PYTHONPATH": os.pathsep.join(
            str(arbre / chemin)
            for chemin in (
                "apps/api/src",
                "packages/domain/src",
                "packages/contracts/src",
            )
        ),
    }
    return subprocess.run(
        [
            sys.executable,
            str(RACINE / "scripts" / "jeu_d_essai_de_montee.py"),
            geste,
            "--url",
            url,
            *options,
        ],
        cwd=RACINE,
        env=environnement,
        capture_output=True,
        text=True,
        check=False,
    )


def epreuve(depuis: str, admin_url: str | None, avec_donnees: bool = False) -> int:
    brouillon = Path(tempfile.mkdtemp(prefix="metreo-montee-"))
    arbre_ancien = brouillon / "preproduction"
    base_postgres: str | None = None
    try:
        if admin_url:
            base_postgres, url = _creer_la_base(admin_url)
            moteur = "PostgreSQL"
        else:
            url = f"sqlite+pysqlite:///{brouillon / 'montee.sqlite3'}"
            moteur = "SQLite"
        print(f"moteur : {moteur}")

        _arbre_de(depuis, arbre_ancien)
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=arbre_ancien,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        print(f"version de départ : {depuis} ({sha})")

        # 1. Le schéma de la préproduction, monté par SES PROPRES fichiers.
        _exiger(_alembic(arbre_ancien, "upgrade", "head", url=url), "montée de la préproduction")
        depart = _exiger(_alembic(arbre_ancien, "current", url=url), "tête de la préproduction")
        depart = depart.splitlines()[-1].split()[0] if depart else ""
        print(f"schéma en service : {depart}")

        # 1 bis. Un chantier complet, écrit par l'ANCIEN code sur ce schéma.
        #
        # Sans lui, la montée se joue sur une base vide, et une base vide ne
        # perd rien. Les quatre accidents qui coûtent cher — une colonne
        # retypée qui tronque, une contrainte qui vide une clé étrangère, un
        # `server_default` qui réécrit une ligne, un déclencheur qui refuse une
        # écriture ultérieure — sont tous invisibles tant qu'il n'y a rien à
        # abîmer.
        avant: dict[str, Any] | None = None
        if avec_donnees:
            creation = _jeu_d_essai(arbre_ancien, "creer", url)
            print(_exiger(creation, "création du jeu d'essai sous l'ancienne version"))
            avant = jeu_d_essai_de_montee.relever(url)
            print(f"jeu d'essai relevé avant la montée : {_lignes(avant)} ligne(s)")

        # 2. La montée du candidat, sur CETTE base.
        montee = _alembic(RACINE, "upgrade", "head", url=url)
        sortie = _exiger(montee, "montée du candidat")
        appliquees = _revisions_appliquees(sortie + "\n" + (montee.stderr or ""))
        print(f"révisions appliquées : {len(appliquees)}")
        for revision in appliquees:
            print(f"  → {revision}")

        # 3. Une seule tête, et la base y est.
        tetes = [
            ligne.split()[0]
            for ligne in _exiger(_alembic(RACINE, "heads", url=url), "têtes").splitlines()
            if ligne.strip() and not ligne.startswith("INFO")
        ]
        if len(tetes) != 1:
            print(f"ÉCHEC — {len(tetes)} tête(s) : {tetes}", file=sys.stderr)
            return 1
        arrivee = _exiger(_alembic(RACINE, "current", url=url), "tête atteinte")
        arrivee = arrivee.splitlines()[-1].split()[0] if arrivee else ""
        if arrivee != tetes[0]:
            print(f"ÉCHEC — la base est en {arrivee}, la tête est {tetes[0]}", file=sys.stderr)
            return 1

        print(f"tête unique : {tetes[0]}")
        print(f"montée valide : {depart} → {arrivee}, {len(appliquees)} révision(s).")

        # 4. Ce que les données sont devenues.
        if avec_donnees and avant is not None:
            apres = jeu_d_essai_de_montee.relever(url)
            ecarts = jeu_d_essai_de_montee.comparer(avant, apres)
            if ecarts:
                print(
                    f"ÉCHEC — la montée a changé {len(ecarts)} valeur(s) métier :",
                    file=sys.stderr,
                )
                for ecart in ecarts:
                    print(f"  · {ecart}", file=sys.stderr)
                return 1
            print(f"données conservées : {_lignes(apres)} ligne(s), aucune différence")

            verification = _jeu_d_essai(RACINE, "verifier", url)
            print(verification.stdout.rstrip())
            if verification.returncode != 0:
                print(verification.stderr, file=sys.stderr)
                return 1
        return 0
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(arbre_ancien)],
            cwd=RACINE,
            check=False,
            capture_output=True,
        )
        if base_postgres and admin_url:
            _detruire_la_base(admin_url, base_postgres)
            print(f"base supprimée : {base_postgres}")
        shutil.rmtree(brouillon, ignore_errors=True)


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--depuis",
        default=DEPUIS_PAR_DEFAUT,
        help="la révision git dont on part — ce qui tourne en préproduction",
    )
    analyseur.add_argument(
        "--admin-url",
        default=None,
        help="une URL PostgreSQL d'administration ; sans elle, l'épreuve se joue sur SQLite",
    )
    analyseur.add_argument(
        "--avec-donnees",
        action="store_true",
        help=(
            "écrire un chantier complet sous l'ancienne version avant de monter, "
            "et vérifier après ce qu'il est devenu"
        ),
    )
    arguments = analyseur.parse_args()
    return epreuve(arguments.depuis, arguments.admin_url, arguments.avec_donnees)


if __name__ == "__main__":
    raise SystemExit(main())
