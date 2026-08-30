"""Le répertoire des clients d'une organisation.

Jusqu'ici, « le client » était deux chaînes libres sur le projet. Deux
chantiers pour la même entreprise ne partageaient rien, et un devis imprimait
l'adresse du CHANTIER faute d'en connaître une autre.

Une fiche s'archive, elle ne se supprime pas : un devis émis en porte
l'instantané, et l'effacer laisserait un document qui désigne un néant. Le
refus est explicite plutôt que silencieux — voir `archive_client`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Client, IssuedQuote, Project
from ..schemas import ClientCreate, ClientOut, ClientUpdate
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit
from ..services.tenant import get_owned, owned_query
from ..transactions import RouteTransactionnelle

router = APIRouter(prefix="/clients", tags=["clients"], route_class=RouteTransactionnelle)


@router.get("", response_model=list[ClientOut], summary="Lister les clients")
def list_clients(
    q: str | None = Query(default=None, description="Nom ou numéro d'entreprise"),
    include_archived: bool = Query(default=False),
    context: TenantContext = Depends(require(Permission.PROJECT_READ)),
    session: Session = Depends(session_scope),
) -> list[Client]:
    """La recherche porte sur le nom ET le numéro d'entreprise.

    Insensible à la casse des deux côtés : un artisan tape « dupont », sa fiche
    s'appelle « Ets DUPONT ». Un `LIKE` sur la valeur brute ne l'aurait pas
    trouvée, et une recherche qui ne trouve pas ce qu'on a saisi est pire
    qu'une absence de recherche.
    """
    requete = owned_query(Client, context.organization_id)
    if not include_archived:
        requete = requete.where(Client.status == "active")
    if q:
        motif = f"%{q.strip().lower()}%"
        requete = requete.where(
            or_(
                func.lower(Client.name).like(motif),
                func.lower(func.coalesce(Client.company_number, "")).like(motif),
            )
        )
    return list(session.scalars(requete.order_by(Client.name)).all())


@router.post(
    "",
    response_model=ClientOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un client",
)
def create_client(
    payload: ClientCreate,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> Client:
    """Aucune fusion sur le nom.

    Deux fiches peuvent porter le même nom, et c'est délibéré : « Dupont » à
    Namur et « Dupont » à Liège sont deux entreprises. Décider qu'elles n'en
    font qu'une est un choix commercial, que ce code n'a pas à prendre pour
    l'utilisateur — et qui serait irréversible.
    """
    client = Client(
        organization_id=context.organization_id,
        created_by=context.user.id,
        status="active",
        **payload.model_dump(),
    )
    session.add(client)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="client.created",
        object_type="client",
        object_id=client.id,
        summary=f"Client {client.name} créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return client


@router.get("/{client_id}", response_model=ClientOut, summary="Détail d'un client")
def read_client(
    client_id: str,
    context: TenantContext = Depends(require(Permission.PROJECT_READ)),
    session: Session = Depends(session_scope),
) -> Client:
    return get_owned(session, Client, context.organization_id, client_id, label="Client")


@router.patch("/{client_id}", response_model=ClientOut, summary="Modifier un client")
def update_client(
    client_id: str,
    payload: ClientUpdate,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> Client:
    """Modifier une fiche ne touche AUCUN devis déjà émis.

    C'est la raison d'être des instantanés : le document remis au client garde
    l'adresse qu'il portait le jour de son émission. Éprouvé par
    `test_devis_emis.py::test_modifier_la_fiche_client_ne_change_pas_un_devis_emis`.
    """
    client = get_owned(session, Client, context.organization_id, client_id, label="Client")
    modifications = payload.model_dump(exclude_unset=True)
    for champ, valeur in modifications.items():
        setattr(client, champ, valeur)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="client.updated",
        object_type="client",
        object_id=client.id,
        summary=f"Client {client.name} modifié",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"fields": sorted(modifications)},
    )
    return client


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archiver un client",
)
def archive_client(
    client_id: str,
    context: TenantContext = Depends(require(Permission.PROJECT_WRITE)),
    session: Session = Depends(session_scope),
) -> None:
    """Archive — et refuse net si la fiche est encore référencée.

    Un `DELETE` qui archive plutôt que de détruire peut surprendre ; le refuser
    quand un chantier ou un devis y renvoie ne surprend personne. Les deux
    ensemble tiennent la promesse : rien de ce qui a été remis à un client ne
    devient illisible parce qu'on a rangé son répertoire.
    """
    client = get_owned(session, Client, context.organization_id, client_id, label="Client")
    projets = session.scalar(
        select(func.count())
        .select_from(Project)
        .where(
            Project.organization_id == context.organization_id,
            Project.client_id == client.id,
            Project.deleted_at.is_(None),
        )
    )
    devis = session.scalar(
        select(func.count())
        .select_from(IssuedQuote)
        .where(
            IssuedQuote.organization_id == context.organization_id,
            IssuedQuote.client_id == client.id,
        )
    )
    if projets or devis:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "client_referenced",
                "message": (
                    "Ce client est encore rattaché à des chantiers ou à des devis émis. "
                    "Détachez-le des chantiers concernés avant de l'archiver ; les devis "
                    "déjà remis, eux, gardent leur copie de la fiche."
                ),
                "projects": int(projets or 0),
                "issued_quotes": int(devis or 0),
            },
        )
    client.status = "archived"
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="client.archived",
        object_type="client",
        object_id=client.id,
        summary=f"Client {client.name} archivé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
