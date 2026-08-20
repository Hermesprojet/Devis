"""Application settings.

Everything configurable comes from the environment. No secret has a usable
default: ``AUTH_MODE=dev`` is the only mode that works without one, and it
refuses to start when ``ENVIRONMENT`` is not a development environment.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METREO_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Metreo API"
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite+pysqlite:///./var/metreo.sqlite3"
    sql_echo: bool = False

    # Authentication -------------------------------------------------------
    # "dev" issues tokens for seeded users without a password. It is a local
    # convenience, never a production mode.
    auth_mode: Literal["dev", "jwt"] = "dev"
    jwt_secret: str = Field(default="", repr=False)
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 8 * 3600

    # Storage --------------------------------------------------------------
    storage_root: str = "./var/storage"
    max_upload_bytes: int = 25 * 1024 * 1024

    # AI / OCR -------------------------------------------------------------
    # Phase 1 ships no provider. The flag exists so acceptance scenario 10
    # ("the product stays usable with AI disabled") is a real switch and not a
    # promise.
    ai_enabled: bool = False
    ai_provider: Literal["null", "local_stub"] = "null"

    cors_origins: list[str] = ["http://localhost:3000"]
    default_currency: str = "EUR"
    default_locale: str = "fr-BE"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment in ("staging", "production")

    def effective_jwt_secret(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        if self.is_production:
            raise RuntimeError("METREO_JWT_SECRET is required outside development environments")
        return "dev-only-insecure-secret"

    def validate_startup(self) -> list[str]:
        """Return the list of configuration problems, worst first."""
        problems: list[str] = []
        if self.is_production:
            if self.auth_mode == "dev":
                problems.append("auth_mode=dev is forbidden in staging/production")
            if not self.jwt_secret:
                problems.append("jwt_secret must be set in staging/production")
            if self.database_url.startswith("sqlite"):
                problems.append("sqlite is not supported in staging/production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
