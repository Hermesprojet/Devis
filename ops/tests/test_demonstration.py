"""Les garde-fous de la démonstration locale, éprouvés sans démon Docker.

Ce que ces tests protègent tient en une phrase : la démonstration est une
composition volontairement ouverte — connexion sans mot de passe, secret
public — et elle porte une commande d'effacement. Deux choses qu'il ne faut
jamais se tromper : ce qu'elle expose, et ce qu'elle détruit.

Ils lisent les fichiers plutôt que de lancer Docker, parce que ce sont les
fichiers qui décident. Un test qui démarrerait la pile prouverait qu'une
version marche ; ceux-ci prouvent qu'aucune version ne peut déraper.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parents[2]
COMPOSITION = RACINE / "infra" / "docker-compose.demo.yml"
SCRIPT = RACINE / "ops" / "demonstration.sh"
MAKEFILE = RACINE / "Makefile"

PROJET = "metreo-demo"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSITION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Ce que la démonstration expose
# --------------------------------------------------------------------------


def test_every_published_port_is_bound_to_the_loopback(compose: dict) -> None:
    """Sans le préfixe `127.0.0.1:`, Docker publie sur 0.0.0.0.

    La démonstration deviendrait alors joignable depuis tout le réseau local —
    un café, un hôtel, un plateau ouvert — avec une connexion qui ne demande
    aucun mot de passe. C'est le contrôle le plus important du fichier.
    """
    publies = []
    for nom, service in compose["services"].items():
        for port in service.get("ports") or []:
            publies.append((nom, port))

    assert publies, "aucun port publié : la démonstration serait inutilisable"
    for nom, port in publies:
        assert str(port).startswith("127.0.0.1:"), (
            f"le service « {nom} » publie « {port} » sans se limiter à la boucle "
            f"locale : la démonstration serait exposée au réseau"
        )


def test_the_database_publishes_no_port_at_all(compose: dict) -> None:
    """Rien dans la démonstration n'exige d'atteindre la base depuis l'hôte."""
    assert not (compose["services"]["db"].get("ports") or []), (
        "la base publie un port : surface inutile, avec un mot de passe trivial"
    )


def test_the_composition_carries_a_visible_warning(compose: dict) -> None:
    """L'avertissement doit être dans le fichier, pas seulement dans un README.

    Celui qui adapte cette composition l'ouvre ; il ne relit pas la
    documentation.
    """
    entete = COMPOSITION.read_text(encoding="utf-8")[:2000].upper()
    assert "NE JAMAIS EXPOSER SUR INTERNET" in entete


def test_the_driver_prints_the_warning_on_start(script: str) -> None:
    assert "NE JAMAIS EXPOSER SUR INTERNET" in script.upper()
    assert "avertissement" in script


# --------------------------------------------------------------------------
# Mode de développement, et rien d'autre
# --------------------------------------------------------------------------


def test_every_service_runs_in_development_mode(compose: dict) -> None:
    """La démonstration ne doit jamais se prendre pour une préproduction.

    Avec `staging`, l'API refuserait `dev-login` et personne ne pourrait
    entrer — la démonstration serait cassée d'une façon difficile à
    diagnostiquer.
    """
    texte = COMPOSITION.read_text(encoding="utf-8")
    assert "METREO_ENVIRONMENT: development" in texte
    for interdit in ("staging", "production"):
        assert not re.search(rf"METREO_ENVIRONMENT:\s*{interdit}", texte), (
            f"la démonstration déclare l'environnement « {interdit} »"
        )


def test_the_environment_cannot_be_overridden_from_outside(compose: dict) -> None:
    """Aucune substitution `${...}` sur les réglages sensibles.

    Une valeur surchargeable depuis l'environnement de l'appelant ferait de
    cette composition ouverte quelque chose qu'on pourrait pousser en
    préproduction par accident.
    """
    texte = COMPOSITION.read_text(encoding="utf-8")
    for reglage in ("METREO_ENVIRONMENT", "METREO_AUTH_MODE", "METREO_CORS_ORIGINS"):
        ligne = re.search(rf"{reglage}:\s*(.+)", texte)
        assert ligne, f"{reglage} absent de la composition"
        assert "${" not in ligne.group(1), f"{reglage} est surchargeable depuis l'environnement"


