"""Price library: books, versions, items, CSV import and sub-details."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError, UnknownUnitError
from metreo_domain.units import get_unit

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import (
    BoqItem,
    CompositeComponentRow,
    CompositePriceRow,
    ImportBatch,
    PriceBook,
    PriceBookVersion,
    PriceItem,
    utcnow,
)
from ..schemas import (
    CompositeDuplicate,
    CompositePreviewIn,
    CompositePreviewOut,
    CompositePriceCreate,
    CompositePriceOut,
    CompositePriceUpdate,
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
from ..services.composites import apercu, spec_from_row, validate_spec
from ..services.estimating import rounding_from_settings
from ..services.locking import lock_owned
from ..services.price_contract import as_http_detail, validate_price_row
from ..services.tenant import get_owned, owned_query
from ..transactions import RouteTransactionnelle

router = APIRouter(prefix="/price-books", tags=["price-books"], route_class=RouteTransactionnelle)


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
    version = get_owned(
        session, PriceBookVersion, context.organization_id, version_id, label="Version"
    )
    rows = session.scalars(
        select(CompositePriceRow)
        .where(
            CompositePriceRow.organization_id == context.organization_id,
            CompositePriceRow.price_book_version_id == version_id,
        )
        .order_by(CompositePriceRow.code)
    ).all()
    return [_composite_out(session, row, version) for row in rows]


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
    version = _version_open_for_writing(session, context, version_id)

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

    _ecrire_les_composants(session, composite, specs, context.organization_id)
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
    return _composite_out(session, composite, version)


def _ecrire_les_composants(
    session: Session,
    composite: CompositePriceRow,
    specs: list[dict[str, object]],
    organization_id: str,
) -> None:
    """Pose les composants d'un sous-détail, dans l'ordre reçu.

    Partagée entre la création et la modification : recopier ces vingt champs
    dans deux fonctions garantissait qu'un champ ajouté un jour n'atterrisse
    que dans l'une des deux, et que la modification perde silencieusement une
    donnée que la création savait écrire.
    """
    for index, data in enumerate(specs):
        session.add(
            CompositeComponentRow(
                organization_id=organization_id,
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


def _references(session: Session, composite_id: str) -> int:
    """Combien de postes de bordereau s'appuient sur ce sous-détail."""
    compte = session.scalar(
        select(func.count()).select_from(BoqItem).where(BoqItem.composite_price_id == composite_id)
    )
    return int(compte or 0)


def _composite_out(
    session: Session, row: CompositePriceRow, version: PriceBookVersion
) -> CompositePriceOut:
    """Le sous-détail, ET ce qui décide des commandes que l'écran peut offrir.

    `version_published` et `referenced_by` voyagent avec la ligne plutôt que
    d'être redevinés côté web : l'écran ne doit proposer aucune commande qui
    échouerait, et il ne peut le savoir qu'en le lisant ici. Les recalculer en
    TypeScript donnerait deux vérités, et c'est l'interface qui aurait tort.
    """
    return CompositePriceOut(
        id=row.id,
        code=row.code,
        label=row.label,
        unit_code=row.unit_code,
        notes=row.notes,
        is_demo_data=row.is_demo_data,
        revision=row.revision,
        version_published=version.status == "published",
        referenced_by=_references(session, row.id),
        components=[spec_from_row(component) for component in row.components],
    )


# --------------------------------------------------------------------------
# Gérer un sous-détail : lire, modifier, dupliquer, supprimer, prévisualiser
# --------------------------------------------------------------------------


def _valider_les_specs(payload_components: list) -> list[dict[str, object]]:
    """Valide et CANONICALISE les spécifications, ou lève un 422 détaillé.

    Les problèmes sont rendus avec l'index du composant fautif : un écran qui
    ne peut pas dire QUEL composant est en cause oblige l'utilisateur à
    relire les vingt. `validate_spec` canonicalise les unités sur place, d'où
    les spécifications rendues plutôt que jetées.
    """
    specs: list[dict[str, object]] = [c.model_dump() for c in payload_components]
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
    return specs


