"""Tenant-scoped data access helpers.

Every read of a business row goes through :func:`get_owned` so the
``organization_id`` filter cannot be forgotten. A row belonging to another
tenant is reported as **404, not 403**: telling a caller that an id exists but
is not theirs already leaks information.
"""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..db import Base

ModelT = TypeVar("ModelT", bound=Base)


def owned_query(model: type[ModelT], organization_id: str) -> Select:
    query = select(model).where(model.organization_id == organization_id)  # type: ignore[attr-defined]
    if hasattr(model, "deleted_at"):
        query = query.where(model.deleted_at.is_(None))  # type: ignore[attr-defined]
    return query


def find_owned(
    session: Session, model: type[ModelT], organization_id: str, object_id: str
) -> ModelT | None:
    return session.scalars(
        owned_query(model, organization_id).where(model.id == object_id)  # type: ignore[attr-defined]
    ).one_or_none()


def get_owned(
    session: Session,
    model: type[ModelT],
    organization_id: str,
    object_id: str,
    *,
    label: str | None = None,
) -> ModelT:
    instance = find_owned(session, model, organization_id, object_id)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "not_found",
                "message": f"{label or model.__name__} introuvable.",
                "object_id": object_id,
            },
        )
    return instance
