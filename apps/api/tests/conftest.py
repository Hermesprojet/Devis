"""Test harness.

Each test gets a fresh database created **through the Alembic migrations**,
not through ``create_all``: a migration that does not reproduce the models is
a bug the suite should catch, not hide.

The suite runs on SQLite by default so that ``pytest`` needs no service. Set
``METREO_TEST_DATABASE_URL`` to a PostgreSQL URL and the whole suite runs there
instead, each test in its own schema. That switch is what makes the PostgreSQL
job in CI a real check: SQLite proves nothing about ``NUMERIC`` precision,
server-side constraints or transactional DDL.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]

#: PostgreSQL URL the suite should run against, when asked.
TEST_DATABASE_URL = os.environ.get("METREO_TEST_DATABASE_URL", "").strip()

#: Incremented per test so two schemas never collide inside one run.
_schema_counter = 0


def _next_schema_name() -> str:
    global _schema_counter
    _schema_counter += 1
    return f"metreo_test_{os.getpid()}_{_schema_counter}"


@pytest.fixture()
def database_url(tmp_path: Path) -> Iterator[str]:
    """A URL pointing at a database this test alone owns."""
    if not TEST_DATABASE_URL:
        yield f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}"
        return

    from sqlalchemy import create_engine, text

    # Le schéma est créé par ce test et détruit par lui : même principe que
    # `scripts/migration_roundtrip.py` — on ne supprime que ce qu'on a créé, et
    # le nom vient d'ici, pas d'un appelant.
    schema = _next_schema_name()
    admin = create_engine(TEST_DATABASE_URL, future=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    separator = "&" if "?" in TEST_DATABASE_URL else "?"
    try:
        # ``options`` reaches libpq, which applies it as the session
        # search_path: migrations and queries then land in this test's own
        # schema. The value is left unencoded on purpose — Alembic stores the
        # URL in a ConfigParser, which would read a percent sign as an
        # interpolation.
        yield f"{TEST_DATABASE_URL}{separator}options=-csearch_path={schema}"
    finally:
        # `finally` plutôt qu'une simple suite : une erreur de collecte ou une
        # interruption laisserait sinon un schéma orphelin par test.
        admin = create_engine(TEST_DATABASE_URL, future=True)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


#: Les scripts du dépôt portent la seule définition de « PostgreSQL prouvé » et
#: de « ressource jetable ». Les redire ici les ferait diverger.
_SCRIPTS = API_ROOT.parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@lru_cache(maxsize=1)
def _verified_server() -> str | None:
    """La bannière du serveur PostgreSQL vérifié, ou ``None`` sans URL.

    `bool(METREO_TEST_DATABASE_URL)` ne prouvait qu'une variable non vide.
    Reproduit : pointer la variable sur `sqlite+pysqlite:///…/metreo_test.sqlite3`
    faisait *sélectionner* les tests PostgreSQL-only, qui tombaient ensuite en
    quinze erreurs — un diagnostic tardif sous une étiquette qui affirmait un
    moteur que rien ne contrôlait.

    Trois étages, dans cet ordre :

    1. pas d'URL → ``None``. C'est le mode SQLite, légitime et silencieux ;
    2. une URL → elle doit être PostgreSQL, joignable, et le serveur doit le
       confirmer lui-même. Sinon on **lève** : une URL fournie exprime une
       intention, et l'ignorer en sautant les tests serait un faux vert ;
    3. la base visée doit être jetable au sens du dépôt — la suite y crée et
       y détruit un schéma par test.
    """
    from _url_safety import NotPostgreSQL, redacted, verified_postgresql_dialect
    from check_disposable_database import refusal as disposable_refusal

    if not TEST_DATABASE_URL:
        return None

    try:
        banner = verified_postgresql_dialect(TEST_DATABASE_URL)
    except NotPostgreSQL as error:
        raise RuntimeError(
            f"METREO_TEST_DATABASE_URL ne mène pas à PostgreSQL : {error}. "
            "Retirez la variable pour tourner sur SQLite, ou corrigez-la — "
            "les tests ne seront pas ignorés en silence."
        ) from None

    not_disposable = disposable_refusal(TEST_DATABASE_URL)
    if not_disposable is not None:
        raise RuntimeError(
            f"METREO_TEST_DATABASE_URL vise une base que la suite ne doit pas "
            f"toucher : {not_disposable}. La suite crée et détruit un schéma par "
            f"test — {redacted(TEST_DATABASE_URL)}"
        )
    return banner


def running_on_postgresql() -> bool:
    """Vrai seulement contre un serveur PostgreSQL **vérifié**, jetable."""
    return _verified_server() is not None


@pytest.fixture()
def app_env(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("METREO_DATABASE_URL", database_url)
    monkeypatch.setenv("METREO_ENVIRONMENT", "test")
    monkeypatch.setenv("METREO_AUTH_MODE", "dev")
    monkeypatch.setenv("METREO_JWT_SECRET", "test-secret-not-used-in-production-0123456789")

    from metreo_api import config, db

    config.get_settings.cache_clear()
    db.reset_engine()
    yield
    db.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture()
def migrated(app_env: None, database_url: str) -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    previous = os.environ.get("METREO_DATABASE_URL")
    os.environ["METREO_DATABASE_URL"] = database_url
    try:
        command.upgrade(cfg, "head")
        yield
    finally:
        if previous is not None:
            os.environ["METREO_DATABASE_URL"] = previous


@pytest.fixture()
def seeded(migrated: None) -> dict[str, str]:
    from metreo_api.db import get_session_factory
    from metreo_api.seed import seed

    session = get_session_factory()()
    try:
        return seed(session)
    finally:
        session.close()


@pytest.fixture()
def client(migrated: None) -> Iterator[TestClient]:
    from metreo_api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def seeded_client(seeded: dict[str, str], client: TestClient) -> TestClient:
    return client


def login(client: TestClient, email: str, organization_id: str | None = None) -> dict[str, str]:
    """Return the Authorization header for a seeded user."""
    payload: dict[str, str] = {"email": email}
    if organization_id:
        payload["organization_id"] = organization_id
    response = client.post("/api/v1/auth/dev-login", json=payload)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
