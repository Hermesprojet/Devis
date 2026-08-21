"""Pessimistic row locks for the read-then-write sequences.

Three places computed a value from what they had just read and then wrote it:
the next price-book version number, the next estimate version number, and the
freeze of an estimate version. Between the read and the write nothing held a
second transaction back, so two callers read the same state and both acted on
it. The unique constraints kept the *data* correct — the second writer hit
``uq_pbv_book_number`` or ``uq_estimateversion_number`` — but the *service* was
not: the ``IntegrityError`` was not caught in the routes, so the caller got a
500 for doing something perfectly legitimate.

The fix is to lock the parent row before reading the children, so the two
callers queue instead of colliding:

* the :class:`~metreo_api.models.PriceBook` before numbering its versions;
* the :class:`~metreo_api.models.Estimate` before numbering its versions;
* the version row itself before publishing it, writing into it, or freezing
  it — there the row being decided *is* the row to lock.

The unique constraints stay as the last line of defence. A lock is a
convention between well-behaved writers; a constraint is what the database
enforces regardless.

**Lock order.** Deadlocks replace races when two transactions take the same
locks in opposite orders. Every caller here therefore acquires locks in the
order below, outermost first, and never takes one it already holds a *later*
one than:

    Organization  →  PriceBook  →  PriceBookVersion  →  Estimate  →  EstimateVersion

``audit.record`` locks ``Organization``, the outermost, and is always called
*after* the business lock in this module — which is the one case where the
order would be violated. It is not, because ``record`` takes its lock in the
same transaction that already holds the later one, and a transaction never
waits on itself; the ordering rule constrains what two *different*
transactions may interleave, and both of them follow the same sequence.

SQLite gets no ``FOR UPDATE``: it does not implement it, and it serialises
writers at the file level anyway. That is also why the concurrency tests skip
unless a real PostgreSQL is configured — passing on SQLite would prove
nothing.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm import Session

from ..db import Base
from .tenant import get_owned, owned_query

ModelT = TypeVar("ModelT", bound=Base)

#: Documented acquisition order; see the module docstring. Kept as data so a
#: future caller can be checked against it rather than trusting a comment.
LOCK_ORDER: tuple[str, ...] = (
    "Organization",
    "PriceBook",
    "PriceBookVersion",
    "Estimate",
    "EstimateVersion",
)


def supports_row_locks(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name != "sqlite"


def lock_owned(
    session: Session,
    model: type[ModelT],
    organization_id: str,
    object_id: str,
    *,
    label: str | None = None,
) -> ModelT:
    """Read one row of this tenant and hold it until the transaction ends.

    Same 404 semantics as :func:`~metreo_api.services.tenant.get_owned`: a row
    belonging to another tenant is not found, never forbidden. The tenant
    filter is part of the locking query, so a caller cannot lock a row it is
    not allowed to see.
    """
    assert model.__name__ in LOCK_ORDER, (
        f"{model.__name__} n'a pas de place dans l'ordre de verrouillage documenté ; "
        "l'ajouter à LOCK_ORDER après avoir vérifié qu'il ne crée pas de cycle."
    )
    if not supports_row_locks(session):
        return get_owned(session, model, organization_id, object_id, label=label)

    instance = session.scalars(
        owned_query(model, organization_id)
        .where(model.id == object_id)  # type: ignore[attr-defined]
        .with_for_update()
    ).one_or_none()
    if instance is None:
        # Deliberately routed through get_owned so the 404 payload is built in
        # exactly one place.
        return get_owned(session, model, organization_id, object_id, label=label)
    return instance
