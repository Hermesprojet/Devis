"""Le contrôle d'installation vérifie les versions, pas seulement le démarrage.

Une application peut démarrer sur des versions qui violent ses propres
manifestes : rien n'oblige un import à toucher le code qui a besoin de la
borne. Deux mécanismes couvrent le trou, et ce fichier les met en défaut sur
des cas construits, sans installer quoi que ce soit.

* `walk` parcourt la clôture des exigences depuis les distributions du dépôt,
  extras compris, et signale ce qui manque ou ce qui est trop ancien ;
* `read_pins` lit le verrou, que `check_clean_install.py` compare ensuite aux
  versions relevées dans l'environnement vierge.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    """Charge un script par chemin : `scripts/` n'est pas un paquet importable."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


closure = _load("verify_dependency_closure")
clean_install = _load("check_clean_install")


Graph = dict[str, tuple[str, list[str]]]
Resolver = Callable[[str], tuple[str, list[str]] | None]


def resolver_over(graph: Graph) -> Resolver:
    def resolve(name: str) -> tuple[str, list[str]] | None:
        return graph.get(name)

    return resolve


class TestClosureWalk:
    def test_a_satisfied_graph_reports_nothing(self) -> None:
        graph = {
            "app": ("1.0", ["lib>=2.0"]),
            "lib": ("2.3", []),
        }
        report = closure.walk(("app",), resolver_over(graph))
        assert report["problems"] == []
        assert report["installed"] == {"app": "1.0", "lib": "2.3"}
        assert report["edges"] == 1

    def test_a_version_below_the_declared_bound_is_refused(self) -> None:
        """Le cas que « l'application démarre » ne voit pas."""
        graph = {
            "app": ("1.0", ["lib>=2.0"]),
            "lib": ("1.9", []),
        }
        report = closure.walk(("app",), resolver_over(graph))
        assert report["problems"] == ["lib==1.9 ne satisfait pas « lib>=2.0 » exigé par app"]

    def test_a_version_above_the_declared_ceiling_is_refused(self) -> None:
        graph = {
            "app": ("1.0", ["lib>=0.115,<1.0"]),
            "lib": ("1.0", []),
        }
        report = closure.walk(("app",), resolver_over(graph))
        assert len(report["problems"]) == 1
        assert "lib==1.0" in report["problems"][0]

    def test_a_missing_dependency_is_named_with_its_requester(self) -> None:
        graph: Graph = {"app": ("1.0", ["lib>=2.0"])}
        report = closure.walk(("app",), resolver_over(graph))
        assert report["problems"] == [
            "lib est exigé par app mais n'est pas installé (exigence : « lib>=2.0 »)"
        ]

    def test_an_extra_pulls_its_own_requirements(self) -> None:
        """`pydantic[email]` doit mener à `email-validator`, sinon le défaut revient."""
        graph = {
            "app": ("1.0", ["lib[email]>=2.7"]),
            "lib": ("2.7", ['validator>=2.0; extra == "email"']),
        }
        report = closure.walk(("app",), resolver_over(graph))
        assert report["problems"] == [
            "validator est exigé par lib mais n'est pas installé "
            '(exigence : « validator>=2.0; extra == "email" »)'
        ]

    def test_without_the_extra_the_guarded_requirement_is_not_demanded(self) -> None:
        graph = {
            "app": ("1.0", ["lib>=2.7"]),
            "lib": ("2.7", ['validator>=2.0; extra == "email"']),
        }
        report = closure.walk(("app",), resolver_over(graph))
        assert report["problems"] == []

    def test_a_root_extra_is_honoured(self) -> None:
        graph = {
            "app": ("1.0", ['driver>=3.1; extra == "postgres"']),
            "driver": ("3.0", []),
        }
        assert closure.walk(("app",), resolver_over(graph))["problems"] == []
        report = closure.walk(("app[postgres]",), resolver_over(graph))
        assert report["problems"] == [
            'driver==3.0 ne satisfait pas « driver>=3.1; extra == "postgres" » exigé par app'
        ]

    def test_a_cycle_terminates(self) -> None:
        graph: Graph = {"a": ("1.0", ["b"]), "b": ("1.0", ["a"])}
        assert closure.walk(("a",), resolver_over(graph))["problems"] == []

    def test_names_are_compared_after_normalisation(self) -> None:
        """`Metreo_Domain` et `metreo-domain` désignent la même distribution."""
        graph: Graph = {"A_b.C": ("1.0", []), "a-b-c": ("1.0", [])}
        report = closure.walk(("A_b.C", "a-b-c"), resolver_over(graph))
        assert report["problems"] == []
        assert list(report["installed"]) == ["a-b-c"]


class TestPinReading:
    @pytest.fixture
    def lockfile(self, tmp_path: Path) -> Iterator[Path]:
        path = tmp_path / "lock.txt"
        path.write_text(
            "# un commentaire\n"
            "\n"
            "FastAPI==0.141.1\n"
            "email_validator==2.3.0  # avec commentaire de fin\n"
            "  sqlalchemy==2.0.44  \n"
            "un-paquet-sans-version\n",
            encoding="utf-8",
        )
        yield path

    def test_comments_blank_lines_and_case_are_handled(self, lockfile: Path) -> None:
        assert clean_install.read_pins(lockfile) == {
            "fastapi": "0.141.1",
            "email-validator": "2.3.0",
            "sqlalchemy": "2.0.44",
        }

    def test_normalisation_follows_pep_503(self) -> None:
        assert clean_install.normalise("Email_Validator") == "email-validator"
        assert clean_install.normalise("zope.interface") == "zope-interface"
        assert clean_install.normalise("A--_.B") == "a-b"


class TestTheLockDescribesTheManifests:
    """Le verrou du dépôt, tel qu'il est commité, reste lisible et complet."""

    def test_every_declared_runtime_dependency_is_pinned(self) -> None:
        import tomllib

        pins = clean_install.read_pins(ROOT / "constraints" / "api.txt")
        manifest = tomllib.loads(
            (ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
        )
        declared = list(manifest["project"]["dependencies"])
        declared += manifest["project"]["optional-dependencies"]["postgres"]

        missing = []
        for raw in declared:
            name = clean_install.normalise(raw.split(">")[0].split("<")[0].split("[")[0].strip())
            # Les distributions du dépôt sont installées depuis l'arborescence
            # locale : les épingler ferait fuiter un chemin de machine.
            if name in {clean_install.normalise(n) for n in clean_install.LOCAL}:
                continue
            if name not in pins:
                missing.append(name)
        assert missing == [], f"dépendances déclarées mais non verrouillées : {missing}"

    def test_the_lock_pins_no_local_path(self) -> None:
        text = (ROOT / "constraints" / "api.txt").read_text(encoding="utf-8")
        offenders = [
            line
            for line in text.splitlines()
            if line.strip() and not line.startswith("#") and ("@" in line or "file://" in line)
        ]
        assert offenders == []
