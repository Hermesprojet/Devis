"""L'aller-retour des migrations ne détruit que ce qu'il a créé.

`make migrations` acceptait une base fournie par l'appelant et y lançait
`alembic downgrade base`, qui supprime toutes les tables applicatives. Trois
défauts distincts sont sortis de cette seule forme : le garde-fou lisait le
mauvais composant de l'URL, une seconde variable écrasait celle qui venait
d'être validée, et le contrôle par le nom acceptait une base de production.
Chacun a été corrigé ; chaque correction rapiéçait une commande qui n'aurait
pas dû exister.

La cible publique est retirée. Ce qui reste applique une règle plus forte
qu'un garde-fou : **le processus ne peut détruire qu'une ressource qu'il a
lui-même créée et dont il possède l'identité.** Un nom rassurant n'est pas une
preuve — une base qui compte peut parfaitement s'appeler `metreo_gate`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import running_on_postgresql

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


roundtrip = _load("migration_roundtrip")


class TestOwnership:
    """Seules les bases que ce script sait engendrer sont destructibles."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("metreo", id="base-de-travail"),
            pytest.param("metreo_gate", id="nom-rassurant"),
            pytest.param("metreo_production", id="production"),
            pytest.param("metreo_test", id="nom-de-test"),
            pytest.param("postgres", id="base-système"),
            pytest.param("metreo_roundtrip_", id="préfixe-seul"),
            pytest.param("metreo_roundtrip_zzz", id="suffixe-non-hexadécimal"),
            pytest.param("metreo_roundtrip_0123456789abcde", id="trop-court"),
            pytest.param("metreo_roundtrip_0123456789abcdef0", id="trop-long"),
            pytest.param("prefixe_metreo_roundtrip_0123456789abcdef", id="préfixé"),
            pytest.param("", id="vide"),
        ],
    )
    def test_a_database_this_run_did_not_create_is_refused(self, name: str) -> None:
        assert roundtrip.owns(name) is False, name

    def test_a_generated_name_is_owned(self) -> None:
        for _ in range(20):
            assert roundtrip.owns(roundtrip.generated_name()) is True

    def test_two_runs_never_generate_the_same_name(self) -> None:
        """Le nom est tiré au hasard : deux exécutions ne se marchent pas dessus."""
        names = {roundtrip.generated_name() for _ in range(200)}
        assert len(names) == 200


class TestTheDestructiveTargetIsGone:
    def test_the_makefile_exposes_no_generic_destructive_target(self) -> None:
        """`make migrations` ne doit plus exister sous sa forme destructive."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert "\nmigrations:" not in makefile, "la cible destructive publique est revenue"
        assert "\nmigrate:" in makefile, "la commande normale doit exister"

    def test_the_normal_target_never_downgrades(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = makefile[
            makefile.index("\nmigrate:") : makefile.index("\n.PHONY: migration-round")
        ]
        assert "downgrade" not in recipe, recipe

    def test_release_gate_does_not_hand_a_pre_existing_database_to_a_downgrade(self) -> None:
        """La porte ne transmet plus qu'une URL d'ADMINISTRATION à l'aller-retour."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        gate = makefile[makefile.index("\nrelease-gate:") :]
        assert "migration-roundtrip-test" in gate
        assert "MIGRATION_DATABASE_URL" not in gate, (
            "release-gate ne doit plus nommer de base à détruire"
        )


@pytest.mark.skipif(
    not running_on_postgresql(),
    reason="L'aller-retour exige un vrai PostgreSQL ; SQLite ne prouve rien sur le DDL.",
)
class TestAgainstARealServer:
    def test_the_created_database_is_dropped_even_when_the_body_fails(self) -> None:
        """Le nettoyage est garanti : `finally`, pas « en cas de succès »."""
        from sqlalchemy import create_engine, text

        from .conftest import TEST_DATABASE_URL

        seen: list[str] = []
        with (
            pytest.raises(RuntimeError, match="échec simulé"),
            roundtrip.owned_database(TEST_DATABASE_URL) as url,
        ):
            seen.append(url.rsplit("/", 1)[-1])
            raise RuntimeError("échec simulé au milieu de l'aller-retour")

        assert seen, "la base n'a pas été créée"
        engine = create_engine(TEST_DATABASE_URL, future=True)
        try:
            with engine.connect() as connection:
                remaining = connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": seen[0]},
                ).scalar_one()
        finally:
            engine.dispose()
        assert remaining == 0, f"la base {seen[0]} survit à l'échec"

    def test_sqlite_is_refused_as_an_admin_target(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "migration_roundtrip.py"),
                "--admin-url",
                "sqlite+pysqlite:///tmp/x.sqlite3",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 1
        assert "PostgreSQL" in completed.stderr, completed.stderr


class TestTheCiStillMigratesItsOwnDatabase:
    """L'aller-retour ne migre plus la base du job — quelqu'un d'autre doit le faire.

    Régression réellement survenue : déplacer l'aller-retour dans sa propre
    base a laissé la base du job PostgreSQL sans schéma, et l'étape de seed est
    tombée sur « relation "organizations" does not exist ». La CI l'a
    attrapée ; ce test évite qu'elle revienne.
    """

    def _postgres_steps(self) -> list[dict[str, object]]:
        import yaml

        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        return list(workflow["jobs"]["api-postgres"]["steps"])

    def test_the_postgres_job_applies_the_migrations_before_seeding(self) -> None:
        steps = self._postgres_steps()
        names = [str(step.get("name", "")) for step in steps]

        def index_of(fragment: str) -> int:
            for position, name in enumerate(names):
                if fragment.lower() in name.lower():
                    return position
            raise AssertionError(f"étape introuvable : {fragment} parmi {names}")

        migrate = index_of("appliquer les migrations")
        seed = index_of("jeu de démonstration")
        suite = index_of("suite complète")
        assert migrate < seed < suite, names

        recipe = str(steps[migrate].get("run", ""))
        assert "upgrade head" in recipe, recipe
        assert "downgrade" not in recipe, "la base du job ne doit jamais être détruite"

    def test_the_roundtrip_step_creates_its_own_database(self) -> None:
        recipes = " ".join(str(step.get("run", "")) for step in self._postgres_steps())
        assert "migration_roundtrip.py" in recipes
        assert "alembic -c alembic.ini downgrade" not in recipes, (
            "aucun downgrade ne doit viser une base préexistante"
        )
