"""« PostgreSQL réel » doit se prouver, pas se déclarer.

Le garde valait `bool(METREO_TEST_DATABASE_URL)`. Reproduit : pointer la
variable sur `sqlite+pysqlite:///…/metreo_test.sqlite3` — dont le nom de
fichier porte le jeton « test » — faisait accepter l'URL par le contrôle de
base jetable, *sélectionner* les tests PostgreSQL-only, puis tomber en quinze
erreurs. Pas un faux vert, mais un diagnostic tardif sous une étiquette qui
affirmait un moteur que rien ne contrôlait.

Trois étages désormais, chacun falsifiable seul : la forme et le dialecte
déclaré, la connexion réelle et ce que le serveur répond de lui-même, puis la
politique de ressource jetable. Aucun message ne recopie les identifiants.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import running_on_postgresql

ROOT = Path(__file__).resolve().parents[3]

#: Un port fermé : la connexion échoue vite et sans dépendre du réseau.
CLOSED_PORT = 9

#: Des identifiants factices, pour vérifier qu'aucun refus ne les recopie.
USER, PASSWORD = "alice", "TopSecret123"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


safety = _load("_url_safety")


class _Connection:
    """Une connexion qui prétend être ce qu'on lui dit d'être."""

    def __init__(self, dialect: str, banner: str) -> None:
        self.dialect = type("Dialect", (), {"name": dialect})()
        self._banner = banner

    def exec_driver_sql(self, statement: str) -> object:
        return type("Result", (), {"scalar_one": lambda _self: self._banner})()

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *exception: object) -> None:
        return None


class _Engine:
    def __init__(self, dialect: str, banner: str) -> None:
        self._dialect, self._banner = dialect, banner
        self.disposed = False

    def connect(self) -> _Connection:
        return _Connection(self._dialect, self._banner)

    def dispose(self) -> None:
        self.disposed = True


class TestTheStaticGate:
    """Forme et dialecte déclaré — sans ouvrir la moindre connexion."""

    @pytest.mark.parametrize(
        ("url", "fragment"),
        [
            pytest.param("", "aucune URL", id="url-absente"),
            pytest.param("   ", "aucune URL", id="url-blanche"),
            pytest.param("sqlite+pysqlite:///./var/metreo_test.sqlite3", "sqlite", id="sqlite"),
            pytest.param("mysql+pymysql://h/metreo_test", "mysql", id="mysql"),
            pytest.param("pas une url du tout", "illisible", id="url-illisible"),
            pytest.param(
                "postgresql+inconnu://h:5432/metreo_test", "dialecte", id="pilote-inconnu"
            ),
            pytest.param("postgresql+psycopg://h:5432/", "aucune base", id="sans-nom-de-base"),
        ],
    )
    def test_a_refused_url_is_refused(self, url: str, fragment: str) -> None:
        problem = safety.postgresql_refusal(url)
        assert problem is not None, url
        assert fragment.lower() in problem.lower(), problem

    def test_a_syntactically_valid_postgresql_url_passes_the_static_gate(self) -> None:
        assert safety.postgresql_refusal("postgresql+psycopg://h:5432/metreo_test") is None


class TestNoCredentialEverReachesAMessage:
    """Un refus recopié dans un journal de CI y recopierait le mot de passe."""

    URL = f"postgresql+psycopg://{USER}:{PASSWORD}@localhost:{CLOSED_PORT}/metreo_test"

    def test_the_redaction_drops_user_and_password(self) -> None:
        rendered = safety.redacted(self.URL)
        assert PASSWORD not in rendered, rendered
        assert USER not in rendered, rendered
        assert "localhost" in rendered and "metreo_test" in rendered, rendered

    def test_an_unreachable_server_is_refused_without_leaking(self) -> None:
        with pytest.raises(safety.NotPostgreSQL) as raised:
            safety.verified_postgresql_dialect(self.URL, timeout=2)
        message = str(raised.value)
        assert "injoignable" in message, message
        assert PASSWORD not in message, message
        assert USER not in message, message

    def test_an_unreadable_url_reveals_nothing(self) -> None:
        assert safety.redacted("pas une url") == "<URL illisible>"


class TestTheServerMustSayItIsPostgreSQL:
    """Le second étage : ce que le serveur répond, pas ce que l'URL déclare."""

    URL = "postgresql+psycopg://h:5432/metreo_test"

    def test_a_server_announcing_something_else_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _Engine("mysql", "MySQL 8.0.36")
        monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: engine, raising=True)
        with pytest.raises(safety.NotPostgreSQL, match="ce n'est pas PostgreSQL"):
            safety.verified_postgresql_dialect(self.URL)
        assert engine.disposed, "le moteur doit être libéré même sur refus"

    def test_a_postgresql_dialect_with_a_foreign_banner_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le dialecte seul ne suffit pas : la bannière doit concorder."""
        engine = _Engine("postgresql", "CockroachDB CCL v23.1")
        monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: engine, raising=True)
        with pytest.raises(safety.NotPostgreSQL, match="ce n'est pas PostgreSQL"):
            safety.verified_postgresql_dialect(self.URL)

    def test_a_genuine_postgresql_answer_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = _Engine("postgresql", "PostgreSQL 16.13 (Ubuntu)")
        monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: engine, raising=True)
        assert safety.verified_postgresql_dialect(self.URL).startswith("PostgreSQL")
        assert engine.disposed


class TestTheDisposablePolicyStillApplies:
    """Un serveur PostgreSQL ne suffit pas : la base doit être jetable."""

    def test_a_production_looking_database_is_refused(self) -> None:
        guard = _load("check_disposable_database")
        problem = guard.refusal(f"postgresql+psycopg://{USER}:{PASSWORD}@h:5432/metreo_production")
        assert problem is not None and "production" in problem, problem
        assert PASSWORD not in problem, problem

    def test_the_system_database_is_refused(self) -> None:
        guard = _load("check_disposable_database")
        problem = guard.refusal("postgresql+psycopg://h:5432/postgres")
        assert problem is not None and "jetable" in problem, problem

    def test_a_disposable_database_is_accepted(self) -> None:
        guard = _load("check_disposable_database")
        assert guard.refusal("postgresql+psycopg://h:5432/metreo_gate") is None


class TestTheSuiteGateDependsOnTheVerifiedDialect:
    """`running_on_postgresql()` ne doit plus valoir `bool(URL)`."""

    def test_the_gate_reads_the_verified_server_and_not_the_variable(self) -> None:
        source = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
        body = source[source.index("def running_on_postgresql") :]
        body = body[: body.index("\n\n\n")] if "\n\n\n" in body else body
        assert "bool(TEST_DATABASE_URL)" not in body, (
            "le garde est revenu à « une variable non vide » : une URL SQLite "
            "ferait de nouveau sélectionner les tests PostgreSQL-only"
        )
        assert "_verified_server()" in body, body

    def test_the_verification_actually_connects(self) -> None:
        """Un contrôle purement statique laisserait passer un serveur absent."""
        source = (ROOT / "scripts" / "_url_safety.py").read_text(encoding="utf-8")
        body = source[source.index("def verified_postgresql_dialect") :]
        assert "engine.connect()" in body, "le second étage doit ouvrir une connexion"
        assert "SELECT version()" in body, "et demander au serveur ce qu'il est"


@pytest.mark.skipif(
    not running_on_postgresql(),
    reason="Le dernier étage exige le serveur que la CI fournit.",
)
class TestAgainstTheRealServer:
    def test_the_configured_server_is_a_verified_disposable_postgresql(self) -> None:
        from .conftest import TEST_DATABASE_URL

        banner = safety.verified_postgresql_dialect(TEST_DATABASE_URL)
        assert banner.startswith("PostgreSQL"), banner
        guard = _load("check_disposable_database")
        assert guard.refusal(TEST_DATABASE_URL) is None
