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

import ast
import importlib.util
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from . import witness_database
from .conftest import running_on_postgresql
from .witness_database import (
    count_sentinels,
    exists,
    owned_witness,
    witness_name,
)

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


PG_ADMIN = "postgresql+psycopg://metreo:metreo@localhost:5432/postgres"


class _Connection:
    """Une connexion qui note ce qu'on lui demande, et peut refuser le CREATE."""

    def __init__(self, log: list[str], fail_on_create: bool) -> None:
        self.log = log
        self.fail_on_create = fail_on_create

    def execute(self, statement: object, parameters: object = None) -> None:
        sql = str(statement)
        self.log.append(sql)
        if "CREATE DATABASE" in sql and self.fail_on_create:
            # La vraie erreur de collision, pour que les deux appelants —
            # `owned_database` et le helper témoin — la traitent comme en vrai.
            raise ProgrammingError("CREATE DATABASE", {}, Exception('database "…" already exists'))

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *exception: object) -> None:
        return None


class _Engine:
    def __init__(self, log: list[str], fail_on_create: bool = False) -> None:
        self.log = log
        self.fail_on_create = fail_on_create
        self.disposed = False

    def connect(self) -> _Connection:
        return _Connection(self.log, self.fail_on_create)

    def dispose(self) -> None:
        self.disposed = True


