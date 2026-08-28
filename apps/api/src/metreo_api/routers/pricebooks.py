"""Price library: books, versions, items, CSV import and sub-details."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from metreo_domain.errors import UnknownUnitError
from metreo_domain.units import get_unit

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import (
    CompositeComponentRow,
    CompositePriceRow,
    ImportBatch,
    PriceBook,
    PriceBookVersion,
    PriceItem,
    utcnow,
)
from ..schemas import (
    CompositePriceCreate,
    CompositePriceOut,
    ImportCommitOut,
    ImportCommitRequest,
    ImportPreviewOut,
    Page,
    PriceBookCreate,
    PriceBookOut,
    PriceBookVersionOut,
    PriceItemCreate,
    PriceItemOut,
    PriceItemPage,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit, price_import, pricebook_versions
from ..services.composites import spec_from_row, validate_spec
from ..services.locking import lock_owned
from ..services.price_contract import as_http_detail, validate_price_row
from ..services.tenant import get_owned, owned_query

router = APIRouter(prefix="/price-books", tags=["price-books"])


@router.get("", response_model=list[PriceBookOut], summary="Lister les bibliothèques de prix")
def list_price_books(
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> list[PriceBook]:
    return list(
        session.scalars(
            owned_query(PriceBook, context.organization_id).order_by(PriceBook.name)
        ).all()
    )


@router.post(
    "",
    response_model=PriceBookOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une bibliothèque de prix",
)
def create_price_book(
    payload: PriceBookCreate,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> PriceBook:
    book = PriceBook(organization_id=context.organization_id, **payload.model_dump())
    session.add(book)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "duplicate_name", "message": "Ce nom est déjà utilisé."},
        ) from exc
    version = PriceBookVersion(
        organization_id=context.organization_id,
        price_book_id=book.id,
        version_number=1,
        label="Version initiale",
        status="draft",
        created_by=context.user.id,
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_book.created",
        object_type="price_book",
        object_id=book.id,
        summary=f"Bibliothèque « {book.name} » créée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return book


@router.get(
    "/{price_book_id}/versions",
    response_model=list[PriceBookVersionOut],
    summary="Versions d'une bibliothèque",
)
def list_versions(
    price_book_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> list[PriceBookVersion]:
    get_owned(session, PriceBook, context.organization_id, price_book_id, label="Bibliothèque")
    return list(
        session.scalars(
            select(PriceBookVersion)
            .where(
                PriceBookVersion.organization_id == context.organization_id,
                PriceBookVersion.price_book_id == price_book_id,
            )
            .order_by(PriceBookVersion.version_number.desc())
        ).all()
    )


@router.post(
    "/{price_book_id}/versions",
    response_model=PriceBookVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une nouvelle version de bibliothèque",
)
def create_version(
    price_book_id: str,
    label: str | None = None,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> PriceBookVersion:
    version = PriceBookVersion(
        organization_id=context.organization_id,
        price_book_id=price_book_id,
        # Verrouille la bibliothèque avant de compter : deux créations
        # simultanées choisissaient le même numéro et la seconde heurtait
        # uq_pbv_book_number, ce qui remontait en 500.
        version_number=pricebook_versions.next_version_number(
            session,
            organization_id=context.organization_id,
            price_book_id=price_book_id,
        ),
        label=label,
        status="draft",
        created_by=context.user.id,
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_book_version.created",
        object_type="price_book_version",
        object_id=version.id,
        summary=f"Version {version.version_number} créée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return version


@router.post(
    "/versions/{version_id}/publish",
    response_model=PriceBookVersionOut,
    summary="Publier une version (la rend non modifiable)",
)
def publish_version(
    version_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> PriceBookVersion:
    # Verrouillée puis relue : une publication concurrente doit attendre, puis
    # constater l'état final plutôt que publier deux fois.
    version = pricebook_versions.lock_version(
        session, organization_id=context.organization_id, version_id=version_id
    )
    if version.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_published", "message": "Version déjà publiée."},
        )
    version.status = "published"
    version.published_at = utcnow()
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_book_version.published",
        object_type="price_book_version",
        object_id=version.id,
        summary=f"Version {version.version_number} publiée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return version


@router.get(
    "/versions/{version_id}/items",
    response_model=PriceItemPage,
    summary="Lister les prix d'une version",
)
def list_items(
    version_id: str,
    q: str | None = Query(default=None, description="Recherche code ou libellé"),
    family: str | None = None,
    unit_code: str | None = None,
    resource_kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> PriceItemPage:
    get_owned(session, PriceBookVersion, context.organization_id, version_id, label="Version")
    query = select(PriceItem).where(
        PriceItem.organization_id == context.organization_id,
        PriceItem.price_book_version_id == version_id,
    )
    if q:
        pattern = f"%{q.lower()}%"
        query = query.where(
            func.lower(PriceItem.code).like(pattern) | func.lower(PriceItem.label).like(pattern)
        )
    if family:
        query = query.where(PriceItem.family == family)
    if unit_code:
        query = query.where(PriceItem.unit_code == unit_code)
    if resource_kind:
        query = query.where(PriceItem.resource_kind == resource_kind)

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = session.scalars(query.order_by(PriceItem.code).limit(limit).offset(offset)).all()
    return PriceItemPage(
        items=[PriceItemOut.model_validate(item) for item in items],
        page=Page(total=int(total or 0), limit=limit, offset=offset),
    )


@router.post(
    "/versions/{version_id}/items",
    response_model=PriceItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un prix à la main",
)
def create_item(
    version_id: str,
    payload: PriceItemCreate,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> PriceItem:
    _version_open_for_writing(session, context, version_id)

    # Même contrat que l'import : une règle ajoutée s'applique aux deux, et la
    # prévisualisation ne peut plus annoncer valide ce que la saisie refuse.
    outcome = validate_price_row(payload.model_dump())
    if not outcome.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=as_http_detail(outcome.errors),
        )

    item = PriceItem(
        organization_id=context.organization_id,
        price_book_version_id=version_id,
        **outcome.normalized,
    )
    session.add(item)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_code",
                "message": f"Le code « {payload.code} » existe déjà.",
            },
        ) from exc
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_item.created",
        object_type="price_item",
        object_id=item.id,
        summary=f"Prix {item.code} créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return item


def _refuse_if_published(version: PriceBookVersion) -> None:
    if version.status == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "version_published",
                "message": (
                    "Cette version est publiée et ne peut plus être modifiée. "
                    "Créez une nouvelle version."
                ),
            },
        )


def _version_open_for_writing(
    session: Session, context: TenantContext, version_id: str
) -> PriceBookVersion:
    """Lock the version, then read its status — in that order.

    Reading the status and *then* writing leaves room for a publication to
    land in between: the price would be added to a version the product
    presents as frozen. Holding the row makes the two operations sequential —
    whichever arrives second waits, then sees the final state and answers
    `409 version_published` instead of writing.
    """
    version = pricebook_versions.lock_version(
        session, organization_id=context.organization_id, version_id=version_id
    )
    _refuse_if_published(version)
    return version


@router.post(
    "/versions/{version_id}/imports/preview",
    response_model=ImportPreviewOut,
    summary="Prévisualiser un import CSV (aucune écriture)",
)
async def preview_import(
    version_id: str,
    file: UploadFile = File(...),
    strategy: str = Form(default="create"),
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> ImportPreviewOut:
    # Lecture simple, sans verrou : la suite `await file.read()` peut durer, et
    # tenir une ligne verrouillée pendant la lecture d'un fichier bloquerait
    # toute publication. La prévisualisation n'écrit aucun prix ; c'est le
    # commit qui verrouille et refuse, et c'est lui qui fait foi.
    version = get_owned(
        session, PriceBookVersion, context.organization_id, version_id, label="Version"
    )
    _refuse_if_published(version)

    payload = await file.read()
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "file_too_large",
                "message": f"Fichier trop volumineux (max {settings.max_upload_bytes} octets).",
            },
        )
    if strategy not in ("create", "replace", "ignore", "merge"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_strategy", "message": f"Stratégie « {strategy} » inconnue."},
        )

    batch, meta = price_import.create_preview(
        session,
        organization_id=context.organization_id,
        price_book_version_id=version_id,
        filename=file.filename or "import.csv",
        payload=payload,
        strategy=strategy,  # type: ignore[arg-type]
        default_currency=context.organization.currency,
        created_by=context.user.id,
    )
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_import.previewed",
        object_type="import_batch",
        object_id=batch.id,
        summary=(
            f"Import « {batch.filename} » prévisualisé: "
            f"{batch.valid_count} ligne(s) valide(s), {batch.error_count} en erreur"
        ),
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"sha256": batch.sha256, "row_count": batch.row_count},
    )
    return ImportPreviewOut(**price_import.batch_report(batch, meta))


@router.get(
    "/imports/{batch_id}",
    response_model=ImportPreviewOut,
    summary="Relire un rapport d'import",
)
def get_import(
    batch_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> ImportPreviewOut:
    batch = get_owned(session, ImportBatch, context.organization_id, batch_id, label="Import")
    return ImportPreviewOut(**price_import.batch_report(batch))


@router.post(
    "/imports/{batch_id}/commit",
    response_model=ImportCommitOut,
    summary="Confirmer l'import (écriture réelle)",
)
def commit_import(
    batch_id: str,
    payload: ImportCommitRequest,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> ImportCommitOut:
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "confirmation_required",
                "message": "Confirmez explicitement l'écriture dans la bibliothèque.",
            },
        )
    # Verrouillé, puis relu — dans cet ordre. Lire le statut d'abord laissait
    # deux requêtes franchir la garde ensemble : la seconde attendait le verrou
    # de version, puis rejouait l'import sur son objet resté « previewed » en
    # mémoire, écrasant `committed_at` et écrivant un second événement
    # `price_import.committed` pour un seul lot. Un double clic suffisait.
    batch = lock_owned(session, ImportBatch, context.organization_id, batch_id, label="Import")
    _version_open_for_writing(session, context, batch.price_book_version_id)
    session.refresh(batch)
    if batch.status != "previewed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "batch_not_pending",
                "message": f"Cet import est déjà « {batch.status} ».",
            },
        )

    outcome = price_import.commit_batch(session, batch, strategy=payload.strategy)
    audit.record(
        session,
        organization_id=context.organization_id,
        action="price_import.committed",
        object_type="import_batch",
        object_id=batch.id,
        summary=(
            f"Import « {batch.filename} » confirmé: {outcome['created']} créé(s), "
            f"{outcome['updated']} mis à jour, {outcome['skipped']} ignoré(s)"
        ),
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload=outcome,
    )
    return ImportCommitOut(batch_id=batch.id, **outcome)


@router.get(
    "/versions/{version_id}/composites",
    response_model=list[CompositePriceOut],
    summary="Sous-détails de prix d'une version",
)
def list_composites(
    version_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> list[CompositePriceOut]:
    get_owned(session, PriceBookVersion, context.organization_id, version_id, label="Version")
    rows = session.scalars(
        select(CompositePriceRow)
        .where(
            CompositePriceRow.organization_id == context.organization_id,
            CompositePriceRow.price_book_version_id == version_id,
        )
        .order_by(CompositePriceRow.code)
    ).all()
    return [_composite_out(row) for row in rows]


@router.post(
    "/versions/{version_id}/composites",
    response_model=CompositePriceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un sous-détail de prix",
)
def create_composite(
    version_id: str,
    payload: CompositePriceCreate,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> CompositePriceOut:
    _version_open_for_writing(session, context, version_id)

    # `validate_spec` canonicalise les unités sur place. Les spécifications
    # sont donc conservées et serviront à écrire les lignes : valider une copie
    # jetable puis écrire l'objet d'origine perdait la canonicalisation, et
    # « tonne » atteignait la base au lieu de « t ».
    specs: list[dict[str, object]] = [c.model_dump() for c in payload.components]
    problems: list[dict[str, object]] = []
    for index, spec in enumerate(specs):
        for message in validate_spec(spec):
            problems.append({"index": index, "message": message})
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_composite",
                "message": "Sous-détail invalide.",
                "problems": problems,
            },
        )

    try:
        composite_unit = get_unit(payload.unit_code).code
    except UnknownUnitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_unit", "message": exc.message},
        ) from exc

    composite = CompositePriceRow(
        organization_id=context.organization_id,
        price_book_version_id=version_id,
        code=payload.code,
        label=payload.label,
        unit_code=composite_unit,
        notes=payload.notes,
    )
    session.add(composite)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_code",
                "message": f"Le code « {payload.code} » existe déjà.",
            },
        ) from exc

    for index, data in enumerate(specs):
        session.add(
            CompositeComponentRow(
                organization_id=context.organization_id,
                composite_price_id=composite.id,
                sort_index=index,
                component_type=data["component_type"],
                label=data["label"],
                resource_kind=data["resource_kind"],
                consumption=data.get("consumption"),
                resource_unit_code=data.get("resource_unit_code"),
                unit_price=data.get("unit_price"),
                loss_ratio=data.get("loss_ratio"),
                convert_boq_quantity=bool(data.get("convert_boq_quantity")),
                density_value=data.get("density_value"),
                density_source=data.get("density_source"),
                output_rate=data.get("output_rate"),
                hourly_rate=data.get("hourly_rate"),
                crew_size=data.get("crew_size"),
                payload_value=data.get("payload_value"),
                payload_unit_code=data.get("payload_unit_code"),
                cost_per_rotation=data.get("cost_per_rotation"),
                round_up=bool(data.get("round_up", True)),
                distance_km=data.get("distance_km"),
                rate_per_km=data.get("rate_per_km"),
                lump_sum_amount=data.get("lump_sum_amount"),
            )
        )
    session.flush()
    session.refresh(composite)
    audit.record(
        session,
        organization_id=context.organization_id,
        action="composite_price.created",
        object_type="composite_price",
        object_id=composite.id,
        summary=f"Sous-détail {composite.code} créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"components": len(payload.components)},
    )
    return _composite_out(composite)


def _composite_out(row: CompositePriceRow) -> CompositePriceOut:
    return CompositePriceOut(
        id=row.id,
        code=row.code,
        label=row.label,
        unit_code=row.unit_code,
        notes=row.notes,
        is_demo_data=row.is_demo_data,
        components=[spec_from_row(component) for component in row.components],
    )