def test_the_authentication_mode_is_the_development_one(compose: dict) -> None:
    assert "METREO_AUTH_MODE: dev" in COMPOSITION.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Migrations et jeu de démonstration
# --------------------------------------------------------------------------


def test_migrations_are_a_dedicated_one_shot_task(compose: dict) -> None:
    """Ni dans la commande de l'API, ni relancée en boucle."""
    migrate = compose["services"]["migrate"]
    assert migrate["restart"] == "no", "la tâche de migration serait rejouée en boucle"
    assert "upgrade" in " ".join(migrate["command"])

    api = compose["services"]["api"]
    assert "command" not in api, (
        "l'API porte une commande : les migrations y seraient rejouées à chaque "
        "redémarrage du conteneur"
    )


def test_the_api_waits_for_the_migration_to_have_succeeded(compose: dict) -> None:
    depend = compose["services"]["api"]["depends_on"]
    assert depend["seed"]["condition"] == "service_completed_successfully"
    assert depend["db"]["condition"] == "service_healthy"


def test_the_demo_data_is_loaded_only_once(compose: dict) -> None:
    """Un témoin dans le volume, testé avant de charger.

    Sans lui, chaque `demo-up` rechargerait le jeu par-dessus le travail de la
    personne en train d'essayer le produit.
    """
    commande = " ".join(compose["services"]["seed"]["command"])
    assert ".demonstration-chargee" in commande
    assert "metreo_api.seed" in commande
    # Le témoin est lu AVANT d'être écrit, sinon il ne garde rien.
    assert commande.index("if [ -f") < commande.index("metreo_api.seed")


def test_the_marker_lives_with_the_data_it_guards(compose: dict) -> None:
    """Dans le volume de stockage : il survit à `down`, disparaît à `reset`.

    Placé ailleurs, il désynchroniserait — jeu effacé mais témoin présent, donc
    une démonstration vide qui refuse de se recharger.
    """
    montages = compose["services"]["seed"]["volumes"]
    assert any(m.startswith("metreo-demo-stockage:") for m in montages)


# --------------------------------------------------------------------------
# Volumes : des noms qui n'appartiennent qu'à cette pile
# --------------------------------------------------------------------------


def test_the_project_name_is_specific(compose: dict) -> None:
    assert compose["name"] == PROJET


def test_no_volume_carries_a_generic_name(compose: dict) -> None:
    """`db-data` ou `data` ne se distinguent pas d'un volume voisin.

    C'est ce qui rend une commande de ménage tapée à la main dangereuse.
    """
    generiques = {"data", "db-data", "db", "storage", "pgdata", "postgres", "cache"}
    for nom in compose["volumes"]:
        assert nom not in generiques, f"le volume « {nom} » porte un nom générique"
        assert nom.startswith("metreo-demo-"), (
            f"le volume « {nom} » ne s'annonce pas comme celui de la démonstration"
        )


# --------------------------------------------------------------------------
# `down` conserve, `reset` demande, et ne vise que cette pile
# --------------------------------------------------------------------------


def test_down_does_not_remove_volumes(script: str) -> None:
    """`docker compose down -v` effacerait les données sans le dire.

    Quelqu'un qui a saisi un devis d'essai doit le retrouver le lendemain.
    """
    corps = script[script.index("commande_down()") : script.index("commande_reset()")]
    assert "down" in corps
    assert " -v" not in corps and "--volumes" not in corps, (
        "« down » supprime les volumes : arrêter n'est pas effacer"
    )


def test_the_project_name_is_not_readable_from_the_environment(script: str) -> None:
    """Surchargeable, il ferait de `reset` une commande capable de tout viser."""
    ligne = re.search(r"^PROJET=(.+)$", script, re.MULTILINE)
    assert ligne, "le nom de projet n'est pas défini en clair"
    assert "${" not in ligne.group(1) and "$(" not in ligne.group(1)
    assert PROJET in ligne.group(1)


