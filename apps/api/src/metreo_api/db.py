"""Database engine, session factory and portable column types.

The application targets PostgreSQL. SQLite is supported so the test suite and a
first local run need no service; every construct used in the models works on
both, and ``docs/adr/0002-multi-tenancy.md`` records what is deliberately kept
out (row-level security, PostGIS) until the Postgres-only phase.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from sqlalchemy import Numeric, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from metreo_domain.money import normalize_decimal

from .config import get_settings

#: Declared precision and scale of every stored decimal column.
#:
#: 28 significant digits with 10 decimals: wide enough for a quantity, a unit
#: price or a total in cents, and identical on both backends. Values carrying
#: more than :data:`AMOUNT_SCALE` decimals — an unrounded total such as
#: ``99097.07595267450389610389611``, produced by the engine's 28-digit working
#: precision — do **not** fit. PostgreSQL would round them on the way in
#: without saying so. The full-precision figure lives in the frozen snapshot,
#: which is JSON and keeps every digit; the columns are a queryable copy.
AMOUNT_PRECISION: Final[int] = 28
AMOUNT_SCALE: Final[int] = 10
_AMOUNT_EXPONENT: Final[Decimal] = Decimal(1).scaleb(-AMOUNT_SCALE)


class Amount(TypeDecorator):
    """Decimal storage that behaves identically on every backend.

    PostgreSQL gets a real ``NUMERIC(28, 10)``. SQLite has no decimal type and
    would round-trip through a binary float, so the value is stored as text and
    rebuilt as :class:`~decimal.Decimal` on the way out. Monetary arithmetic
    happens in the domain layer, never in SQL.

    Both directions are normalised so the two backends cannot drift apart:

    * on the way **in**, the value is quantised to :data:`AMOUNT_SCALE`
      decimals. PostgreSQL does this anyway; doing it explicitly stops SQLite
      from keeping digits its counterpart silently drops, which would otherwise
      make a frozen estimate compare equal on one backend and unequal on the
      other;
    * on the way **out**, the value is normalised, so the padding
      ``NUMERIC`` adds never reaches the arithmetic or the digest.
    """

    impl = Numeric(AMOUNT_PRECISION, AMOUNT_SCALE)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(AMOUNT_PRECISION, AMOUNT_SCALE))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        decimal_value = decimal_value.quantize(_AMOUNT_EXPONENT, rounding=ROUND_HALF_UP)
        if dialect.name == "sqlite":
            return format(decimal_value, "f")
        return decimal_value

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        """Hand back the canonical decimal, not the backend's spelling of it.

        ``NUMERIC(28, 10)`` pads: PostgreSQL returns a 6 % rate as
        ``Decimal("0.0600000000")`` and a zero quantity as ``Decimal("0E-10")``,
        while SQLite returns what was written. Left alone, that padding
        propagates through every product computed from the value and ends up in
        explanation strings, exports and the frozen snapshot digest — the same
        estimate would hash differently depending on the database it was read
        from. Normalising here fixes it once, at the boundary, instead of at
        every rendering site.
        """
        if value is None:
            return None
        return normalize_decimal(value)


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
