"""La version de Next est une décision de sécurité, pas un détail de verrou.

`npm audit` ne suffit pas à la retenir. Reproduit : ramener `next` à 15.5.23 —
la version portant GHSA-2xp9-vwfh-vxw4 et GHSA-p293-qw3h-jr36, deux exécutions
de code à distance non authentifiées — laisse `npm audit --audit-level=high`
répondre « found 0 vulnerabilities » et sortir en 0. Les avis ne sont pas dans
sa base ; un audit vert ne dit rien de ces deux-là, et n'est jamais accepté ici
comme preuve du correctif.

Quatre endroits déclarent la version, et ils peuvent se contredire :

    apps/web/package.json                        ce qu'on demande
    package-lock.json → packages[""]             ce que la racine du verrou retient
    package-lock.json → packages["node_modules/next"]   ce que `npm ci` installe
    node_modules/next/package.json               ce qui tourne réellement

La première rédaction de ce garde n'en lisait que deux. Reproduit : le paquet
installé ramené à 15.5.23, manifeste et verrou laissés à 15.5.24 — cinq
contrôles au vert. Les quatre sont désormais confrontés.

Le contrôle est statique et sans réseau. Le paquet installé n'est lu que s'il
est présent : la suite doit tourner sur un clone où `npm ci` n'a pas encore été
lancé.
"""

from __future__ import annotations

import os

import json
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[3] / "apps" / "web"

#: Première version corrigée. Le nombre seul vieillit mal : la raison voyage
#: avec lui.
FIRST_FIXED = (15, 5, 24)
ADVISORIES = (
    "GHSA-2xp9-vwfh-vxw4 (RCE non authentifiée via l'API d'optimisation "
    "d'images, fichiers AVIF) et GHSA-p293-qw3h-jr36 (RCE non authentifiée "
    "sur hôte Windows), tous deux critiques, corrigés en 15.5.24"
)

#: Borne haute **exclusive**. La politique est déduite de la documentation du
#: dépôt, pas inventée ici : `docs/PHASE1_VERIFICATION.md` écrit « Montée sur la
#: branche de maintenance 15.5 » puis « Aucun passage à Next 16, à une version
#: canary ou à une autre branche ». Une 15.6 serait une autre branche.
#:
#: C'est donc `>=15.5.24 <15.6`. Le jour où la 15.5 cesse de recevoir les
#: correctifs, cette borne devient un obstacle à une montée de sécurité : elle
#: se change ici, en une ligne, et c'est une décision humaine — pas un effet de
#: bord d'une montée de version.
UPPER_BOUND = (15, 6)
POLICY = f">={'.'.join(map(str, FIRST_FIXED))} <{'.'.join(map(str, UPPER_BOUND))}"


def parse(version: str) -> tuple[int, int, int] | None:
    """« 15.5.24 » → (15, 5, 24). ``None`` pour tout ce qui n'est pas trois entiers.

    Une `canary`, une `rc`, une `beta` ou un intervalle ne se comparent pas au
    petit bonheur : ils ne sont pas des versions stables et sont refusés en tant
    que tels, pas rangés par erreur d'un côté ou de l'autre de la borne.
    """
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def policy_violation(version: str, *, source: str = "la version") -> str | None:
    """Pourquoi cette version est refusée, ou ``None`` si elle convient."""
    parsed = parse(version)
    if parsed is None:
        return (
            f"{source} « {version} » n'est pas une version stable à trois "
            f"nombres : préversion, intervalle ou format inconnu. Politique : {POLICY}."
        )
    if parsed < FIRST_FIXED:
        return (
            f"{source} « {version} » est antérieure à "
            f"{'.'.join(map(str, FIRST_FIXED))} : {ADVISORIES}. "
            "npm audit ne rattrape pas cette régression — il rend zéro sur la "
            "version vulnérable."
        )
    if parsed[:2] >= UPPER_BOUND:
        return (
            f"{source} « {version} » quitte la branche de maintenance suivie par "
            f"le dépôt. Politique : {POLICY}. Changer de branche est une tranche "
            "à part, avec analyse des changements incompatibles."
        )
    return None


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _json(WEB / "package.json")


@pytest.fixture(scope="module")
def lockfile() -> dict:
    return _json(WEB / "package-lock.json")


class TestThePolicyItself:
    """La règle est éprouvée sur une table, sans toucher au dépôt.

    Sans cela, « 15.5.23 est refusée » ne se vérifierait qu'en cassant le
    manifeste — donc jamais.
    """

    @pytest.mark.parametrize(
        "version",
        [
            pytest.param("15.5.23", id="la-version-vulnérable"),
            pytest.param("15.5.4", id="antérieure"),
            pytest.param("15.4.99", id="mineure-antérieure"),
            pytest.param("14.2.35", id="majeure-antérieure"),
            pytest.param("15.6.0", id="autre-branche-de-maintenance"),
            pytest.param("16.3.3", id="majeure-suivante"),
            pytest.param("15.5.25-canary.0", id="canary"),
            pytest.param("16.0.0-beta.0", id="beta"),
            pytest.param("15.5.24-rc.1", id="release-candidate"),
            pytest.param("^15.5.24", id="intervalle-caret"),
            pytest.param("~15.5.24", id="intervalle-tilde"),
            pytest.param("latest", id="étiquette"),
            pytest.param("", id="vide"),
        ],
    )
    def test_a_refused_version_is_refused(self, version: str) -> None:
        assert policy_violation(version) is not None, version

    @pytest.mark.parametrize(
        "version",
        [
            pytest.param("15.5.24", id="la-première-version-corrigée"),
            pytest.param("15.5.25", id="correctif-ultérieur"),
            pytest.param("15.5.99", id="correctif-lointain"),
        ],
    )
    def test_a_compliant_version_is_accepted(self, version: str) -> None:
        assert policy_violation(version) is None, policy_violation(version)

    def test_the_refusal_of_an_old_version_names_both_advisories(self) -> None:
        reason = policy_violation("15.5.23") or ""
        assert "GHSA-2xp9-vwfh-vxw4" in reason, reason
        assert "GHSA-p293-qw3h-jr36" in reason, reason
        assert "npm audit" in reason, "le message doit dire pourquoi l'audit ne suffit pas"

    def test_the_refusal_of_another_branch_states_the_policy(self) -> None:
        reason = policy_violation("15.6.0") or ""
        assert POLICY in reason, reason