def test_reset_names_its_volumes_one_by_one(script: str) -> None:
    """Une liste explicite, pas un filtre.

    Un filtre qui déraperait emporterait des volumes voisins, et personne ne
    relit un filtre avant de taper la commande.
    """
    assert "VOLUMES=(" in script
    for interdit in ("docker volume prune", "docker system prune", "-f dangling"):
        assert interdit not in script, f"« {interdit} » vise au-delà de la démonstration"


def test_reset_verifies_the_prefix_at_the_point_of_deletion(script: str) -> None:
    """La garde est au contact de la commande destructrice, pas en amont.

    Placée seulement à la construction de la liste, une erreur ultérieure la
    contournerait.
    """
    corps = script[script.index("commande_reset()") :]
    position_garde = corps.index('"${PROJET}_"*')
    position_suppression = corps.index("docker volume rm")
    assert position_garde < position_suppression


def test_reset_refuses_without_confirmation_and_deletes_nothing() -> None:
    """Exécution réelle du script, entrée non interactive et sans confirmation.

    Docker n'est pas nécessaire : le refus doit tomber avant tout appel.
    """
    resultat = subprocess.run(
        ["bash", str(SCRIPT), "reset"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=str(RACINE),
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )
    assert resultat.returncode != 0, "« reset » a réussi sans confirmation"
    sortie = resultat.stdout + resultat.stderr
    assert "docker volume rm" not in sortie
    # Le refus doit expliquer, sinon on le contourne à l'aveugle.
    assert "Refus" in sortie or "ne répond pas" in sortie


def test_reset_refuses_a_wrong_confirmation_word() -> None:
    resultat = subprocess.run(
        ["bash", str(SCRIPT), "reset"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=str(RACINE),
        env={**os.environ, "DEMO_RESET_CONFIRME": "oui"},
    )
    assert resultat.returncode != 0
    assert "Refus" in resultat.stdout + resultat.stderr


def test_an_unknown_subcommand_is_refused() -> None:
    resultat = subprocess.run(
        ["bash", str(SCRIPT), "supprime-tout"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=str(RACINE),
    )
    assert resultat.returncode == 2
    assert "usage" in (resultat.stdout + resultat.stderr).lower()


# --------------------------------------------------------------------------
# Ce que `up` promet
# --------------------------------------------------------------------------


def test_up_waits_on_readiness_not_liveness(script: str) -> None:
    """`/live` serait vert avant que la base ne réponde.

    Annoncer « c'est prêt » à ce moment envoie la personne sur une page qui
    échoue, et lui fait croire que le produit est cassé.
    """
    corps = script[script.index("commande_up()") : script.index("commande_status()")]
    assert "/ready" in corps
    assert "attendre " in corps


def test_up_prints_the_url_and_the_demo_accounts(script: str) -> None:
    corps = script[script.index("commande_up()") : script.index("commande_status()")]
    assert "URL_WEB" in corps
    assert "COMPTES" in corps
    assert "admin@dubois.demo" in script


def test_the_makefile_exposes_the_four_documented_targets() -> None:
    texte = MAKEFILE.read_text(encoding="utf-8")
    for cible in ("demo-up", "demo-status", "demo-down", "demo-reset"):
        assert re.search(rf"^{cible}:", texte, re.MULTILINE), f"cible « {cible} » absente"


def test_every_make_target_delegates_to_the_reviewed_script() -> None:
    """Aucune cible ne doit appeler `docker` directement.

    La logique de refus n'existe qu'ici ; une cible qui la contournerait la
    rendrait facultative.
    """
    texte = MAKEFILE.read_text(encoding="utf-8")
    for cible in ("demo-up", "demo-status", "demo-down", "demo-reset"):
        bloc = re.search(rf"^{cible}:.*?\n(\t.*\n)+", texte, re.MULTILINE)
        assert bloc, f"cible « {cible} » sans recette"
        assert "ops/demonstration.sh" in bloc.group(0)


# --------------------------------------------------------------------------
# Sauvegarde : ce qu'elle refuse d'envoyer
# --------------------------------------------------------------------------

SAUVEGARDE = RACINE / "ops" / "sauvegarder.sh"


def test_backup_refuses_to_send_an_unencrypted_archive_to_a_third_party() -> None:
    """Le contrôle porte sur l'ordre : le refus AVANT l'envoi.

    Le script annonçait « archive NON chiffrée, locale seulement », puis
    déposait quand même cette archive chez le tiers configuré. La phrase
    rassurait sur ce qui n'arrivait pas — un dump PostgreSQL en clair chez un
    hébergeur, c'est la base entière.
    """
    texte = SAUVEGARDE.read_text(encoding="utf-8")
    bloc = texte[texte.index('if [[ -n "${BACKUP_DESTINATION:-}" ]]') :]
    refus = bloc.index("dépôt vers un tiers demandé sans chiffrement")
    for envoi in ("aws s3 cp", "rsync -a"):
        assert refus < bloc.index(envoi), (
            f"« {envoi} » peut s'exécuter avant le refus : une archive en clair "
            f"partirait chez le tiers"
        )


def test_backup_still_allows_a_local_unencrypted_archive() -> None:
    """Sans destination distante, exiger le chiffrement ne protégerait rien.

    Cela bloquerait en revanche l'exercice de restauration, qui est la seule
    façon de savoir qu'une sauvegarde vaut quelque chose.
    """
    texte = SAUVEGARDE.read_text(encoding="utf-8")
    # Le refus est gardé par DEUX conditions imbriquées : une destination
    # distante posée, et pas de destinataire de chiffrement. Sans la première,
    # une archive locale en clair serait refusée elle aussi.
    bloc = texte[
        texte.index('if [[ -n "${BACKUP_DESTINATION:-}" ]]') : texte.index(
            "dépôt vers un tiers demandé sans chiffrement"
        )
    ]
    assert 'if [[ -z "${BACKUP_AGE_RECIPIENT:-}" ]]' in bloc, (
        "le refus ne dépend pas de l'absence de chiffrement"
    )


# --------------------------------------------------------------------------
# Les migrations, depuis n'importe quel répertoire
# --------------------------------------------------------------------------

ALEMBIC_INI = RACINE / "apps" / "api" / "alembic.ini"


def test_the_migration_paths_do_not_depend_on_the_working_directory() -> None:
    """Alembic résout un chemin relatif contre le répertoire d'APPEL.

    La commande de préproduction — `alembic -c apps/api/alembic.ini upgrade
    head`, lancée depuis la racine, qui est le WORKDIR de l'image — cherchait
    `./alembic` et sortait en 255. La tâche de migration, celle qui doit
    réussir avant que l'API ne démarre, ne pouvait pas fonctionner dans le
    conteneur.

    La validation de syntaxe des compositions ne pouvait pas le voir : il a
    fallu une exécution réelle.
    """
    texte = ALEMBIC_INI.read_text(encoding="utf-8")
    for reglage in ("script_location", "prepend_sys_path"):
        ligne = re.search(rf"^{reglage}\s*=\s*(.+)$", texte, re.MULTILINE)
        assert ligne, f"{reglage} absent de alembic.ini"
        valeur = ligne.group(1).strip()
        assert valeur.startswith("%(here)s") or valeur.startswith("/"), (
            f"{reglage} = « {valeur} » est relatif au répertoire d'appel : la "
            f"tâche de migration échouera depuis le WORKDIR de l'image"
        )


def test_the_migration_task_invokes_alembic_with_that_file() -> None:
    """La composition doit pointer sur le fichier, pas sur un chemin deviné."""
    compose_staging = yaml.safe_load(
        (RACINE / "infra" / "docker-compose.staging.yml").read_text(encoding="utf-8")
    )
    commande = " ".join(compose_staging["services"]["migrate"]["command"])
    assert "apps/api/alembic.ini" in commande
    assert "upgrade" in commande and "head" in commande
