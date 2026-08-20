"""Projects / tender files."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Project, utcnow
from ..schemas import Page, ProjectCreate, ProjectOut, ProjectPage, ProjectUpdate
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit
from ..services.tenant import get_owned, owned_query

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectPage, summary="Lister les projets")
def list_projects(
    q: str | None = Query(default=None, description="Recherche sur référence, nom ou client"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require(Permission.PROJECT_READ)),
    session: Session = Depends(session_scope),
) -> ProjectPage:
    query = owned_query(Project, context.organization_id)
    if q:
        pattern = f"%{q.lower()}%"
        query = query.where(
            func.lower(Project.reference).like(pattern)
            | func.lower(Project.name).like(pattern)
            | func.lower(func.coalesce(Project.client_name, "")).like(pattern)
        )
    if status_filter:
        query = query.where(Project.status == status_filter)

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = session.scalars(
        query.order_by(Project.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return ProjectPage(
        items=[ProjectOut.model_validate(item) for item in items],
        page=Page(total=int(total or 0), limit=limit, offset=offset),
    )


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un projet",
)
def create_project(
    payload: ProjectCreate,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> Project:
    project = Project(
        organization_id=context.organization_id,
        created_by=context.user.id,
        **payload.model_dump(),
    )
    session.add(project)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_reference",
                "message": f"La référence « {payload.reference} » existe déjà.",
            },
        ) from exc
    audit.record(
        session,
        organization_id=context.organization_id,
        action="project.created",
        object_type="project",
        object_id=project.id,
        summary=f"Projet {project.reference} créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"reference": project.reference, "name": project.name},
    )
    return project


@router.get("/{project_id}", response_model=ProjectOut, summary="Détail d'un projet")
def get_project(
    project_id: str,
    context: TenantContext = Depends(require(Permission.PROJECT_READ)),
    session: Session = Depends(session_scope),
) -> Project:
    return get_owned(session, Project, context.organization_id, project_id, label="Projet")


@router.patch("/{project_id}", response_model=ProjectOut, summary="Modifier un projet")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> Project:
    project = get_owned(session, Project, context.organization_id, project_id, label="Projet")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(project, key, value)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="project.updated",
        object_type="project",
        object_id=project.id,
        summary=f"Projet {project.reference} modifié",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"fields": sorted(changes)},
    )
    return project


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer (logiquement) un projet",
)
def delete_project(
    project_id: str,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> None:
    project = get_owned(session, Project, context.organization_id, project_id, label="Projet")
    project.deleted_at = utcnow()
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="project.deleted",
        object_type="project",
        object_id=project.id,
        summary=f"Projet {project.reference} supprimé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
