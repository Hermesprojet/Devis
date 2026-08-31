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
* the :class:`~metreo_api.models.DocumentRevision` before claiming a pipeline
  step and the :class:`~metreo_api.models.DocumentStepRun` before changing its
  state;
* the version row itself before publishing it, writing into it, or freezing
  it — there the row being decided *is* the row to lock.

The unique constraints stay as the last line of defence. A lock is a
convention between well-behaved writers; a constraint is what the database
enforces regardless.

**Lock order.** Deadlocks replace races when two transactions take the same
locks in opposite orders. The order every request actually follows is:

    business row  →  Organization

The business row is the one being decided — the ``PriceBook`` or ``Estimate``
whose children are being numbered, or the version whose status is being
changed. ``Organization`` comes last because ``audit.record`` locks it to
allocate the audit sequence, and auditing happens *after* the act it records.

When a caller takes two business rows it takes them in the order of
``LOCK_ORDER``. Only one does today: committing an import locks the
``ImportBatch`` it consumes, then the ``PriceBookVersion`` it writes into.
Numbering locks only the parent; a status change locks only the row whose
status changes.

The rule for anything added later: **never lock ``Organization`` before a
business row.** Doing so inverts the order against every existing request and
turns a race into a deadlock — which is worse, because a deadlock fails even
when nothing was contended in a harmful way.

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

#: Ordered acquisition sequence. A caller that takes two of these must take
#: them in this order, and ``Organization`` — locked last by ``audit.record``
#: — comes after all of them.
#:
#: Only one caller takes two today: committing an import locks the
#: ``ImportBatch`` it consumes, then the ``PriceBookVersion`` it writes into.
#: The order is checked, not merely written down, by
#: ``apps/api/tests/test_lock_order.py``.
LOCK_ORDER: tuple[str, ...] = (
    "ImportBatch",
    "BoqItem",
    "Document",
    "DocumentRevision",
    "DocumentStepRun",
    "PriceBook",
    "PriceBookVersion",
    "Estimate",
    "EstimateVersion",
    # Le devis remis vient après la version dont il est issu. Une décision
    # commerciale — acceptation ou refus — le verrouille avant de lire son
    # journal, ce qui sérialise deux réponses opposées simultanées.
    "IssuedQuote",
)

#: Kept as an alias so callers read as intent rather than as sequence when they
#: only take one.
LOCKABLE: frozenset[str] = frozenset(LOCK_ORDER)


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
    # `raise`, pas `assert` : `python -O` supprime les assertions, et une règle
    # de cohérence qui disparaît en mode optimisé ne protège rien là où elle
    # compte. Vérifié dans un sous-processus par
    # `test_the_refusal_survives_python_optimised_mode`.
    if model.__name__ not in LOCKABLE:
        raise RuntimeError(
            f"{model.__name__} n'est pas dans la liste des lignes verrouillables ; "
            "l'ajouter à LOCK_ORDER, à sa place dans la séquence, après avoir "
            "vérifié qu'il ne crée pas de cycle — et jamais Organization avant une "
            "ligne métier."
        )
    if not supports_row_locks(session):
        return get_owned(session, model, organization_id, object_id, label=label)

    instance = session.scalars(
        owned_query(model, organization_id)
        .where(model.id == object_id)  # type: ignore[attr-defined]
        # `FOR NO KEY UPDATE`, pas `FOR UPDATE` : le second s'oppose au
        # `FOR KEY SHARE` que PostgreSQL prend sur cette ligne dès qu'une
        # autre en référence la clé, ce qui transforme une lecture-écriture
        # ordinaire en interblocage. Le premier s'oppose à lui-même — deux
        # décideurs restent donc sérialisés — sans gêner les insertions qui
        # ne font que pointer vers la ligne.
        .with_for_update(key_share=True)
    ).one_or_none()
    if instance is None:
        # Deliberately routed through get_owned so the 404 payload is built in
        # exactly one place.
        return get_owned(session, model, organization_id, object_id, label=label)
    return instance