class TestARedirectingUrlIsRefusedBeforeAnything:
    """Un `?dbname=` dans l'URL d'administration déplace TOUT l'aller-retour.

    Reproduit sur un vrai serveur avant correction : `…/postgres?dbname=metreo_victim_a`
    a fait tourner `upgrade head`, `downgrade base` puis `upgrade head` sur
    `metreo_victim_a`, qui est passée de 2 organisations à 0 — pendant que le
    script annonçait « aller-retour valide » et supprimait sa propre base
    jetable, restée vide. `parsed.set(database=name)` conserve la chaîne de
    requête, et libpq lui obéit plutôt qu'au chemin.
    """

    @pytest.mark.parametrize(
        "parameter",
        ["dbname", "database", "host", "hostaddr", "port", "user", "service", "passfile"],
    )
    def test_a_redirecting_parameter_is_refused_before_create_engine(
        self, parameter: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def forbidden(*arguments: object, **keywords: object) -> object:
            raise AssertionError(
                "create_engine a été appelé : la connexion a eu lieu malgré la redirection"
            )

        monkeypatch.setattr(roundtrip, "create_engine", forbidden)
        with (
            pytest.raises(roundtrip.UnsafeAdminUrl, match=parameter),
            roundtrip.owned_database(f"{PG_ADMIN}?{parameter}=victime"),
        ):
            pass  # pragma: no cover - le refus doit venir avant

    def test_the_url_builder_alone_refuses_a_redirected_url(self) -> None:
        """Seconde couche, contrôlée séparément de la première.

        Le refus en amont et la vérification par `create_connect_args()` sont
        deux gardes distincts. Celui-ci est appelé directement, sans passer par
        l'autre : si `ephemeral_url` se contentait de `parsed.set(database=…)`,
        l'URL rendue ouvrirait encore « victime ».
        """
        parsed = make_url(f"{PG_ADMIN}?dbname=victime")
        name = roundtrip.generated_name()
        built = roundtrip.ephemeral_url(parsed, name)
        assert roundtrip.effective_database(built) == name, built

    def test_the_yielded_url_opens_exactly_the_generated_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ce n'est pas le chemin de l'URL qui décide, c'est le dialecte."""
        log: list[str] = []
        monkeypatch.setattr(roundtrip, "create_engine", lambda *a, **k: _Engine(log))
        seen: list[str] = []
        with roundtrip.owned_database(f"{PG_ADMIN}?connect_timeout=5") as url:
            seen.append(url)

        parsed = make_url(seen[0])
        arguments = parsed.get_dialect()().create_connect_args(parsed)[1]
        created = next(line for line in log if "CREATE DATABASE" in line)
        name = created.split('"')[1]
        assert arguments["dbname"] == name, arguments
        assert roundtrip.owns(name)
        # Un paramètre qui ne déplace rien n'a pas de raison d'être perdu.
        assert arguments["connect_timeout"] == "5"


class TestAFailedCreationDestroysNothing:
    """`owns()` ne prouve qu'un format de nom, pas que ce run a créé la base.

    Reproduit sur un vrai serveur avant correction : une base préexistante
    nommée `metreo_roundtrip_deadbeefdeadbeef`, portant trois lignes témoins, a
    fait échouer `CREATE DATABASE` — puis le `finally` l'a terminée et
    supprimée. La preuve de propriété était le CREATE ; s'il échoue, il n'y a
    plus de preuve, donc plus de droit de détruire.
    """

    def test_no_termination_and_no_drop_when_the_creation_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log: list[str] = []
        engine = _Engine(log, fail_on_create=True)
        monkeypatch.setattr(roundtrip, "create_engine", lambda *a, **k: engine)

        with (
            pytest.raises(ProgrammingError, match="already exists"),
            roundtrip.owned_database(PG_ADMIN),
        ):
            pass  # pragma: no cover - le corps ne doit pas être atteint

        assert any("CREATE DATABASE" in line for line in log), log
        assert not [line for line in log if "DROP DATABASE" in line], (
            f"une base que ce run n'a pas créée a été supprimée : {log}"
        )
        assert not [line for line in log if "pg_terminate_backend" in line], (
            f"les connexions d'une base étrangère ont été coupées : {log}"
        )
        assert engine.disposed, "le moteur d'administration doit être libéré quoi qu'il arrive"

    def test_a_successful_creation_still_drops_and_disposes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La correction ne doit pas transformer le nettoyage en fuite."""
        log: list[str] = []
        engine = _Engine(log)
        monkeypatch.setattr(roundtrip, "create_engine", lambda *a, **k: engine)

        with roundtrip.owned_database(PG_ADMIN):
            pass

        assert [line for line in log if "DROP DATABASE" in line], log
        assert [line for line in log if "pg_terminate_backend" in line], log
        assert engine.disposed


class TestTheGateMigratesTheDatabaseItSeeds:
    """`release-gate` semait une base que rien n'avait migrée.

    Trouvé en lançant la porte depuis un clone propre contre une base
    `metreo_gate` fraîchement créée : `relation "organizations" does not exist`.
    Les exécutions précédentes ne passaient que parce que la base traînait le
    schéma d'une commande antérieure — l'aller-retour, lui, travaille dans sa
    propre base et ne migre plus celle de la porte. Même classe de régression
    que celle déjà attrapée côté CI, à l'autre bout de la chaîne.
    """

    def _gate_recipe(self) -> str:
        """Les commandes de la porte, commentaires du Makefile retirés.

        Un commentaire qui parle de `downgrade` n'en exécute pas un ; lire les
        deux ensemble ferait échouer le contrôle sur du texte explicatif.
        """
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = makefile[makefile.index("\nrelease-gate:") :]
        return "\n".join(line for line in recipe.splitlines() if not line.strip().startswith("@#"))

    def test_the_gate_applies_the_migrations_before_seeding(self) -> None:
        recipe = self._gate_recipe()
        migrate = recipe.index("migrate ")
        seed = recipe.index("seed \\")
        assert migrate < seed, "la porte sème avant de migrer"
        head = recipe[migrate:seed]
        assert 'METREO_DATABASE_URL="$(METREO_TEST_DATABASE_URL)"' in head, head

    def test_the_gate_never_downgrades_the_database_it_was_given(self) -> None:
        recipe = self._gate_recipe()
        assert "downgrade" not in recipe, recipe


class TestASingleListOfRedirectingParameters:
    def test_the_scripts_share_one_definition(self) -> None:
        """Deux listes divergeraient : celle du contrôle de nom n'avait pas `database`."""
        defining = [
            path.name
            for path in sorted((ROOT / "scripts").glob("*.py"))
            if "REDIRECTING_PARAMETERS = " in path.read_text(encoding="utf-8")
        ]
        assert defining == ["_url_safety.py"], defining


class TestTheseTestsCannotDestroyWhatTheyDoNotOwn:
    """Le contrôle qui empêche ces preuves de redevenir dangereuses.

    Les preuves « une base préexistante survit » commençaient par supprimer,
    sous un nom fixe, toute base portant ce nom. Sur un serveur partagé, cette
    préparation détruisait précisément la base qu'elle prétendait épargner,
    puis en recréait une du même nom et concluait qu'elle avait survécu —
    preuve circulaire, perte réelle. Reproduit : une base de développeur
    portant une table sans rapport avec le test a disparu, **pendant que le
    test passait au vert**. Deux suites lancées ensemble se supprimaient aussi
    l'une l'autre.

    Ces contrôles sont statiques : ils lisent le source, n'ouvrent aucune base,
    et tournent donc partout — y compris là où le défaut ferait le plus de
    dégâts, sur la machine d'un développeur.

    Leur portée est celle de leur lecture : ils voient le SQL écrit en clair
    dans un appel, pas une requête assemblée ailleurs puis exécutée. C'est la
    forme qu'a prise le défaut, et celle qu'ils retiennent.
    """

    #: La classe qui passe des noms à `owns()`, un prédicat pur. Ces chaînes ne
    #: touchent aucun serveur : ce sont les noms que le script doit REFUSER.
    PURE_PREDICATE = "TestOwnership"

    def _module_source(self) -> str:
        return Path(__file__).read_text(encoding="utf-8")

    def _executed_sql_lines(self, source: str) -> list[str]:
        """Les lignes qui exécutent du SQL écrit en clair."""
        return [
            f"{number}: {line.strip()}"
            for number, line in enumerate(source.splitlines(), start=1)
            if "execute(" in line and "DATABASE" in line
        ]

    def test_no_test_here_drops_a_database(self) -> None:
        """Seul le helper propriétaire supprime, et seulement ce qu'il a créé."""
        offending = [
            line for line in self._executed_sql_lines(self._module_source()) if "DROP" in line
        ]
        assert offending == [], (
            f"ces lignes suppriment une base depuis un test : {offending} — la "
            "destruction appartient à witness_database.owned_witness, qui ne "
            "détruit que ce qu'il a lui-même créé"
        )

    def test_no_fixed_database_name_reaches_a_server(self) -> None:
        """Un nom en dur est partagé par toutes les exécutions du monde.

        Les littéraux de `TestOwnership` sont exclus : ils alimentent `owns()`,
        qui ne se connecte à rien. Partout ailleurs, un nom fixe finit tôt ou
        tard dans un `CREATE` ou un `DROP`.
        """
        pattern = re.compile(r"^metreo_(?:temoin|roundtrip)_[a-z0-9_]+$")
        tree = ast.parse(self._module_source())
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == self.PURE_PREDICATE:
                continue
            if isinstance(node, ast.ClassDef):
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Constant)
                        and isinstance(inner.value, str)
                        and pattern.match(inner.value)
                    ):
                        found.append(f"{node.name}:{inner.lineno}:{inner.value}")
        assert found == [], (
            f"noms de base fixes hors du prédicat pur : {found} — deux "
            "exécutions concurrentes viseraient la même base ; le nom doit être "
            "tiré au hasard par witness_database.witness_name ou par le "
            "générateur du script sous test"
        )

    def test_the_owning_helper_never_prepares_by_dropping(self) -> None:
        helper = (Path(__file__).parent / "witness_database.py").read_text(encoding="utf-8")
        conditional = [
            line for line in self._executed_sql_lines(helper) if "IF EXISTS" in line.upper()
        ]
        assert conditional == [], (
            f"{conditional} — un DROP conditionnel accepte de supprimer une base "
            "dont on ne sait rien ; le helper ne supprime que celle qu'il vient "
            "de créer, et veut échouer bruyamment si elle a disparu"
        )
        assert "created_by_test = False" in helper, "l'état de possession doit partir de faux"


