"""Bill of quantities (métré / bordereau)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from metreo_domain.errors import UnknownUnitError
from metreo_domain.units import get_unit

from ..db import session_scope
from ..models import BillOfQuantities, BoqItem, CompositePriceRow, PriceItem, Project
from ..schemas import (
    BoqCreate,
    BoqItemBulkCreate,
    BoqItemCreate,
    BoqItemOut,
    BoqItemTransition,
    BoqItemUpdate,
    BoqOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit
from ..services.tenant import get_owned, owned_query

router = APIRouter(tags=["bill-of-quantities"])


def _canonical_unit(unit_code: str) -> str:
    try:
        return get_unit(unit_code).code
    except UnknownUnitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_unit", "message": exc.message, "unit": unit_code},
        ) from exc


def _check_price_links(
    session: Session, organization_id: str, payload: BoqItemCreate | BoqItemUpdate
) -> None:
    """A bill-of-quantities row may only point at prices of its own tenant."""
    price_item_id = getattr(payload, "price_item_id", None)
    if price_item_id:
        get_owned(session, PriceItem, organization_id, price_item_id, label="Prix")
    composite_price_id = getattr(payload, "composite_price_id", None)
    if composite_price_id:
        get_owned(
            session,
            CompositePriceRow,
            organization_id,
            composite_price_id,
            label="Sous-détail",
        )


@router.post(
    "/projects/{project_id}/boqs",
    response_model=BoqOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un bordereau",
)
def create_boq(
    project_id: str,
    payload: BoqCreate,
    context: TenantContext = Depends(require(Permission.BOQ_WRITE)),
    session: Session = Depends(session_scope),
) -> BillOfQuantities:
    get_owned(session, Project, context.organization_id, project_id, label="Projet")
    boq = BillOfQuantities(
        organization_id=context.organization_id, project_id=project_id, **payload.model_dump()
    )
    session.add(boq)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="boq.created",
        object_type="boq",
        object_id=boq.id,
        summary=f"Bordereau « {boq.name} » créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return boq


@router.get(
    "/projects/{project_id}/boqs",
    response_model=list[BoqOut],
    summary="Bordereaux d'un projet",
)
def list_boqs(
    project_id: str,
    context: TenantContext = Depends(require(Permission.BOQ_READ)),
    session: Session = Depends(session_scope),
) -> list[BillOfQuantities]:
    get_owned(session, Project, context.organization_id, project_id, label="Projet")
    return list(
        session.scalars(
            owned_query(BillOfQuantities, context.organization_id)
            .where(BillOfQuantities.project_id == project_id)
            .order_by(BillOfQuantities.created_at.desc())
        ).all()
    )


@router.get("/boqs/{boq_id}/items", response_model=list[BoqItemOut], summary="Lignes du bordereau")
def list_items(
    boq_id: str,
    context: TenantContext = Depends(require(Permission.BOQ_READ)),
    session: Session = Depends(session_scope),
) -> list[BoqItem]:
    get_owned(session, BillOfQuantities, context.organization_id, boq_id, label="Bordereau")
    return list(
        session.scalars(
            select(BoqItem)
            .where(BoqItem.organization_id == context.organization_id, BoqItem.boq_id == boq_id)
            .order_by(BoqItem.sort_index, BoqItem.position)
        ).all()
    )


@router.post(
    "/boqs/{boq_id}/items",
    response_model=BoqItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter une ligne",
)
def create_item(
    boq_id: str,
    payload: BoqItemCreate,
    context: TenantContext = Depends(require(Permission.BOQ_WRITE)),
    session: Session = Depends(session_scope),
) -> BoqItem:
    get_owned(session, BillOfQuantities, context.organization_id, boq_id, label="Bordereau")
    _check_price_links(session, context.organization_id, payload)
    data = payload.model_dump()
    data["unit_code"] = _canonical_unit(data["unit_code"])
    if data.get("sort_index") is None:
        existing = session.scalars(select(BoqItem.sort_index).where(BoqItem.boq_id == boq_id)).all()
        data["sort_index"] = (max(existing) + 10) if existing else 0

    item = BoqItem(organization_id=context.organization_id, boq_id=boq_id, **data)
    session.add(item)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_position",
                "message": f"Le poste « {payload.position} » existe déjà dans ce bordereau.",
            },
        ) from exc
    audit.record(
        session,
        organization_id=context.organization_id,
        action="boq_item.created",
        object_type="boq_item",
        object_id=item.id,
        summary=f"Poste {item.position} ajouté",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"quantity": str(item.quantity), "unit": item.unit_code},
    )
    return item


@router.post(
    "/boqs/{boq_id}/items:bulk",
    response_model=list[BoqItemOut],
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter plusieurs lignes",
)
def bulk_create_items(
    boq_id: str,
    payload: BoqItemBulkCreate,
    context: TenantContext = Depends(require(Permission.BOQ_WRITE)),
    session: Session = Depends(session_scope),
) -> list[BoqItem]:
    get_owned(session, BillOfQuantities, context.organization_id, boq_id, label="Bordereau")
    created: list[BoqItem] = []
    existing = session.scalars(select(BoqItem.sort_index).where(BoqItem.boq_id == boq_id)).all()
    next_index = (max(existing) + 10) if existing else 0
    for offset, entry in enumerate(payload.items):
        _check_price_links(session, context.organization_id, entry)
        data = entry.model_dump()
        data["unit_code"] = _canonical_unit(data["unit_code"])
        if data.get("sort_index") is None:
            data["sort_index"] = next_index + offset * 10
        item = BoqItem(organization_id=context.organization_id, boq_id=boq_id, **data)
        session.add(item)
        created.append(item)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_position",
                "message": "Un des postes existe déjà dans ce bordereau.",
            },
        ) from exc
    audit.record(
        session,
        organization_id=context.organization_id,
        action="boq_item.bulk_created",
        object_type="boq",
        object_id=boq_id,
        summary=f"{len(created)} poste(s) ajouté(s) au bordereau",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return created


@router.patch("/boq-items/{item_id}", response_model=BoqItemOut, summary="Modifier une ligne")
def update_item(
    item_id: str,
    payload: BoqItemUpdate,
    context: TenantContext = Depends(require(Permission.BOQ_WRITE)),
    session: Session = Depends(session_scope),
) -> BoqItem:
    item = get_owned(session, BoqItem, context.organization_id, item_id, label="Poste")
    changes = payload.model_dump(exclude_unset=True)
    override = bool(changes.pop("override_approved", False))
    reason = changes.pop("override_reason", None)

    touches_quantity = "quantity" in changes or "unit_code" in changes
    if item.status == "approved" and touches_quantity and not override:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approved_quantity_locked",
                "message": (
                    "Cette quantité est approuvée. Confirmez explicitement la "
                    "modification (override_approved) et indiquez le motif."
                ),
                "current_quantity": str(item.quantity),
                "current_unit": item.unit_code,
            },
        )
    # Déroger n'est pas une case à cocher : c'est approuver de nouveau. Sans ce
    # contrôle, un porteur de BOQ_WRITE refusé sur /approve modifiait une
    # quantité approuvée en se déclarant lui-même autorisé, et la ligne
    # redescendait à « vérifié » au passage — le verrou se contournait par son
    # propre mécanisme de dérogation.
    if (
        item.status == "approved"
        and touches_quantity
        and override
        and not context.can(Permission.BOQ_APPROVE)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "approval_required_to_override",
                "message": (
                    "Modifier une quantité approuvée exige le droit "
                    "d'approbation. Demandez la dérogation à un responsable "
                    "étude de prix."
                ),
                "required_permission": Permission.BOQ_APPROVE.value,
            },
        )

    if item.status == "approved" and touches_quantity and override and not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "override_reason_required",
                "message": "Un motif est obligatoire pour modifier une quantité approuvée.",
            },
        )

    _check_price_links(session, context.organization_id, payload)
    if changes.get("unit_code"):
        changes["unit_code"] = _canonical_unit(changes["unit_code"])

    before = {key: str(getattr(item, key)) for key in changes}
    for key, value in changes.items():
        setattr(item, key, value)
    if override and touches_quantity:
        item.status = "verified"
    session.flush()

    audit.record(
        session,
        organization_id=context.organization_id,
        # Une dérogation sur quantité approuvée porte son propre nom : la noyer
        # dans « modifié » la rendrait introuvable dans le journal, alors que
        # c'est précisément l'action qu'un auditeur cherchera.
        action=(
            "boq_item.approved_quantity_overridden"
            if override and touches_quantity
            else "boq_item.updated"
        ),
        object_type="boq_item",
        object_id=item.id,
        summary=(
            f"Poste {item.position} modifié"
            + (" (dérogation sur quantité approuvée)" if override and touches_quantity else "")
        ),
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "before": before,
            "after": {k: str(v) for k, v in changes.items()},
            "override_reason": reason,
            "unit": item.unit_code,
            "status_after": item.status,
        },
    )
    return item


#: Machine d'états des lignes de bordereau. Une transition absente est refusée
#: plutôt qu'ignorée : « rejeté » ne revient pas silencieusement à « proposé »,
#: et « approuvé » ne se quitte que par une décision explicite.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # `proposed → approved` est explicitement autorisé. Le raccourci /approve
    # faisait auparavant passer la ligne par « vérifié » en mémoire sans
    # journaliser ce passage : le journal affirmait une vérification que
    # personne n'avait faite. Puisque approuver exige déjà BOQ_APPROVE, le
    # détenteur de ce droit peut approuver directement, et l'audit dit la
    # vérité — from=proposed, to=approved.
    "proposed": frozenset({"verified", "approved", "rejected"}),
    "verified": frozenset({"approved", "proposed", "rejected"}),
    "approved": frozenset({"verified", "rejected"}),
    "rejected": frozenset({"proposed"}),
}


@router.post(
    "/boq-items/{item_id}/transition",
    response_model=BoqItemOut,
    summary="Changer le statut d'une ligne",
)
def transition_item(
    item_id: str,
    payload: BoqItemTransition,
    context: TenantContext = Depends(require(Permission.BOQ_APPROVE)),
    session: Session = Depends(session_scope),
) -> BoqItem:
    """Seul chemin vers un changement de statut.

    Exige `BOQ_APPROVE` dans tous les sens : approuver, rejeter et déclasser
    engagent autant l'un que l'autre. Déclasser sans droit rouvrirait le verrou
    qui protège les quantités approuvées.
    """
    item = get_owned(session, BoqItem, context.organization_id, item_id, label="Poste")
    before = item.status
    target = payload.status

    if target == before:
        return item

    if target not in ALLOWED_TRANSITIONS.get(before, frozenset()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "illegal_status_transition",
                "message": f"Un poste « {before} » ne peut pas passer à « {target} ».",
                "from": before,
                "to": target,
                "allowed": sorted(ALLOWED_TRANSITIONS.get(before, frozenset())),
            },
        )

    if before == "approved" and not payload.reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "transition_reason_required",
                "message": "Quitter le statut approuvé exige un motif.",
            },
        )

    item.status = target
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        # « approuvé » garde son nom propre : c'est l'action que l'on cherche
        # dans le journal, et la noyer dans un générique la rendrait
        # introuvable.
        action=("boq_item.approved" if target == "approved" else "boq_item.status_changed"),
        object_type="boq_item",
        object_id=item.id,
        summary=f"Poste {item.position} : {before} → {target}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "from": before,
            "to": target,
            "quantity": str(item.quantity),
            "unit": item.unit_code,
            "reason": payload.reason,
        },
    )
    return item


@router.post(
    "/boq-items/{item_id}/approve",
    response_model=BoqItemOut,
    summary="Approuver une quantité",
)
def approve_item(
    item_id: str,
    context: TenantContext = Depends(require(Permission.BOQ_APPROVE)),
    session: Session = Depends(session_scope),
) -> BoqItem:
    """Raccourci vers la transition « approuvé ».

    Ne fabrique aucun état intermédiaire : la transition enregistrée est celle
    qui a réellement eu lieu. Une ligne rejetée doit d'abord revenir à
    « proposé », elle ne saute pas directement à « approuvé ».
    """
    item = get_owned(session, BoqItem, context.organization_id, item_id, label="Poste")
    if item.status == "approved":
        return item
    return transition_item(
        item_id,
        BoqItemTransition(status="approved"),
        context=context,
        session=session,
    )


@router.delete(
    "/boq-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une ligne",
)
def delete_item(
    item_id: str,
    context: TenantContext = Depends(require(Permission.BOQ_WRITE)),
    session: Session = Depends(session_scope),
) -> None:
    item = get_owned(session, BoqItem, context.organization_id, item_id, label="Poste")
    position = item.position
    session.delete(item)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="boq_item.deleted",
        object_type="boq_item",
        object_id=item_id,
        summary=f"Poste {position} supprimé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
