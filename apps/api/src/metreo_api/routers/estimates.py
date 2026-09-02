"""Estimates, versions, computation, freezing and exports."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError
from metreo_domain.estimate import EstimateResult

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import (
    BillOfQuantities,
    BoqItem,
    Estimate,
    EstimateVersion,
    IssuedQuote,
    OrganizationSettings,
    PriceBookVersion,
    PriceItem,
    Project,
    User,
)
from ..schemas import (
    ComputationOut,
    EstimateCreate,
    EstimateOut,
    EstimateVersionCreate,
    EstimateVersionOut,
    FreezeRequest,
    IssuedQuoteOut,
    QuoteIssueRequest,
    ScenariosIn,
    ScenariosOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import audit, estimating, exports, issuance, scenarios
from ..services.document_storage import ContenuRefuse, StockageLocal
from ..services.estimating import FreezeRefused, PricingInputError
from ..services.issuance import EmissionRefusee
from ..services.tenant import get_owned, owned_query
from ..transactions import RouteTransactionnelle

router = APIRouter(tags=["estimates"], route_class=RouteTransactionnelle)


def _load(session: Session, context: TenantContext, estimate_id: str) -> Estimate:
    return get_owned(session, Estimate, context.organization_id, estimate_id, label="Estimation")


def _version(
    session: Session,
    context: TenantContext,
    estimate: Estimate,
    version_id: str,
    *,
    lock: bool = False,
) -> EstimateVersion:
    """Load a version of this estimate, optionally holding it for the write.

    ``lock=True`` is for the routes that read the status, decide, then write —
    the freeze. Without it two callers both read ``draft``, both freeze, and
    two audit events are written for one irreversible act.
    """
    if lock:
        version = estimating.lock_version(
            session, organization_id=context.organization_id, version_id=version_id
        )
    else:
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
        # Verrouille l'estimation avant de compter : voir services/locking.py.
        version_number=estimating.next_version_number(
            session, estimate.id, organization_id=context.organization_id
        ),
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
    payload = estimating.totals_for_display(
        result,
        rounding,
        include_costs=include_internal,
        # `margin:read` gouverne les ÉTAPES de markup, séparément des coûts :
        # un métreur porte `cost:read` sans `margin:read`, et recevait donc
        # jusqu'ici le taux de marge de l'entreprise.
        include_margin=context.can(Permission.MARGIN_READ),
    )
    return ComputationOut(
        version=EstimateVersionOut.model_validate(version),
        computed_at=datetime.now(UTC),
        from_snapshot=version.status == "frozen",
        includes_internal_costs=include_internal,
        result=payload,
    )


@router.post(
    "/estimates/{estimate_id}/versions/{version_id}/scenarios",
    response_model=ScenariosOut,
    summary="Chiffrer trois scénarios (aucune écriture)",
)
def simuler_des_scenarios(
    estimate_id: str,
    version_id: str,
    payload: ScenariosIn,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> ScenariosOut:
    """Compare trois hypothèses de chiffrage, sans rien enregistrer.

    **`POST` pour un calcul, et non `GET`.** Les hypothèses forment un corps
    structuré — trois jeux de quatre champs, dont une liste de catégories — que
    la chaîne de requête rendrait illisible et bornerait mal. Le verbe ne dit
    donc rien d'une écriture : la route est inscrite au registre transactionnel
    comme LECTURE, et le contrôle de démarrage l'y tient.

    **`cost:read` en plus de `estimate:read`.** Comparer des scénarios, c'est
    lire des déboursés : la question posée est celle du coût, pas celle du prix
    remis au client. Sans ce droit, la fonction n'aurait aucun objet.

    **Rien n'est écrit.** Pas de version, pas de bordereau, pas de
    bibliothèque, pas d'audit, pas de devis, pas de fichier. Une simulation qui
    laisserait une trace cesserait d'être une simulation.
    """
    # `cost:read` en second, dans le corps : `require` n'impose qu'une
    # permission, et le refus doit NOMMER celle qui manque. Vérifié avant toute
    # lecture de l'estimation, pour que l'absence de droit ne se distingue pas
    # d'une estimation inexistante par le temps de réponse.
    context.require(Permission.COST_READ)

    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)

    try:
        hypotheses = {
            nom: scenarios.hypotheses_depuis(getattr(payload, nom).model_dump())
            for nom in scenarios.SCENARIOS
        }
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.to_dict()
        ) from exc

    # Le calcul de RÉFÉRENCE peut échouer avant toute hypothèse — un bordereau
    # dont une ligne n'est pas chiffrable. Le même chemin d'erreur que les
    # quatre autres routes qui calculent, plutôt qu'un cinquième.
    _computed(session, estimate=estimate, version=version)

    rendu = scenarios.simuler(
        session,
        estimate=estimate,
        version=version,
        hypotheses_par_scenario=hypotheses,
        # Deux décisions distinctes, prises par le MÊME filtre que la route de
        # calcul : une logique de masquage propre aux scénarios divergerait de
        # l'autre au premier champ ajouté, et c'est exactement ainsi qu'un
        # taux finit par sortir d'un côté et pas de l'autre.
        inclure_couts=context.can(Permission.COST_READ),
        inclure_marge=context.can(Permission.MARGIN_READ),
    )
    return ScenariosOut(
        version=EstimateVersionOut.model_validate(version),
        computed_at=datetime.now(UTC),
        **rendu,
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
    version = _version(session, context, estimate, version_id, lock=True)
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


# ---------------------------------------------------------------------------
# Le devis remis au client
# ---------------------------------------------------------------------------


def _stockage(settings: Settings) -> StockageLocal:
    return StockageLocal(settings.storage_root)


def _refus_emission(exc: EmissionRefusee) -> HTTPException:
    """Deux familles de refus, deux codes, et la même frontière partout.

    409 quand c'est l'ÉTAT qui s'y oppose — version non gelée, chantier sans
    fiche, fiche trop maigre, version déjà émise : le corps envoyé était
    correct, c'est le monde qui n'est pas prêt, et l'écran doit inviter à agir
    ailleurs. 422 quand c'est la DEMANDE qui est fautive — une validité déjà
    passée : le corps se corrige sur place.

    Le `code` reste ce que l'écran lit pour choisir son message ; le statut
    HTTP dit seulement où se trouve la correction à faire.
    """
    codes = {
        "version_not_frozen": status.HTTP_409_CONFLICT,
        "already_issued": status.HTTP_409_CONFLICT,
        "client_required": status.HTTP_409_CONFLICT,
        "client_incomplete": status.HTTP_409_CONFLICT,
        # L'état des RÉGLAGES, pas celui du corps envoyé : la correction se
        # fait sur l'écran des paramètres, pas dans cette requête.
        "quote_number_pattern_invalid": status.HTTP_409_CONFLICT,
        "validity_in_the_past": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        status_code=codes.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
        detail=exc.to_dict(),
    )


@router.post(
    "/estimates/{estimate_id}/versions/{version_id}/issue",
    response_model=IssuedQuoteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Émettre le devis d'une version gelée",
)
def issue_quote(
    estimate_id: str,
    version_id: str,
    payload: QuoteIssueRequest,
    context: TenantContext = Depends(require(Permission.ESTIMATE_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> IssuedQuoteOut:
    """Numérote, fige, imprime et range — ou ne fait rien du tout.

    Les coûts internes ne s'invitent pas : le réglage de l'organisation donne le
    défaut, l'émetteur peut le contredire, et l'inclure exige `export:internal`.
    La décision est écrite dans l'instantané, et c'est elle qui vaut ensuite —
    pas le réglage tel qu'il sera demain.
    """
    estimate = _load(session, context, estimate_id)
    version = _version(session, context, estimate, version_id)
    project = get_owned(
        session, Project, context.organization_id, estimate.project_id, label="Projet"
    )
    # `context.organization` est déjà la ligne de CETTE organisation, chargée
    # par l'authentification. `get_owned` ne convient pas ici : il filtre sur
    # une colonne `organization_id` que la table `organizations` n'a pas — elle
    # EST le tenant.
    organization = context.organization
    reglages = session.get(OrganizationSettings, context.organization_id)

    defaut = bool(getattr(reglages, "show_internal_costs_in_client_pdf", False))
    include_internal = (
        defaut if payload.include_internal_costs is None else payload.include_internal_costs
    )
    if include_internal:
        context.require(Permission.EXPORT_INTERNAL)

    result, _ = _computed(session, estimate=estimate, version=version)
    rounding = estimating.rounding_from_dict(version.rounding)
    lignes = exports.line_rows(result, rounding, _positions(session, estimate))
    totaux = issuance.totaux_du_document(result.to_dict(rounding), estimate.currency)

    try:
        devis = issuance.emettre(
            session,
            context=context,
            estimate=estimate,
            version=version,
            project=project,
            organization=organization,
            reglages=reglages,
            lignes=lignes,
            totaux=totaux,
            stockage=_stockage(settings),
            valid_until=payload.valid_until,
            terms=payload.terms,
            include_internal_costs=include_internal,
        )
    except EmissionRefusee as exc:
        raise _refus_emission(exc) from exc
    return _devis_rendu(session, devis, version.version_number)


def _devis_rendu(session: Session, devis: IssuedQuote, version_number: int) -> IssuedQuoteOut:
    emetteur = session.get(User, devis.issued_by) if devis.issued_by else None
    return IssuedQuoteOut(
        id=devis.id,
        number=devis.number,
        project_id=devis.project_id,
        estimate_id=devis.estimate_id,
        estimate_version_id=devis.estimate_version_id,
        client_id=devis.client_id,
        client_name=str(devis.client_snapshot.get("name", "")),
        issued_at=devis.issued_at,
        valid_until=devis.valid_until,
        terms=devis.terms,
        include_internal_costs=devis.include_internal_costs,
        pdf_sha256=devis.pdf_sha256,
        pdf_byte_size=devis.pdf_byte_size,
        version_number=version_number,
        issued_by_email=emetteur.email if emetteur else None,
    )


@router.get(
    "/projects/{project_id}/issued-quotes",
    response_model=list[IssuedQuoteOut],
    summary="Devis émis pour un chantier",
)
def list_issued_quotes(
    project_id: str,
    context: TenantContext = Depends(require(Permission.ESTIMATE_READ)),
    session: Session = Depends(session_scope),
) -> list[IssuedQuoteOut]:
    get_owned(session, Project, context.organization_id, project_id, label="Projet")
    devis = session.scalars(
        owned_query(IssuedQuote, context.organization_id)
        .where(IssuedQuote.project_id == project_id)
        .order_by(IssuedQuote.issued_at.desc())
    ).all()
    numeros = {
        version.id: version.version_number
        for version in session.scalars(
            select(EstimateVersion).where(
                EstimateVersion.organization_id == context.organization_id,
                EstimateVersion.id.in_([d.estimate_version_id for d in devis] or [""]),
            )
        ).all()
    }
    return [_devis_rendu(session, d, numeros.get(d.estimate_version_id, 0)) for d in devis]


@router.get(
    "/issued-quotes/{quote_id}/document.pdf",
    summary="Télécharger le PDF d'un devis émis",
    response_class=Response,
)
def download_issued_quote(
    quote_id: str,
    context: TenantContext = Depends(require(Permission.EXPORT_CLIENT)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Rend les octets écrits à l'émission, jamais un document reconstruit.

    C'est ce qui rend deux téléchargements identiques, et c'est aussi ce qui
    rend le contenu indépendant de qui télécharge : les coûts internes y sont —
    ou n'y sont pas — selon la décision prise à l'émission, pas selon les
    permissions du lecteur.
    """
    devis = get_owned(session, IssuedQuote, context.organization_id, quote_id, label="Devis émis")
    stockage = _stockage(settings)
    try:
        octets = stockage.chemin(devis.pdf_storage_key).read_bytes()
    except (OSError, ContenuRefuse) as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "document_missing",
                "message": "Le fichier de ce devis est introuvable sur le volume.",
            },
        ) from exc

    issuance.enregistrer_le_telechargement(session, context=context, devis=devis)
    return Response(
        content=octets,
        media_type="application/pdf",
        headers={
            "Content-Disposition": exports.content_disposition(f"devis-{devis.number}", "pdf"),
            "Content-Length": str(len(octets)),
            # Un devis est un document commercial confidentiel : il ne doit
            # être ni deviné par le navigateur, ni conservé par un cache
            # partagé, ni rendu dans l'origine de l'application.
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store, private",
            "X-Quote-Sha256": devis.pdf_sha256,
        },
    )