class TestAHelperThatCreatedNothingDropsNothing:
    """`created_by_test` épinglé sans serveur, donc partout.

    La falsification par le serveur réel se manifeste par un crash — le helper
    tente `DROP DATABASE` sur un nom qu'il n'a pas créé. Ce test-ci le dit
    avant le crash : quand toutes les créations échouent, aucune destruction
    n'est émise. C'est le cas dangereux, celui où la base d'en face existe.
    """

    def test_no_drop_and_no_termination_when_every_creation_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log: list[str] = []
        engine = _Engine(log, fail_on_create=True)
        monkeypatch.setattr(witness_database, "create_engine", lambda *a, **k: engine)

        with (
            pytest.raises(RuntimeError, match="aucun nom libre"),
            witness_database.owned_witness(PG_ADMIN),
        ):
            pass  # pragma: no cover - aucune base n'a pu être créée

        assert [line for line in log if "CREATE DATABASE" in line], log
        assert not [line for line in log if "DROP DATABASE" in line], (
            f"une base que ce test n'a pas créée a été supprimée : {log}"
        )
        assert not [line for line in log if "pg_terminate_backend" in line], log
        assert engine.disposed, "le moteur d'administration doit être libéré quoi qu'il arrive"


@pytest.mark.skipif(
    not running_on_postgresql(),
    reason="Ces preuves créent et épargnent de vraies bases ; il en faut un serveur.",
)
class TestAgainstARealServerAfterTheFix:
    """Les reproductions, rejouées contre un vrai serveur — sans rien détruire.

    Chaque base témoin est créée par le test sous un nom tiré au hasard, et
    n'est supprimée que par le helper qui l'a créée.
    """

    def _admin(self) -> str:
        from .conftest import TEST_DATABASE_URL

        return TEST_DATABASE_URL

    def test_the_helper_refuses_to_touch_a_database_it_did_not_create(self) -> None:
        """La preuve directe du P1 : une base étrangère n'est jamais supprimée.

        Un second helper se voit imposer le nom du premier. Il ne doit pas
        « faire de la place » : il tire un autre nom, et les sentinelles du
        premier restent intactes.
        """
        admin_url = self._admin()
        with owned_witness(admin_url) as occupant:
            attempts: list[str] = []

            def collide_then_move_on() -> str:
                # Deux fois le nom de l'occupant, puis un nom libre.
                attempts.append(occupant.name if len(attempts) < 2 else witness_name())
                return attempts[-1]

            with owned_witness(admin_url, name_factory=collide_then_move_on) as latecomer:
                assert latecomer.name != occupant.name, "le second a pris la base du premier"
                assert attempts[:2] == [occupant.name, occupant.name], attempts
                assert exists(admin_url, occupant.name), "la base de l'occupant a disparu"
                assert count_sentinels(occupant.url) == 3, "les sentinelles ont été perdues"
                second = latecomer.name

            assert not exists(admin_url, second), "le second helper a laissé un résidu"
            assert count_sentinels(occupant.url) == 3, "l'occupant a été touché à la sortie"

    def test_a_witness_survives_a_dbname_parameter(self) -> None:
        """`?dbname=` ne doit déclencher ni migration ni destruction."""
        admin_url = self._admin()
        with owned_witness(admin_url) as witness:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "migration_roundtrip.py"),
                    "--admin-url",
                    f"{admin_url}?dbname={witness.name}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert completed.returncode == 1, completed.stdout + completed.stderr
            assert "dbname" in completed.stderr, completed.stderr
            assert "Traceback" not in completed.stderr, completed.stderr
            assert count_sentinels(witness.url) == 3, "la base témoin a été touchée"

    def test_a_pre_existing_database_of_the_same_name_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Collision : le CREATE échoue, et la base d'en face n'est pas détruite.

        Le nom vient du générateur du script lui-même — donc `owns()` l'accepte
        et le nettoyage aurait bien porté sur lui — mais il est tiré au hasard à
        chaque exécution, jamais écrit en dur.
        """
        admin_url = self._admin()
        with owned_witness(admin_url, name_factory=roundtrip.generated_name) as witness:
            assert roundtrip.owns(witness.name), witness.name
            monkeypatch.setattr(roundtrip, "generated_name", lambda: witness.name)

            with (
                pytest.raises(Exception, match="already exists"),
                roundtrip.owned_database(admin_url),
            ):
                pass  # pragma: no cover - la création doit échouer

            assert exists(admin_url, witness.name), "la base préexistante a été supprimée"
            assert count_sentinels(witness.url) == 3

    def test_two_concurrent_runs_leave_each_other_alone(self) -> None:
        """Deux exécutions simultanées : ni suppression croisée, ni résidu.

        Chacune crée sa base témoin, attend l'autre au tourniquet pour que les
        durées de vie se chevauchent réellement, puis ouvre une base éphémère.
        Avec des noms fixes, les deux viseraient la même base.
        """
        admin_url = self._admin()
        barrier = threading.Barrier(2)
        seen: dict[int, tuple[str, str, int]] = {}
        failures: list[BaseException] = []

        def run(index: int) -> None:
            try:
                with owned_witness(admin_url) as witness:
                    barrier.wait(timeout=60)
                    with roundtrip.owned_database(admin_url) as ephemeral:
                        ephemeral_name = make_url(ephemeral).database or ""
                        barrier.wait(timeout=60)
                        seen[index] = (witness.name, ephemeral_name, count_sentinels(witness.url))
                    assert not exists(admin_url, ephemeral_name), "base éphémère résiduelle"
                    assert count_sentinels(witness.url) == 3, "témoin touché par l'autre run"
            except BaseException as error:
                failures.append(error)
                barrier.abort()

        threads = [threading.Thread(target=run, args=(index,)) for index in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)

        assert not failures, failures
        assert set(seen) == {0, 1}, seen
        assert seen[0][0] != seen[1][0], "les deux runs ont visé la même base témoin"
        assert seen[0][1] != seen[1][1], "les deux runs ont visé la même base éphémère"
        for witness_seen, ephemeral_seen, sentinels in seen.values():
            assert sentinels == 3
            assert not exists(admin_url, ephemeral_seen)
            assert not exists(admin_url, witness_seen), "base témoin résiduelle"