def _unite_canonique(unit_code: str) -> str:
    try:
        return get_unit(unit_code).code
    except UnknownUnitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unknown_unit", "message": exc.message},
        ) from exc


def _sous_detail_ouvert(
    session: Session, context: TenantContext, composite_id: str
) -> tuple[CompositePriceRow, PriceBookVersion]:
    """Le sous-détail et sa version, verrouillés dans le bon ordre.

    La VERSION d'abord, le sous-détail ensuite — l'ordre déclaré par
    `LOCK_ORDER`. L'inverse croiserait celui de toute autre requête et
    changerait une course en interblocage, qui échoue même quand rien n'était
    réellement disputé.

    Verrouiller la version, et pas seulement la lire : sans cela une
    publication peut se glisser entre le contrôle du statut et l'écriture, et
    le sous-détail serait modifié dans une version que le produit présente
    comme figée.
    """
    composite = get_owned(
        session, CompositePriceRow, context.organization_id, composite_id, label="Sous-détail"
    )
    version = _version_open_for_writing(session, context, composite.price_book_version_id)
    return (
        lock_owned(
            session, CompositePriceRow, context.organization_id, composite_id, label="Sous-détail"
        ),
        version,
    )


@router.get(
    "/composites/{composite_id}",
    response_model=CompositePriceOut,
    summary="Détail d'un sous-détail de prix",
)
def get_composite(
    composite_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_READ)),
    session: Session = Depends(session_scope),
) -> CompositePriceOut:
    composite = get_owned(
        session, CompositePriceRow, context.organization_id, composite_id, label="Sous-détail"
    )
    version = get_owned(
        session,
        PriceBookVersion,
        context.organization_id,
        composite.price_book_version_id,
        label="Version",
    )
    return _composite_out(session, composite, version)


@router.put(
    "/composites/{composite_id}",
    response_model=CompositePriceOut,
    summary="Modifier un sous-détail (remplacement complet)",
)
def update_composite(
    composite_id: str,
    payload: CompositePriceUpdate,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> CompositePriceOut:
    """Remplace le sous-détail ENTIER, composants compris, en une transaction.

    Trois refus, et chacun protège d'une perte silencieuse :

    * `version_published` — une version publiée ne se modifie plus ;
    * `composite_stale` — la ligne a bougé depuis que l'appelant l'a lue ;
    * `duplicate_code` — le code appartient déjà à un autre sous-détail.

    Le remplacement est total plutôt que partiel : rapiécer une liste
    ORDONNÉE, avec des ajouts, des retraits et des déplacements, demande une
    sémantique de fusion que personne n'a écrite et qui se tromperait dès que
    deux éditeurs la sollicitent.
    """
    composite, version = _sous_detail_ouvert(session, context, composite_id)

    if composite.revision != payload.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "composite_stale",
                "message": (
                    "Ce sous-détail a été modifié depuis son chargement "
                    f"(révision {composite.revision}, vous avez {payload.revision}). "
                    "Rechargez-le : écrire maintenant effacerait la modification "
                    "de quelqu'un d'autre sans que personne ne l'apprenne."
                ),
                "current_revision": composite.revision,
            },
        )

    specs = _valider_les_specs(payload.components)
    unite = _unite_canonique(payload.unit_code)

    composite.code = payload.code
    composite.label = payload.label
    composite.unit_code = unite
    composite.notes = payload.notes
    composite.revision = composite.revision + 1

    # `delete-orphan` sur la relation efface les anciennes lignes au `flush`.
    composite.components.clear()
    session.flush()
    _ecrire_les_composants(session, composite, specs, context.organization_id)

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

    session.refresh(composite)
    audit.record(
        session,
        organization_id=context.organization_id,
        action="composite_price.updated",
        object_type="composite_price",
        object_id=composite.id,
        summary=f"Sous-détail {composite.code} modifié",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"components": len(specs), "revision": composite.revision},
    )
    return _composite_out(session, composite, version)


