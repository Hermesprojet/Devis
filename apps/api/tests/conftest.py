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
from collections.abc import Iterator
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


def running_on_postgresql() -> bool:
    """For the handful of assertions that only make sense on a real server."""
    return bool(TEST_DATABASE_URL)


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
