"""Vérifie que les versions réellement installées honorent les manifestes.

Ce script est exécuté par l'interpréteur de l'environnement vierge créé par
`check_clean_install.py`, jamais par celui du dépôt : il ne parle que de ce
qui est effectivement présent dans cet environnement.

Démarrer ne prouve rien sur les versions. Une résolution peut poser une
version antérieure à la borne déclarée sans que l'import échoue, et un extra
peut manquer sans se voir tant qu'aucun code ne l'atteint à l'import. Le
contrôle part donc des deux distributions du dépôt et parcourt leur clôture :
pour chaque exigence retenue par ses marqueurs, la distribution doit être
installée et sa version doit satisfaire la spécification écrite dans le
manifeste.

Le parcours suit les extras — `pydantic[email]` mène à `email-validator` —
parce que c'est précisément là que le défaut d'origine se logeait.

`walk` ne touche à rien : elle interroge un `resolve` qui rend, pour un nom de
distribution, sa version et ses exigences brutes. Le parcours est donc
testable sur un graphe synthétique, sans installer quoi que ce soit.

Sortie : une ligne JSON sur la sortie standard.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

# Les racines du parcours, extras compris : l'environnement vérifié est celui
# que `make lock` résout, donc `metreo-api` y est installé avec son extra
# `postgres`.
ROOTS = ("metreo-domain", "metreo-api[postgres]")

# Rend `(version, exigences brutes)`, ou None si la distribution est absente.
Resolver = Callable[[str], tuple[str, list[str]] | None]


def installed_resolver(name: str) -> tuple[str, list[str]] | None:
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        return None
    return dist.version, list(dist.requires or [])


def walk(roots: tuple[str, ...], resolve: Resolver) -> dict[str, Any]:
    """Parcourt la clôture des exigences et rend les manquements constatés."""
    problems: list[str] = []
    versions: dict[str, str] = {}
    edges = 0

    seen: set[tuple[str, frozenset[str]]] = set()
    queue: list[tuple[str, frozenset[str]]] = [
        (Requirement(root).name, frozenset(Requirement(root).extras)) for root in roots
    ]

    while queue:
        name, extras = queue.pop()
        key = (canonicalize_name(name), extras)
        if key in seen:
            continue
        seen.add(key)

        found = resolve(name)
        if found is None:
            problems.append(f"{name} est déclaré mais absent de l'environnement")
            continue
        version, requires = found
        versions[canonicalize_name(name)] = version

        # Une distribution atteinte sans extra ne doit pas tirer les exigences
        # gardées par un extra : on évalue alors le marqueur avec extra vide.
        wanted = set(extras) or {""}

        for raw in requires:
            requirement = Requirement(raw)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in wanted
            ):
                continue

            target = resolve(requirement.name)
            if target is None:
                problems.append(
                    f"{requirement.name} est exigé par {name} mais n'est pas installé "
                    f"(exigence : « {raw} »)"
                )
                continue

            if requirement.specifier and not requirement.specifier.contains(
                target[0], prereleases=True
            ):
                problems.append(
                    f"{requirement.name}=={target[0]} ne satisfait pas « {raw} » exigé par {name}"
                )

            edges += 1
            queue.append((requirement.name, frozenset(requirement.extras)))

    return {"installed": versions, "problems": problems, "edges": edges}


def main() -> int:
    report = walk(ROOTS, installed_resolver)
    print(json.dumps(report, sort_keys=True))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
