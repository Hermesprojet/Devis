"""La version de Next est une décision de sécurité, pas un détail de verrou.

`npm audit` ne suffit pas à la retenir. Reproduit : ramener `next` à 15.5.23 —
la version portant GHSA-2xp9-vwfh-vxw4 et GHSA-p293-qw3h-jr36, deux exécutions
de code à distance non authentifiées — laisse `npm audit --audit-level=high`
répondre « found 0 vulnerabilities » et sortir en 0. Les avis ne sont pas dans
sa base ; un audit vert ne dit rien de ces deux-là.

Rien d'autre dans le dépôt ne mentionnait la version. Une régression aurait
donc traversé les dix jobs au vert. Le cas n'est pas théorique : une PR
empilée, partie d'une base antérieure à la montée de version, porte encore
15.5.23 dans son arbre ; une résolution de conflit prise du mauvais côté
suffirait.

Ce contrôle est statique et sans réseau : il lit le manifeste et le verrou.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[3] / "apps" / "web"

#: Version minimale acceptable, et pourquoi. Le nombre seul vieillit mal : la
#: raison doit voyager avec lui.
MINIMUM = (15, 5, 24)
MINIMUM_REASON = (
    "GHSA-2xp9-vwfh-vxw4 (RCE non authentifiée via l'API d'optimisation "
    "d'images, fichiers AVIF) et GHSA-p293-qw3h-jr36 (RCE non authentifiée "
    "sur hôte Windows), tous deux critiques, corrigés en 15.5.24"
)

#: La branche de maintenance suivie. Passer à Next 16 est une migration
#: majeure, décidée à part — pas un effet de bord d'une montée de sécurité.
MAINTENANCE_LINE = (15, 5)


def _parse(version: str) -> tuple[int, ...]:
    """« 15.5.24 » → (15, 5, 24). Refuse tout ce qui n'est pas trois entiers.

    Une `canary`, une `rc` ou une `beta` échouent ici plutôt que de se
    comparer au petit bonheur : le dépôt les interdit, et une comparaison
    silencieusement fausse serait pire qu'un refus.
    """
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise AssertionError(
            f"version « {version} » : trois entiers attendus. Une version canary, "
            "rc ou beta n'est pas acceptée sur cette ligne de maintenance."
        )
    return tuple(int(part) for part in parts)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((WEB / "package.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lockfile() -> dict:
    return json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))


class TestNextCarriesItsSecurityFix:
    def test_the_manifest_pins_at_least_the_patched_version(self, manifest: dict) -> None:
        declared = manifest["dependencies"]["next"]
        assert _parse(declared) >= MINIMUM, (
            f"next {declared} est antérieure à "
            f"{'.'.join(map(str, MINIMUM))} : {MINIMUM_REASON}. "
            "npm audit ne rattrape pas cette régression — il rend zéro sur la "
            "version vulnérable."
        )

    def test_the_manifest_stays_on_the_maintenance_line(self, manifest: dict) -> None:
        declared = _parse(manifest["dependencies"]["next"])
        assert declared[:2] == MAINTENANCE_LINE, (
            f"next {'.'.join(map(str, declared))} quitte la ligne "
            f"{'.'.join(map(str, MAINTENANCE_LINE))} : une migration majeure se "
            "décide à part, avec analyse des changements incompatibles."
        )

    def test_the_manifest_pins_an_exact_version(self, manifest: dict) -> None:
        """Un intervalle laisserait `npm install` choisir, y compris à la baisse."""
        declared = manifest["dependencies"]["next"]
        assert declared[0].isdigit(), (
            f"next « {declared} » : version exacte attendue, sans ^ ni ~ — "
            "sinon la version installée dépend du jour."
        )

    def test_the_lockfile_agrees_with_the_manifest(self, manifest: dict, lockfile: dict) -> None:
        """Le verrou est ce que `npm ci` installe ; c'est lui qui fait foi."""
        locked = lockfile["packages"]["node_modules/next"]["version"]
        assert locked == manifest["dependencies"]["next"], (
            f"le verrou pose next {locked} là où le manifeste déclare "
            f"{manifest['dependencies']['next']} : c'est le verrou qui décide."
        )
        assert _parse(locked) >= MINIMUM, f"verrou : next {locked} — {MINIMUM_REASON}"

    def test_every_next_package_moves_together(self, lockfile: dict) -> None:
        """Les binaires `@next/*` suivent le paquet principal, ou rien ne suit."""
        expected = lockfile["packages"]["node_modules/next"]["version"]
        stragglers = {
            name.removeprefix("node_modules/"): entry["version"]
            for name, entry in lockfile["packages"].items()
            if name.startswith("node_modules/@next/") and entry.get("version") != expected
        }
        assert stragglers == {}, f"paquets @next/* désalignés de next {expected} : {stragglers}"