class TestTheFourDeclarationsAgree:
    """Quatre endroits déclarent la version ; aucun ne peut mentir seul."""

    def test_the_manifest_complies(self, manifest: dict) -> None:
        declared = manifest["dependencies"]["next"]
        assert policy_violation(declared, source="le manifeste déclare") is None, policy_violation(
            declared, source="le manifeste déclare"
        )

    def test_the_lockfile_root_complies_and_matches_the_manifest(
        self, manifest: dict, lockfile: dict
    ) -> None:
        root = lockfile["packages"][""]["dependencies"]["next"]
        assert policy_violation(root, source="la racine du verrou déclare") is None, (
            policy_violation(root, source="la racine du verrou déclare")
        )
        assert root == manifest["dependencies"]["next"], (
            f"la racine du verrou déclare next {root} là où le manifeste déclare "
            f"{manifest['dependencies']['next']} : les deux doivent coïncider."
        )

    def test_the_installed_entry_of_the_lockfile_complies(
        self, manifest: dict, lockfile: dict
    ) -> None:
        """C'est cette entrée que `npm ci` pose sur disque."""
        locked = lockfile["packages"]["node_modules/next"]["version"]
        assert policy_violation(locked, source="le verrou pose") is None, policy_violation(
            locked, source="le verrou pose"
        )
        assert locked == manifest["dependencies"]["next"], (
            f"le verrou pose next {locked} là où le manifeste déclare "
            f"{manifest['dependencies']['next']} : c'est le verrou qui décide."
        )

    def test_the_package_actually_installed_complies(self, manifest: dict) -> None:
        """Ce qui tourne, et non ce qu'on a demandé.

        Ce contrôle a besoin d'une installation JavaScript, que la suite Python
        ne doit pas exiger. Mais un simple `pytest.skip` rendait le décompte de
        la suite **dépendant de l'état local** : 24 réussites ici, 23 plus un
        ignoré sur un clone neuf, sans qu'aucune des deux valeurs soit fausse.
        Un compteur qui change selon la machine ne peut pas servir de preuve.

        Deux états sont donc distingués, et le troisième est une erreur :

        * aucun `node_modules` du tout — rien n'a été installé, l'ignoré est
          l'état explicite et sa raison le dit ;
        * `node_modules` présent mais `next` absent — une installation
          partielle ou cassée : **rouge**, plus jamais un ignoré silencieux ;
        * `METREO_REQUIRE_WEB_INSTALL` posé — l'appelant affirme avoir installé,
          donc l'ignoré lui-même devient **rouge**. `make release-gate` le pose :
          il lance Playwright, il ne peut pas tourner sans installation.
        """
        exige = os.environ.get("METREO_REQUIRE_WEB_INSTALL") == "1"
        node_modules = WEB / "node_modules"
        installed = node_modules / "next" / "package.json"

        if not node_modules.exists():
            if exige:
                pytest.fail(
                    "METREO_REQUIRE_WEB_INSTALL=1 mais apps/web/node_modules "
                    "n'existe pas : lancez `make install` avant cette porte."
                )
            pytest.skip(
                "aucune installation JavaScript ici (apps/web/node_modules absent) ; "
                "posez METREO_REQUIRE_WEB_INSTALL=1 pour en faire une erreur"
            )

        assert installed.exists(), (
            "apps/web/node_modules existe mais next n'y est pas : installation "
            "partielle ou interrompue. Ce n'est pas un ignoré, c'est une erreur."
        )
        version = _json(installed)["version"]
        assert policy_violation(version, source="le paquet installé est en") is None, (
            policy_violation(version, source="le paquet installé est en")
        )
        assert version == manifest["dependencies"]["next"], (
            f"le paquet installé est en next {version} là où le manifeste déclare "
            f"{manifest['dependencies']['next']} : c'est l'installé qui tourne."
        )

    def test_the_manifest_pins_an_exact_version(self, manifest: dict) -> None:
        """Un intervalle laisserait `npm install` choisir, y compris à la baisse."""
        declared = manifest["dependencies"]["next"]
        assert parse(declared) is not None, (
            f"next « {declared} » : version exacte attendue, sans ^ ni ~ — sinon "
            "la version installée dépend du jour."
        )

    def test_every_next_package_moves_together(self, lockfile: dict) -> None:
        """Les binaires `@next/*` suivent le paquet principal, ou rien ne suit."""
        expected = lockfile["packages"]["node_modules/next"]["version"]
        stragglers = {
            name.removeprefix("node_modules/"): entry["version"]
            for name, entry in lockfile["packages"].items()
            if name.startswith("node_modules/@next/") and entry.get("version") != expected
        }
        assert stragglers == {}, f"paquets @next/* désalignés de next {expected} : {stragglers}"
