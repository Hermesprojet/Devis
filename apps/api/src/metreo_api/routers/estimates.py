"""Estimates, versions, computation, freezing and exports."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError
from metreo_domain.estimate import EstimateResult

from ..db import session_scope
from ..models import (
    BillOfQuantities,
    BoqItem,
    Estimate,
    EstimateVersion,
    OrganizationSettings,
    PriceBookVersion,
    PriceItem,
    Project,
)
from ..schemas import (
    ComputationOut,
    EstimateCreate,
    EstimateOut,
    EstimateVersionCreate,
    EstimateVersionOut,
    FreezeRequest,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit, estimating, exports
from ..services.estimating import FreezeRefused, PricingInputError
from ..services.tenant import get_owned, owned_query

router = APIRouter(tags=["estimates"])


def _load(session: Session, context: TenantContext, estimate_id: str) -> Estimate:
    return get_owned(session, Estimate, context.organization_id, estimate_id, label="Estimation")


def _version(
    session: Session, context: TenantContext, estimate: Estimate, version_id: str
) -> EstimateVersion:
    version = get_owned(
        session, EstimateVersion, context.organization_id, version_id, label="Version"
    )
    if version.estimate_id != estimate.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Version introuvable."},
        )
    return version


def _positions(session: Session, estimate: Estimate) -> dict[str, str]:
    return {
        item.id: item.position
        for item in session.scalars(
            select(BoqItem).where(
                BoqItem.organization_id == estimate.organization_id,
                BoqItem.boq_id == estimate.boq_id,
            )
        ).all()
    }


def _uses_demo_prices(session: Session, estimate: Estimate, version: EstimateVersion) -> bool:
    return bool(
        session.scalars(
            select(PriceItem.id).where(
                PriceItem.organization_id == estimate.organization_id,
                PriceItem.price_book_version_id == version.price_book_version_id,
                PriceItem.is_demo_data.is_(True),
            )
        ).first()
    )


@router.post(
    "/estimates",
    response_model=EstimateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une estimation",
)
def create_estimate(
    payload: EstimateCreate,
    context: TenantContext = Depends(require(Permission.ESTIMATE_WRITE)),
    session: Session = Depends(session_scope),
) -> Estimate:
    project = get_owned(
        session, Project, context.organization_id, payload.project_id, label="Projet"
    )
    boq = get_owned(
        session, BillOfQuantities, context.organization_id, payload.boq_id, label="Bordereau"
    )
    if boq.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "boq_project_mismatch",
                "message": "Ce bordereau n'appartient pas à ce projet.",
            },
        )
    price_version = get_owned(
        session,
        PriceBookVersion,
        context.organization_id,
        payload.price_book_version_id,
        label="Version de bibliothèque",
    )

    estimate = Estimate(
        organization_id=context.organization_id,
        project_id=project.id,
        boq_id=boq.id,
        price_book_version_id=price_version.id,
        name=payload.name,
        currency=project.currency,
        created_by=context.user.id,
    )
    session.add(estimate)
    session.flush()

    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    version = EstimateVersion(
        organization_id=context.organization_id,
        estimate_id=estimate.id,
        version_number=1,
        label="Version initiale",
        status="draft",
        price_book_version_id=price_version.id,
        markup=estimating.markup_to_dict(estimating.markup_from_settings(settings)),
        taxes=estimating.taxes_to_list(estimating.active_taxes(session, context.organization_id)),
        rounding=estimating.rounding_to_dict(estimating.rounding_from_settings(settings)),
        missing_price_policy=settings.missing_price_policy,
        created_by=context.user.id,
    )
    session.add(version)
    session.flush()

    audit.record(
        session,
        organization_id=context.organization_id,
        action="estimate.created",
        object_type="estimate",
        object_id=estimate.id,
        summary=f"Estimation « {estimate.name} » créée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"project_id": project.id, "boq_id": boq.id},
    )
    return estimate


@router.get("/estimates", response_model=list[EstimateOut], summary="Lister les estimations")
def list_estimates(
    project_id: str | None = None,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> list[Estimate]:
    query = owned_query(Estimate, context.organization_id)
    if project_id:
        query = query.where(Estimate.project_id == project_id)
    return list(session.scalars(query.order_by(Estimate.created_at.desc())).all())


@router.get(
    "/estimates/{estimate_id}", response_model=EstimateOut, summary="Détail d'une estimation"
)
def get_estimate(
    estimate_id: str,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> Estimate:
    return _load(session, context, estimate_id)


@router.get(
    "/estimates/{estimate_id}/versions",
    response_model=list[EstimateVersionOut],
    summary="Versions d'une estimation",
)
def list_versions(
    estimate_id: str,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> list[EstimateVersion]:
    estimate = _load(session, context, estimate_id)
    return list(
        session.scalars(
            select(EstimateVersion)
            .where(
                EstimateVersion.organization_id == context.organization_id,
                EstimateVersion.estimate_id == estimate.id,
            )
            .order_by(EstimateVersion.version_number.desc())
        ).all()
    )


@router.post(
    "/estimates/{estimate_id}/versions",
    response_model=EstimateVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une nouvelle version (brouillon)",
)
def create_version(
    estimate_id: str,
    payload: EstimateVersionCreate,
    context: TenantContext = Depends(require(Permission.ESTIMATE_WRITE)),
    session: Session = Depends(session_scope),
) -> EstimateVersion:
    estimate = _load(session, context, estimate_id)
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    price_book_version_id = payload.price_book_version_id or estimate.price_book_version_id
    get_owned(
        session,
        PriceBookVersion,
        context.organization_id,
        price_book_version_id,
        label="Version de bibliothèque",
    )
    version = EstimateVersion(
        organization_id=context.organization_id,
        estimate_id=estimate.id,
        version_number=estimating.next_version_number(session, estimate.id),
        label=payload.label,
        status="draft",
        price_book_version_id=price_book_version_id,
        markup=estimating.markup_to_dict(estimating.markup_from_settings(settings)),
        taxes=estimating.taxes_to_list(estimating.active_taxes(session, context.organization_id)),
        rounding=estimating.rounding_to_dict(estimating.rounding_from_settings(settings)),
        missing_price_policy=settings.missing_price_policy,
        created_by=context.user.id,
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="estimate_version.created",
        object_type="estimate_version",
        object_id=version.id,
        summary=f"Version {version.version_number} créée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return version


def _computed(
    session: Session, *, estimate: Estimate, version: EstimateVersion
) -> tuple[EstimateResult, dict[str, object]]:
    """Calcule une version, ou lève une erreur métier lisible.

    Les quatre routes qui calculent — aperçu, gel, CSV, HTML — traitaient leurs
    erreurs de trois façons différentes, et les deux exports pas du tout : une
    ligne incalculable y produisait un 500. Un seul chemin, une seule réponse.
    """
    try:
        return estimating.compute_version(session, estimate=estimate, version=version)
    except PricingInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unpriceable_lines",
                "message": "Certaines lignes ne peuvent pas être calculées en l'état.",
                "problems": exc.problems,
            },
        ) from exc
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.to_dict()
        ) from exc


@router.get(
    "/estimates/{estimate_id}/versions/{version_id}/computation",
    response_model=ComputationOut,
    summary="Calculer (ou relire) une version",
)
def compute(
    estimate_id: str,
    version_id: str,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> ComputationOut:
    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)
    result, _ = _computed(session, estimate=estimate, version=version)

    rounding = estimating.rounding_from_dict(version.rounding) if version.rounding else None
    if rounding is None:
        settings = session.get(OrganizationSettings, context.organization_id)
        assert settings is not None
        rounding = estimating.rounding_from_settings(settings)

    include_internal = context.can(Permission.COST_READ)
    payload = estimating.totals_for_display(result, rounding, include_internal=include_internal)
    return ComputationOut(
        version=EstimateVersionOut.model_validate(version),
        computed_at=datetime.now(UTC),
        from_snapshot=version.status == "frozen",
        includes_internal_costs=include_internal,
        result=payload,
    )


@router.post(
    "/estimates/{estimate_id}/versions/{version_id}/freeze",
    response_model=EstimateVersionOut,
    summary="Geler une version (irréversible)",
)
def freeze(
    estimate_id: str,
    version_id: str,
    payload: FreezeRequest,
    context: TenantContext = Depends(require(Permission.ESTIMATE_FREEZE)),
    session: Session = Depends(session_scope),
) -> EstimateVersion:
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "confirmation_required",
                "message": "Le gel est irréversible: confirmez explicitement.",
            },
        )
    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)
    try:
        frozen, result = estimating.freeze_version(
            session,
            estimate=estimate,
            version=version,
            actor_user_id=context.user.id,
            label=payload.label,
        )
    except FreezeRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.reason, **exc.details},
        ) from exc
    except PricingInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unpriceable_lines",
                "message": "Certaines lignes ne peuvent pas être calculées en l'état.",
                "problems": exc.problems,
            },
        ) from exc
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.to_dict()
        ) from exc

    audit.record(
        session,
        organization_id=context.organization_id,
        action="estimate_version.frozen",
        object_type="estimate_version",
        object_id=frozen.id,
        summary=(
            f"Version {frozen.version_number} gelée — "
            f"{result.total_selling_price_ht.rounded().amount} {estimate.currency} HT"
        ),
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "snapshot_sha256": frozen.snapshot_sha256,
            "price_book_version_id": frozen.price_book_version_id,
            "total_selling_price_ht": str(frozen.total_selling_price_ht),
        },
    )
    return frozen


@router.get(
    "/estimates/{estimate_id}/versions/{version_id}/export.csv",
    summary="Exporter le bordereau chiffré en CSV",
    response_class=Response,
)
def export_csv(
    estimate_id: str,
    version_id: str,
    include_internal: bool = Query(default=False, description="Inclure les coûts internes"),
    context: TenantContext = Depends(require(Permission.EXPORT_CLIENT)),
    session: Session = Depends(session_scope),
) -> Response:
    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)
    project = get_owned(
        session, Project, context.organization_id, estimate.project_id, label="Projet"
    )

    if include_internal:
        context.require(Permission.EXPORT_INTERNAL)

    # Avant tout effet de bord : un export refusé ne doit rien journaliser.
    result, _ = _computed(session, estimate=estimate, version=version)
    rounding = estimating.rounding_from_dict(version.rounding)
    content = exports.estimate_to_csv(
        result=result,
        rounding=rounding,
        positions=_positions(session, estimate),
        include_internal=include_internal,
        header={
            "Référence projet": project.reference,
            "Nom du projet": project.name,
            "Estimation": estimate.name,
            "Version": str(version.version_number),
            "Statut": version.status,
            "Devise": estimate.currency,
            "Généré le (UTC)": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
            "Empreinte de version": version.snapshot_sha256 or "—",
        },
    )
    audit.record(
        session,
        organization_id=context.organization_id,
        action="estimate_version.exported",
        object_type="estimate_version",
        object_id=version.id,
        summary=f"Export CSV version {version.version_number}"
        + (" (avec coûts internes)" if include_internal else ""),
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"format": "csv", "include_internal": include_internal},
    )
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": exports.content_disposition(
                f"{project.reference}-v{version.version_number}", "csv"
            )
        },
    )


@router.get(
    "/estimates/{estimate_id}/versions/{version_id}/quote.html",
    summary="Aperçu imprimable du devis",
    response_class=Response,
)
def quote_preview(
    estimate_id: str,
    version_id: str,
    context: TenantContext = Depends(require(Permission.EXPORT_CLIENT)),
    session: Session = Depends(session_scope),
) -> Response:
    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)
    project = get_owned(
        session, Project, context.organization_id, estimate.project_id, label="Projet"
    )
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None

    result, _ = _computed(session, estimate=estimate, version=version)
    include_internal = settings.show_internal_costs_in_client_pdf and context.can(
        Permission.COST_READ
    )
    html = exports.quote_html(
        organization=context.organization,
        project=project,
        estimate=estimate,
        version=version,
        result=result,
        rounding=estimating.rounding_from_dict(version.rounding),
        positions=_positions(session, estimate),
        include_internal=include_internal,
        demo_data_warning=_uses_demo_prices(session, estimate, version),
    )
    audit.record(
        session,
        organization_id=context.organization_id,
        action="estimate_version.exported",
        object_type="estimate_version",
        object_id=version.id,
        summary=f"Aperçu devis version {version.version_number}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"format": "html"},
    )
    return Response(content=html, media_type="text/html; charset=utf-8")
