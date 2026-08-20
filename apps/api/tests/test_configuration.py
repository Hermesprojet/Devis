"""Configuration and production guard rails.

Two distinct risks are covered here. The first is that the application cannot
be configured the way its own ``.env.example`` says to — a failure nobody sees
until a first deployment, because the tests never set the variable. The second
is that a development shortcut reaches an environment where it is dangerous.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from metreo_api import config

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    config.get_settings.cache_clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **environment: str) -> config.Settings:
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    return config.Settings()


# -- the documented configuration has to work ------------------------------


def test_every_variable_of_env_example_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The example file is the installation instructions; it must load.

    ``METREO_CORS_ORIGINS=http://localhost:3000`` used to raise
    ``SettingsError`` at import time: pydantic-settings JSON-decodes a ``list``
    field inside the environment source, before any ``mode="before"``
    validator could split the comma-separated form.
    """
    assert ENV_EXAMPLE.is_file(), f"{ENV_EXAMPLE} manquant"
    applied = 0
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(METREO_[A-Z0-9_]+)=(.*)$", line.strip())
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if not value:
            # A deliberately empty secret; setting it proves nothing.
            continue
        monkeypatch.setenv(name, value)
        applied += 1

    assert applied >= 5, f"trop peu de variables lues dans .env.example ({applied})"
    config.get_settings.cache_clear()
    settings = config.Settings()
    assert settings.cors_origins


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("http://a.test,http://b.test", ["http://a.test", "http://b.test"]),
        ("http://a.test, http://b.test ", ["http://a.test", "http://b.test"]),
        ('["http://a.test","http://b.test"]', ["http://a.test", "http://b.test"]),
    ],
)
def test_cors_origins_accepts_both_documented_forms(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    settings = _settings(monkeypatch, METREO_CORS_ORIGINS=raw)
    assert settings.cors_origins == expected


def test_a_malformed_json_list_of_origins_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silently keeping one nonsensical origin would break every browser call."""
    with pytest.raises(Exception, match="JSON"):
        _settings(monkeypatch, METREO_CORS_ORIGINS='["http://a.test",')


def test_cors_is_not_open_to_every_origin_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, METREO_ENVIRONMENT="production")
    assert "*" not in settings.cors_origins


# -- development shortcuts must not reach production -----------------------


def test_dev_authentication_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        METREO_ENVIRONMENT="production",
        METREO_AUTH_MODE="dev",
        METREO_JWT_SECRET="a-secret-long-enough-0123456789",
        METREO_DATABASE_URL="postgresql+psycopg://user@host/db",
    )
    problems = settings.validate_startup()
    assert any("auth_mode=dev" in problem for problem in problems), problems


def test_a_missing_secret_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        METREO_ENVIRONMENT="production",
        METREO_AUTH_MODE="jwt",
        METREO_JWT_SECRET="",
        METREO_DATABASE_URL="postgresql+psycopg://user@host/db",
    )
    assert any("jwt_secret" in problem for problem in settings.validate_startup())


def test_sqlite_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        METREO_ENVIRONMENT="production",
        METREO_AUTH_MODE="jwt",
        METREO_JWT_SECRET="a-secret-long-enough-0123456789",
        METREO_DATABASE_URL="sqlite+pysqlite:///./var/metreo.sqlite3",
    )
    assert any("sqlite" in problem for problem in settings.validate_startup())


def test_a_sound_production_configuration_raises_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard rails above would also pass if everything were rejected."""
    settings = _settings(
        monkeypatch,
        METREO_ENVIRONMENT="production",
        METREO_AUTH_MODE="jwt",
        METREO_JWT_SECRET="a-secret-long-enough-0123456789",
        METREO_DATABASE_URL="postgresql+psycopg://user@host/db",
    )
    assert settings.validate_startup() == []


def test_development_keeps_its_shortcuts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, METREO_ENVIRONMENT="development", METREO_AUTH_MODE="dev")
    assert settings.validate_startup() == []


# -- the AI provider is off, and nothing depends on it ---------------------


def test_the_ai_service_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, METREO_ENVIRONMENT="development")
    assert settings.ai_enabled is False
    assert settings.ai_provider == "null"


def test_no_secret_has_a_usable_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METREO_JWT_SECRET", raising=False)
    settings = _settings(monkeypatch, METREO_ENVIRONMENT="development")
    assert settings.jwt_secret == ""


def test_env_example_carries_no_usable_secret() -> None:
    """A committed example file must document names, never values."""
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^METREO_(JWT_SECRET|[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|KEY))=(.+)$", line)
        assert match is None, f"valeur renseignée pour un secret dans .env.example : {line}"
