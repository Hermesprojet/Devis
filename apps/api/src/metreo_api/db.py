"""Database engine, session factory and portable column types.

The application targets PostgreSQL. SQLite is supported so the test suite and a
first local run need no service; every construct used in the models works on
both, and ``docs/adr/0002-multi-tenancy.md`` records what is deliberately kept
out (row-level security, PostGIS) until the Postgres-only phase.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import get_settings


class Amount(TypeDecorator):
    """Exact decimal storage on every backend.

    PostgreSQL gets a real ``NUMERIC``. SQLite has no decimal type and would
    round-trip through a binary float, so the value is stored as text and
    rebuilt as :class:`~decimal.Decimal` on the way out. Monetary arithmetic
    happens in the domain layer, never in SQL, so nothing is lost.
    """

    impl = Numeric(28, 10)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(28, 10))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "sqlite":
            return format(decimal_value, "f")
        return decimal_value

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class Base(DeclarativeBase):
    """Declarative base for every persisted model."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {"echo": settings.sql_echo, "future": True}
        if settings.database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(settings.database_url, **kwargs)
        if _engine.dialect.name == "sqlite":
            event.listen(_engine, "connect", _apply_sqlite_pragmas)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory


def reset_engine() -> None:
    """Drop the cached engine. Used by tests that switch database URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def session_scope() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
