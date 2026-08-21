"""Version numbering and status transitions for a price library.

The route used to read every existing version number, take the maximum and add
one. Two callers doing that at the same instant both chose the same successor;
the second one violated ``uq_pbv_book_number`` and, since nothing caught the
``IntegrityError``, the caller received a 500 for a perfectly legitimate
request.

Both operations here take a lock first, in the order documented in
:mod:`~metreo_api.services.locking`: the :class:`~metreo_api.models.PriceBook`
before numbering its versions, the
:class:`~metreo_api.models.PriceBookVersion` itself before deciding on its
status. The unique constraint stays as the last line of defence.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PriceBook, PriceBookVersion
from .locking import lock_owned


def next_version_number(session: Session, *, organization_id: str, price_book_id: str) -> int:
    """Next free version number for a library, allocated under a lock."""
    lock_owned(session, PriceBook, organization_id, price_book_id, label="Bibliothèque")
    numbers = session.scalars(
        select(PriceBookVersion.version_number).where(
            PriceBookVersion.price_book_id == price_book_id
        )
    ).all()
    return (max(numbers) if numbers else 0) + 1


def lock_version(session: Session, *, organization_id: str, version_id: str) -> PriceBookVersion:
    """Hold a version so its status cannot change under our feet.

    Publishing and writing into a version are the two sides of one race:
    reading ``draft``, then acting, without holding the row means a price can
    land in a version that has just been published — a modification of
    something the product presents as frozen.
    """
    return lock_owned(session, PriceBookVersion, organization_id, version_id, label="Version")
