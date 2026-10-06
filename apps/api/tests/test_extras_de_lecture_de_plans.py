"""Les extras de lecture de plans sont-ils là, et où devraient-ils l'être ?

**Le défaut que ce fichier ferme, et il a duré plusieurs tranches.**

Les tests de lecture de plans commencent par `pytest.importorskip` : sans
`ezdxf` ou sans `pypdfium2`, ils se SAUTENT. C'est légitime — les deux extras
sont optionnels dans le manifeste, et une API qui n'ouvre aucun plan n'a pas
besoin de numpy. Mais une suite qui saute ne prouve rien, et c'est écrit noir
sur blanc dans le workflow lui-même :

    `plans` porte ezdxf ET Pillow […] sans lui, les tests de lecture de plans
    se SAUTERAIENT, et une suite qui saute ne prouve rien.

L'extra `plans` a donc été ajouté aux ateliers. **L'extra `pdf` ne l'a jamais
été.** Les tests du lecteur PDF, de la mesure, de la calibration et du cache
de tuiles se sautaient tous en intégration continue, pendant que la CI
affichait vert. Rien ne pouvait le dire : un ignoré ne fait pas échouer, et le
décompte total ne se compare à rien.

Deux contrôles, et ils regardent dans deux directions :

1. l'environnement COURANT, pour que `METREO_REQUIRE_PLAN_EXTRAS=1` transforme
   un ignoré en rouge — la même mécanique que `METREO_REQUIRE_WEB_INSTALL` ;
2. les FICHIERS d'installation, pour qu'un atelier qui lance la suite sans les
   extras soit refusé avant même d'avoir tourné.

Le second est le plus important : c'est lui qui aurait parlé.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[3]

#: Les modules que les extras apportent, et l'extra qui les apporte.
#:
#: `pypdfium2` n'est pas une commodité : `services/rendu_pdf.py` et
#: `services/lecture_pdf.py` l'importent à l'appel, et sans lui un PDF déposé
#: est refusé à l'analyse. L'image d'exécution l'installe ; la CI doit donc
#: l'installer aussi, sans quoi elle éprouve un autre logiciel que celui qui
#: est livré.
MODULES_PAR_EXTRA: dict[str, tuple[str, ...]] = {
    "plans": ("ezdxf", "PIL"),
    "pdf": ("pypdfium2",),
}

#: Les ateliers d'intégration continue qui LANCENT la suite de tests, et le
#: motif qui y cherche l'installation de l'API.
#:
#: Un atelier qui ne lance pas la suite — le banc de connexion, par exemple —
#: n'a pas besoin des extras, et les lui imposer alourdirait son installation
#: sans rien prouver.
ATELIERS_QUI_LANCENT_LA_SUITE: tuple[str, ...] = (
    "api-sqlite",
    "api-postgres",
    "e2e",
)


def test_the_plan_extras_are_importable_when_the_caller_says_they_must_be() -> None:
    """`METREO_REQUIRE_PLAN_EXTRAS=1` fait d'un ignoré une erreur.

    Sans la variable, l'absence d'un extra reste un ignoré explicite : un
    développeur qui ne travaille pas sur les plans n'a pas à installer numpy
    et fonttools pour lancer la suite.

    Avec, l'appelant AFFIRME les avoir installés, et un ignoré devient un
    mensonge sur ce qui a été éprouvé.
    """
    exige = os.environ.get("METREO_REQUIRE_PLAN_EXTRAS") == "1"
    manquants = [
        f"{module} (extra « {extra} »)"
        for extra, modules in MODULES_PAR_EXTRA.items()
        for module in modules
        if importlib.util.find_spec(module) is None
    ]
    if not manquants:
        return
    if exige:
        pytest.fail(
            "METREO_REQUIRE_PLAN_EXTRAS=1 mais ces modules manquent : "
            + ", ".join(manquants)
            + " — installez « ./apps/api[plans,pdf] », sans quoi les tests de "
            "lecture de plans se sautent et la suite ne prouve rien."
        )
    pytest.skip(
        "extras de lecture de plans absents : "
        + ", ".join(manquants)
        + " ; posez METREO_REQUIRE_PLAN_EXTRAS=1 pour en faire une erreur"
    )


def _installations_de_l_api(texte: str) -> dict[str, set[str]]:
    """Les extras installés par chaque atelier du workflow, lus dans le YAML.

    Lu au motif plutôt qu'avec un analyseur YAML : ce qu'on cherche est une
    ligne de commande `pip install`, pas une structure. Un analyseur rendrait
    la chaîne entière, qu'il faudrait de toute façon relire au motif.

    Le nom de l'atelier est la dernière clé de premier niveau rencontrée avant
    la ligne : c'est l'ordre du fichier, et il est stable.
    """
    extras_par_atelier: dict[str, set[str]] = {}
    atelier = ""
    motif = re.compile(r"apps/api\[([^\]]*)\]")
    for ligne in texte.splitlines():
        entete = re.match(r"^  ([a-z0-9][a-z0-9-]*):\s*$", ligne)
        if entete:
            atelier = entete.group(1)
            continue
        trouve = motif.search(ligne)
        if trouve and atelier:
            extras_par_atelier.setdefault(atelier, set()).update(
                morceau.strip() for morceau in trouve.group(1).split(",")
            )
    return extras_par_atelier


def test_every_workflow_that_runs_the_suite_installs_every_plan_extra() -> None:
    """Un atelier qui lance la suite installe `plans` ET `pdf`.

    **C'est le contrôle qui aurait parlé.** L'atelier `api-sqlite` installait
    `[dev,plans]`, l'atelier `api-postgres` `[dev,plans,postgres]`, et celui du
    parcours navigateur `[plans]` : aucun ne portait `pdf`. Les quatre-vingts
    tests du lecteur PDF, de la mesure, de la calibration et du cache de
    tuiles se sautaient donc en silence, et le parcours navigateur échouait
    sur un écran qui ne s'affichait pas — parce que le serveur refusait le PDF,
    faute de `pypdfium2`.

    Le contrôle lit le workflow et non une liste recopiée : une liste
    recopiée serait une seconde vérité, et c'est déjà la divergence entre deux
    vérités qui a produit ce défaut.
    """
    workflow = RACINE / ".github" / "workflows" / "ci.yml"
    extras_par_atelier = _installations_de_l_api(workflow.read_text(encoding="utf-8"))

    attendus = set(MODULES_PAR_EXTRA)
    for atelier in ATELIERS_QUI_LANCENT_LA_SUITE:
        installes = extras_par_atelier.get(atelier)
        assert installes is not None, (
            f"l'atelier « {atelier} » n'installe plus l'API par ses manifestes, "
            "ou il a été renommé : ce contrôle ne protège plus rien"
        )
        manquants = attendus - installes
        assert not manquants, (
            f"l'atelier « {atelier} » installe {sorted(installes)} et il manque "
            f"{sorted(manquants)} : les tests correspondants s'y SAUTERAIENT, "
            "et un atelier vert qui saute ne prouve rien"
        )


def test_the_runtime_image_installs_at_least_what_the_suite_needs() -> None:
    """L'image d'exécution ne peut pas porter MOINS que ce qu'on éprouve.

    Le sens de l'inégalité compte. L'image peut porter plus — `postgres`, dont
    la suite SQLite n'a pas besoin. Elle ne peut pas porter moins : ce serait
    livrer un logiciel dont une partie n'a jamais été exercée, et c'est
    exactement le défaut qu'une tranche entière a servi à fermer — l'image
    installait `[postgres]` sans `plans`, démarrait, passait tous les contrôles
    de santé, et échouait sur le premier plan déposé.
    """
    dockerfile = RACINE / "infra" / "api.Dockerfile"
    trouve = re.search(r"apps/api\[([^\]]*)\]", dockerfile.read_text(encoding="utf-8"))
    assert trouve is not None, (
        "infra/api.Dockerfile n'installe plus l'API par ses manifestes : "
        "ce contrôle ne protège plus rien"
    )
    installes = {morceau.strip() for morceau in trouve.group(1).split(",")}
    manquants = set(MODULES_PAR_EXTRA) - installes
    assert not manquants, (
        f"l'image installe {sorted(installes)} et il manque {sorted(manquants)} : "
        "elle démarrerait, passerait les contrôles de santé, et échouerait sur "
        "le premier plan déposé"
    )


def test_the_lock_pins_what_the_plan_extras_declare() -> None:
    """Le verrou épingle ce que les extras déclarent, et c'était un hasard.

    `make lock` régénère `constraints/api.txt` depuis une résolution propre,
    dans un venv jetable. Cette cible installait `./apps/api[postgres]` — sans
    `plans` ni `pdf`. Le verrou, lui, PORTE `ezdxf`, `pillow` et `pypdfium2` :
    il a donc été produit autrement que par la cible qui prétend le produire.

    La conséquence n'est pas théorique. Une régénération par la cible telle
    qu'elle était écrite aurait RETIRÉ ces épingles ; l'image d'exécution, qui
    installe `[postgres,plans,pdf]` sous contrainte de ce verrou, aurait alors
    résolu `ezdxf` et `pypdfium2` librement — donc installé des versions que
    rien n'a éprouvées, dans le conteneur livré.

    Les noms sont lus dans `pyproject.toml`, pas recopiés : c'est le manifeste
    qui décide de ce que les extras portent.
    """
    import tomllib

    manifeste = tomllib.loads(
        (RACINE / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    )
    extras = manifeste["project"]["optional-dependencies"]
    declarees = {
        re.split(r"[<>=!~\[ ]", exigence, maxsplit=1)[0].strip().lower()
        for extra in MODULES_PAR_EXTRA
        for exigence in extras[extra]
    }

    verrou = (RACINE / "constraints" / "api.txt").read_text(encoding="utf-8")
    epinglees = {
        ligne.split("==", 1)[0].strip().lower()
        for ligne in verrou.splitlines()
        if "==" in ligne and not ligne.lstrip().startswith("#")
    }

    manquantes = declarees - epinglees
    assert not manquantes, (
        f"les extras déclarent {sorted(declarees)} et le verrou n'épingle pas "
        f"{sorted(manquantes)} : l'image résoudrait ces versions librement, "
        "donc installerait du code que rien n'a éprouvé"
    )
