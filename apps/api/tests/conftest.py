"""Test harness.

Each test gets a fresh SQLite database created **through the Alembic
migrations**, not through ``create_all``: a migration that does not reproduce
the models is a bug the suite should catch, not hide.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def database_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}"


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