@router.post(
    "/composites/{composite_id}/duplicate",
    response_model=CompositePriceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Dupliquer un sous-détail",
)
def duplicate_composite(
    composite_id: str,
    payload: CompositeDuplicate,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> CompositePriceOut:
    """Copie un sous-détail dans SA version, sous un code neuf.

    Le code est demandé plutôt que dérivé : un suffixe automatique produit des
    « SD-TER-EXC-copie-2 » que personne ne relit, et deux duplications
    successives donnent un nom qui ne dit plus rien. La copie ne porte jamais
    `is_demo_data` — ce qu'un utilisateur duplique devient sa donnée.
    """
    source = get_owned(
        session, CompositePriceRow, context.organization_id, composite_id, label="Sous-détail"
    )
    version = _version_open_for_writing(session, context, source.price_book_version_id)

    copie = CompositePriceRow(
        organization_id=context.organization_id,
        price_book_version_id=source.price_book_version_id,
        code=payload.code,
        label=payload.label or source.label,
        unit_code=source.unit_code,
        notes=source.notes,
    )
    session.add(copie)
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

    _ecrire_les_composants(
        session, copie, [spec_from_row(c) for c in source.components], context.organization_id
    )
    session.flush()
    session.refresh(copie)
    audit.record(
        session,
        organization_id=context.organization_id,
        action="composite_price.duplicated",
        object_type="composite_price",
        object_id=copie.id,
        summary=f"Sous-détail {copie.code} dupliqué depuis {source.code}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"source_id": source.id, "components": len(source.components)},
    )
    return _composite_out(session, copie, version)


@router.delete(
    "/composites/{composite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un sous-détail",
)
def delete_composite(
    composite_id: str,
    context: TenantContext = Depends(require(Permission.PRICEBOOK_WRITE)),
    session: Session = Depends(session_scope),
) -> None:
    """Supprime — sur version brouillon, et seulement si personne ne s'en sert.

    Un sous-détail référencé par un poste ne se supprime pas : `SET NULL`
    laisserait le poste sans prix sans le dire, et un devis brouillon
    deviendrait incalculable au prochain recalcul, loin du geste qui l'a causé.
    Le refus nomme le nombre de postes concernés pour que la personne sache
    quoi faire.
    """
    composite, _ = _sous_detail_ouvert(session, context, composite_id)

    utilisations = _references(session, composite.id)
    if utilisations:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "composite_referenced",
                "message": (
                    f"{utilisations} poste(s) de bordereau utilisent ce sous-détail. "
                    "Changez leur source de prix avant de le supprimer."
                ),
                "referenced_by": utilisations,
            },
        )

    code = composite.code
    session.delete(composite)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="composite_price.deleted",
        object_type="composite_price",
        object_id=composite_id,
        summary=f"Sous-détail {code} supprimé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )


@router.post(
    "/versions/{version_id}/composites/preview",
    response_model=CompositePreviewOut,
    summary="Prévisualiser le coût unitaire d'un sous-détail, sans rien écrire",
)
def preview_composite(
    version_id: str,
    payload: CompositePreviewIn,
    context: TenantContext = Depends(require(Permission.COST_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> CompositePreviewOut:
    """Le déboursé sec d'une unité, calculé par le MOTEUR, sans écriture.

    `COST_READ` et non `PRICEBOOK_READ` : cette réponse porte des montants de
    ressources et leur ventilation par nature — c'est un coût interne, et la
    matrice des permissions le traite comme tel partout ailleurs.

    Aucune écriture : c'est ce qui permet à l'écran de montrer le chiffre
    pendant la saisie, avant que quoi que ce soit ne soit enregistré, sans
    jamais recopier l'arithmétique du moteur en TypeScript.
    """
    get_owned(session, PriceBookVersion, context.organization_id, version_id, label="Version")
    specs = _valider_les_specs(payload.components)
    unite = _unite_canonique(payload.unit_code)
    try:
        rendu = apercu(
            specs,
            unit_code=unite,
            currency=settings.default_currency,
            arrondi=rounding_from_settings(context.organization.settings),
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message, "context": exc.context},
        ) from exc
    return CompositePreviewOut(**rendu)
