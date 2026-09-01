"""Company profile, calculation rules and tax rates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import EstimateVersion, Membership, OrganizationSettings, TaxRateRow, User
from ..schemas import (
    CHAMPS_COMMERCIAUX_SENSIBLES,
    LogoOut,
    MemberInvite,
    MemberOut,
    MemberUpdate,
    OrganizationOut,
    OrganizationProfileUpdate,
    OrganizationSettingsOut,
    OrganizationSettingsUpdate,
    QuoteNumberPreviewOut,
    TaxRateCreate,
    TaxRateOut,
    TaxRateUpdate,
)
from ..security.auth import TenantContext, current_context, require
from ..security.roles import Permission, Role
from ..services import audit, images, numerotation, profil_entreprise
from ..services.document_storage import StockageLocal
from ..services.estimating import markup_from_settings
from ..services.images import ImageRefusee
from ..transactions import RouteTransactionnelle

router = APIRouter(prefix="/organization", tags=["organization"], route_class=RouteTransactionnelle)


def _profil(organization: Any) -> OrganizationOut:
    """L'organisation telle que l'API la rend, logo et manques compris.

    `missing_for_issue` est calculé ici plutôt que par l'écran : la règle qui
    décide si un devis peut partir doit avoir UN seul endroit, et c'est
    `profil_entreprise.emetteur_suffisant` — le même que l'émission consulte.
    Deux listes, l'une à l'écran et l'autre au serveur, divergeraient au
    premier champ ajouté, et l'écran promettrait une émission que le serveur
    refuserait.
    """
    sortie = OrganizationOut.model_validate(organization)
    sortie.logo = (
        LogoOut(
            sha256=organization.logo_sha256,
            byte_size=organization.logo_byte_size,
            media_type=organization.logo_media_type,
            width=organization.logo_width,
            height=organization.logo_height,
            updated_at=organization.logo_updated_at,
        )
        if profil_entreprise.logo_present(organization)
        else None
    )
    sortie.missing_for_issue = profil_entreprise.emetteur_suffisant(organization)
    return sortie


@router.get("", response_model=OrganizationOut, summary="Organisation courante")
def get_organization(context: TenantContext = Depends(current_context)) -> OrganizationOut:
    return _profil(context.organization)


@router.patch(
    "",
    response_model=OrganizationOut,
    summary="Modifier le profil de l'entreprise",
)
def update_organization_profile(
    payload: OrganizationProfileUpdate,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> OrganizationOut:
    """L'adresse, les coordonnées et l'identité qui s'impriment sur un devis.

    Une chaîne vide EFFACE le champ facultatif qu'elle vise : retirer un site
    web qu'on n'a plus doit se faire depuis l'écran, pas par la base. Le nom
    fait exception — Pydantic lui impose une longueur minimale — parce qu'une
    organisation sans nom ne s'imprime nulle part.

    Ce que cette route ne fait PAS : toucher un devis déjà émis. Ils portent
    leur instantané, et c'est tout le sujet de `issuance`.
    """
    organization = session.get(type(context.organization), context.organization_id)
    assert organization is not None
    changements = payload.model_dump(exclude_unset=True)
    avant = {cle: getattr(organization, cle) for cle in changements}

    for cle, valeur in changements.items():
        # `None` et chaîne vide sont volontairement traités pareil : l'écran
        # envoie l'un ou l'autre selon qu'il a vidé le champ ou ne l'a jamais
        # rempli, et la différence ne veut rien dire pour une adresse.
        propre = (valeur or "").strip() if isinstance(valeur, str) or valeur is None else valeur
        setattr(organization, cle, propre or None)
    # `country_code` n'est pas nullable : le vider le remettrait à NULL et la
    # base refuserait. On garde donc la valeur d'avant plutôt que de laisser
    # partir une erreur d'intégrité que personne ne saurait lire.
    if not organization.country_code:
        organization.country_code = avant.get("country_code") or "BE"
    if not organization.name:
        organization.name = avant.get("name") or organization.name
    session.flush()

    audit.record(
        session,
        organization_id=context.organization_id,
        action="organization.profile.updated",
        object_type="organization",
        object_id=context.organization_id,
        summary="Profil de l'entreprise modifié",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "before": {
                cle: str(valeur) if valeur is not None else None for cle, valeur in avant.items()
            },
            "after": {
                cle: str(getattr(organization, cle))
                if getattr(organization, cle) is not None
                else None
                for cle in changements
            },
        },
    )
    return _profil(organization)


@router.put(
    "/logo",
    response_model=OrganizationOut,
    summary="Charger ou remplacer le logo de l'entreprise",
)
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> OrganizationOut:
    """Le logo qui s'imprimera en tête des devis.

    Rien de ce que le navigateur annonce n'est cru : ni le nom du fichier, ni
    son extension, ni son type MIME. Le contenu est décodé, et c'est lui qui
    décide — un SVG renommé `.png`, un PDF, un exécutable ou un PNG entrelacé
    sont refusés en le disant, avant qu'un seul octet n'atteigne le volume.

    Le plafond est lu deux fois : l'en-tête `Content-Length` évite d'absorber
    un fichier énorme pour le rejeter après, et la taille RÉELLE tranche —
    l'en-tête est une allégation comme une autre.
    """
    annoncee = request.headers.get("content-length")
    if annoncee and annoncee.isdigit() and int(annoncee) > images_plafond_enveloppe():
        raise _refus_logo(
            ImageRefusee(
                "fichier_trop_volumineux",
                f"Ce fichier dépasse le maximum de {images.OCTETS_MAXIMUM // (1024 * 1024)} Mio.",
            )
        )

    # Lu en une fois, et borné : `verifier_un_logo` refuse au-delà du plafond,
    # et l'en-tête ci-dessus a déjà écarté l'envoi manifestement démesuré.
    contenu = await file.read(images.OCTETS_MAXIMUM + 1)
    organization = session.get(type(context.organization), context.organization_id)
    assert organization is not None
    stockage = StockageLocal(settings.storage_root)
    try:
        image = profil_entreprise.poser_le_logo(
            session, organization=organization, contenu=contenu, stockage=stockage
        )
    except ImageRefusee as refus:
        raise _refus_logo(refus) from refus

    audit.record(
        session,
        organization_id=context.organization_id,
        action="organization.logo.updated",
        object_type="organization",
        object_id=context.organization_id,
        summary="Logo de l'entreprise chargé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "sha256": organization.logo_sha256,
            "byte_size": organization.logo_byte_size,
            "width": image.largeur,
            "height": image.hauteur,
        },
    )
    return _profil(organization)


@router.delete("/logo", response_model=OrganizationOut, summary="Retirer le logo")
def delete_logo(
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> OrganizationOut:
    """Retire le logo courant. Les devis déjà émis gardent le leur.

    Retirer un logo n'est pas une erreur quand il n'y en a pas : la route rend
    le profil dans les deux cas. Un 404 obligerait l'écran à distinguer deux
    situations qui, pour qui clique, sont la même — « je n'en veux plus ».
    """
    organization = session.get(type(context.organization), context.organization_id)
    assert organization is not None
    stockage = StockageLocal(settings.storage_root)
    retire = profil_entreprise.retirer_le_logo(
        session, organization=organization, stockage=stockage
    )
    if retire:
        audit.record(
            session,
            organization_id=context.organization_id,
            action="organization.logo.removed",
            object_type="organization",
            object_id=context.organization_id,
            summary="Logo de l'entreprise retiré",
            actor_user_id=context.user.id,
            actor_email=context.user.email,
        )
    return _profil(organization)


@router.get("/logo", summary="Le logo de l'entreprise", response_class=StreamingResponse)
def download_logo(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Sert les octets du logo de SON organisation, et d'aucune autre.

    Il n'y a pas d'identifiant dans le chemin, et c'est délibéré : la seule
    organisation atteignable est celle de la session. Aucune valeur venue de
    la requête n'entre dans le chemin lu — il vient de la base, où seul ce
    module l'a écrit.

    Servi en `inline` : c'est une image d'interface, affichée dans l'écran des
    réglages, pas une pièce à télécharger. `nosniff` reste, et le type est
    celui qui a été ÉTABLI sur les octets à la réception.
    """
    organization = context.organization
    if not profil_entreprise.logo_present(organization):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "no_logo", "message": "Cette entreprise n'a pas de logo."},
        )
    stockage = StockageLocal(settings.storage_root)
    cle = organization.logo_storage_key
    assert cle is not None
    if stockage.taille(cle) is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "content_missing",
                "message": "Le logo est absent du stockage.",
            },
        )
    return StreamingResponse(
        stockage.lire(cle),
        media_type=organization.logo_media_type or "application/octet-stream",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            # L'empreinte fait l'étiquette : un logo remplacé change d'empreinte,
            # donc d'étiquette, et le navigateur recharge sans qu'on ait à lui
            # interdire tout cache.
            "ETag": f'"{organization.logo_sha256}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


def images_plafond_enveloppe() -> int:
    """Le plafond appliqué à l'enveloppe multipart, marge comprise.

    L'en-tête `Content-Length` mesure l'enveloppe — bornes, en-têtes de partie
    — et non le fichier. Le comparer tel quel au plafond refuserait un fichier
    de la taille exacte du plafond. La marge est celle des dépôts de pièces,
    pour la même raison.
    """
    return images.OCTETS_MAXIMUM + 8 * 1024


def _refus_logo(refus: ImageRefusee) -> HTTPException:
    """Un refus d'image en réponse HTTP, jamais en 500.

    Un fichier refusé est une erreur de l'appelant : il doit la lire et
    recommencer avec un autre fichier. Le code machine vient du service et ne
    change pas ; le message est destiné à un humain.
    """
    statut = (
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        if refus.code == "fichier_trop_volumineux"
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    return HTTPException(
        status_code=statut,
        detail={"code": refus.code, "message": refus.message, "context": refus.context},
    )


@router.get(
    "/settings",
    response_model=OrganizationSettingsOut,
    summary="Règles de calcul de l'entreprise",
)
def get_settings_endpoint(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
) -> OrganizationSettingsOut:
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    payload = _avec_apercu(settings)
    if not context.can(Permission.MARGIN_READ):
        # Coefficients that reveal commercial policy are masked rather than
        # refused: the rest of the screen stays usable. They become null, never
        # zero, so a client cannot mistake a mask for a real rate.
        # La même liste que le journal d'audit consulte, et pour la même
        # raison : deux listes tenues séparément, c'est exactement ce qui a
        # laissé `/audit/events` rendre en clair ce que cet écran masque.
        payload = payload.model_copy(
            update={
                "commercial_rates_visible": False,
                **dict.fromkeys(CHAMPS_COMMERCIAUX_SENSIBLES, None),
            }
        )
    return payload


@dataclass(slots=True)
class _MergedSettings:
    """Vue en lecture de l'état final, sans toucher à l'objet persistant.

    Muter puis valider laisserait, en cas de refus, une session portant des
    valeurs invalides : un `flush()` déclenché ailleurs les écrirait.
    """

    site_overheads_rate: Any
    site_overheads_base: Any
    general_overheads_rate: Any
    general_overheads_base: Any
    contingency_rate: Any
    contingency_base: Any
    margin_rate: Any
    margin_method: Any


def _merged(settings: OrganizationSettings, changes: dict[str, Any]) -> Any:
    fields = (
        "site_overheads_rate",
        "site_overheads_base",
        "general_overheads_rate",
        "general_overheads_base",
        "contingency_rate",
        "contingency_base",
        "margin_rate",
        "margin_method",
    )
    return _MergedSettings(**{name: changes.get(name, getattr(settings, name)) for name in fields})


@router.patch(
    "/settings",
    response_model=OrganizationSettingsOut,
    summary="Modifier les règles de calcul",
)
def update_settings(
    payload: OrganizationSettingsUpdate,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> OrganizationSettingsOut:
    settings = session.get(OrganizationSettings, context.organization_id)
    assert settings is not None
    changes = payload.model_dump(exclude_unset=True)
    before = {key: str(getattr(settings, key)) for key in changes}

    # Valider l'état FINAL fusionné, avant d'écrire quoi que ce soit. Une
    # modification partielle suffit à rendre la configuration incalculable :
    # passer la méthode à `on_price` alors que le taux stocké vaut 1,5 — ou
    # l'inverse — produit une division par un nombre négatif ou nul, et TOUTES
    # les estimations de l'entreprise deviennent incalculables d'un coup. La
    # construction de MarkupPolicy est le seul juge : elle porte déjà la règle.
    # Le motif de numérotation se contrôle ICI, au moment où quelqu'un le
    # saisit — et non à l'émission, où il serait trop tard pour le corriger
    # sans perdre le geste en cours.
    if "quote_number_pattern" in changes:
        try:
            changes["quote_number_pattern"] = numerotation.verifier(changes["quote_number_pattern"])
        except numerotation.MotifInvalide as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": exc.message, "context": exc.context},
            ) from exc

    try:
        markup_from_settings(_merged(settings, changes))
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": exc.code,
                "message": exc.message,
                "context": exc.context,
            },
        ) from exc

    for key, value in changes.items():
        setattr(settings, key, value)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="organization.settings.updated",
        object_type="organization_settings",
        object_id=context.organization_id,
        summary="Règles de calcul modifiées",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"before": before, "after": {k: str(v) for k, v in changes.items()}},
    )
    return _avec_apercu(settings)


@router.get(
    "/quote-number-preview",
    response_model=QuoteNumberPreviewOut,
    summary="Prévisualiser un motif de numérotation",
)
def preview_quote_number(
    pattern: str = Query(default="", max_length=60, description="Motif à essayer"),
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
) -> QuoteNumberPreviewOut:
    """Ce que ce motif produirait, sans rien enregistrer.

    Le rendu appartient au serveur, ici comme à l'émission. Le recopier dans
    l'interface donnerait deux vérités, et l'aperçu finirait par annoncer un
    format que l'API n'applique pas — exactement le genre d'écart que le repli
    silencieux d'hier laissait passer.
    """
    del context  # la permission suffit : rien de cette organisation n'est lu
    try:
        numerotation.verifier(pattern)
    except numerotation.MotifInvalide as refus:
        return QuoteNumberPreviewOut(valid=False, preview=None, message=refus.message)
    return QuoteNumberPreviewOut(valid=True, preview=numerotation.apercu(pattern), message=None)


def _avec_apercu(settings: OrganizationSettings) -> OrganizationSettingsOut:
    """Les réglages, plus le numéro que le motif produirait.

    Calculé côté serveur : recopier la règle de rendu dans l'interface
    donnerait deux vérités, et l'aperçu finirait par annoncer un format
    que l'API n'applique pas.
    """
    return OrganizationSettingsOut.model_validate(settings).model_copy(
        update={"quote_number_preview": numerotation.apercu(settings.quote_number_pattern)}
    )


def _taux_possede(session: Session, organization_id: str, tax_rate_id: str) -> TaxRateRow:
    """Le taux, ou 404 — jamais celui d'une autre organisation.

    404 et non 403 : répondre « interdit » confirmerait l'existence de
    l'identifiant chez quelqu'un d'autre.
    """
    taux = session.scalars(
        select(TaxRateRow).where(
            TaxRateRow.id == tax_rate_id,
            TaxRateRow.organization_id == organization_id,
        )
    ).one_or_none()
    if taux is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Taux de taxe introuvable."},
        )
    return taux


def _fenetres_se_chevauchent(
    debut_a: date | None, fin_a: date | None, debut_b: date | None, fin_b: date | None
) -> bool:
    """Deux périodes d'application se recouvrent-elles ?

    Une borne absente est une borne ouverte : « depuis toujours » ou « sans
    fin ».
    """
    a_finit_avant_b = fin_a is not None and debut_b is not None and fin_a < debut_b
    b_finit_avant_a = fin_b is not None and debut_a is not None and fin_b < debut_a
    return not (a_finit_avant_b or b_finit_avant_a)


def _refuser_doublon_ambigu(
    session: Session,
    organization_id: str,
    *,
    code: str,
    applies_from: date | None,
    applies_to: date | None,
    is_default: bool,
    sauf_id: str | None = None,
) -> None:
    """Refuse deux taux du même code applicables en même temps.

    `active_taxes` retient TOUS les taux par défaut en vigueur : deux lignes
    « TVA-21 » se chevauchant seraient appliquées toutes les deux, et le devis
    porterait la taxe en double sans que rien ne le signale.

    Deux codes DIFFÉRENTS qui se chevauchent restent permis — c'est le cas
    normal d'une entreprise qui facture deux taux.
    """
    if not is_default:
        return
    for autre in session.scalars(
        select(TaxRateRow).where(
            TaxRateRow.organization_id == organization_id,
            TaxRateRow.code == code,
            TaxRateRow.is_default.is_(True),
        )
    ).all():
        if sauf_id is not None and autre.id == sauf_id:
            continue
        if _fenetres_se_chevauchent(applies_from, applies_to, autre.applies_from, autre.applies_to):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "tax_rate_overlap",
                    "message": (
                        f"Un taux « {code} » s'applique déjà sur cette période. "
                        "Deux taux de même code applicables en même temps "
                        "seraient tous les deux appliqués au devis."
                    ),
                },
            )


def _codes_de_taxe_geles(session: Session, organization_id: str) -> set[str]:
    """Les codes de taxe qu'un devis gelé porte déjà dans son instantané.

    Lus dans les instantanés eux-mêmes, et non déduits des taux courants : un
    devis gelé garde le taux qu'il a appliqué, même si la configuration a
    changé depuis. C'est précisément cette trace qu'une suppression
    effacerait.
    """
    codes: set[str] = set()
    for version in session.scalars(
        select(EstimateVersion).where(
            EstimateVersion.organization_id == organization_id,
            EstimateVersion.status == "frozen",
        )
    ).all():
        instantane = version.snapshot or {}
        resultat = instantane.get("result") if isinstance(instantane, dict) else None
        if not isinstance(resultat, dict):
            continue
        for taxe in resultat.get("taxes") or []:
            if isinstance(taxe, dict) and taxe.get("code"):
                codes.add(str(taxe["code"]))
    return codes


@router.post(
    "/tax-rates",
    response_model=TaxRateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un taux de taxe",
)
def create_tax_rate(
    payload: TaxRateCreate,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> TaxRateRow:
    """Metreo n'installe aucun taux et n'en devine aucun.

    Le taux applicable, sa date d'effet et sa base légale sont une décision de
    l'entreprise. Les inscrire à sa place lui ferait porter une affirmation
    fiscale qu'elle n'a pas prise — et un « TVA 21 % » préinstallé serait faux
    pour tout travail relevant d'un taux réduit.
    """
    if payload.applies_from and payload.applies_to and payload.applies_to < payload.applies_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_period",
                "message": "La fin d'application précède son début.",
            },
        )
    _refuser_doublon_ambigu(
        session,
        context.organization_id,
        code=payload.code,
        applies_from=payload.applies_from,
        applies_to=payload.applies_to,
        is_default=payload.is_default,
    )
    taux = TaxRateRow(organization_id=context.organization_id, **payload.model_dump())
    session.add(taux)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="tax_rate.created",
        object_type="tax_rate",
        object_id=taux.id,
        summary=f"Taux « {taux.code} » créé à {taux.rate}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return taux


@router.patch(
    "/tax-rates/{tax_rate_id}",
    response_model=TaxRateOut,
    summary="Modifier un taux de taxe",
)
def update_tax_rate(
    tax_rate_id: str,
    payload: TaxRateUpdate,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> TaxRateRow:
    """Le `code` n'est pas modifiable : un instantané gelé le porte déjà.

    Pour retirer un taux du service, on borne son application dans le temps
    (`applies_to`) plutôt que de le supprimer : les devis déjà gelés gardent
    ainsi la trace exacte de ce qui leur a été appliqué.
    """
    taux = _taux_possede(session, context.organization_id, tax_rate_id)
    changements = payload.model_dump(exclude_unset=True)
    if not changements:
        return taux

    avant = {cle: str(getattr(taux, cle)) for cle in changements}
    debut = changements.get("applies_from", taux.applies_from)
    fin = changements.get("applies_to", taux.applies_to)
    if debut and fin and fin < debut:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_period",
                "message": "La fin d'application précède son début.",
            },
        )
    _refuser_doublon_ambigu(
        session,
        context.organization_id,
        code=taux.code,
        applies_from=debut,
        applies_to=fin,
        is_default=changements.get("is_default", taux.is_default),
        sauf_id=taux.id,
    )

    for cle, valeur in changements.items():
        setattr(taux, cle, valeur)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="tax_rate.updated",
        object_type="tax_rate",
        object_id=taux.id,
        summary=f"Taux « {taux.code} » modifié : {', '.join(sorted(changements))}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"avant": avant, "apres": {c: str(getattr(taux, c)) for c in changements}},
    )
    return taux


@router.delete(
    "/tax-rates/{tax_rate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un taux jamais appliqué",
)
def delete_tax_rate(
    tax_rate_id: str,
    context: TenantContext = Depends(require(Permission.ORG_MANAGE)),
    session: Session = Depends(session_scope),
) -> None:
    """Ne supprime QUE ce qu'aucun devis gelé n'a appliqué.

    Corriger une faute de frappe faite il y a deux minutes doit rester
    possible. Effacer un taux qu'un devis remis au client porte déjà ne l'est
    pas : la ligne disparue rendrait l'instantané illisible pour qui
    chercherait à quoi « TVA-21 » correspondait.

    Pour retirer du service un taux déjà employé, on le borne dans le temps.
    """
    taux = _taux_possede(session, context.organization_id, tax_rate_id)
    if taux.code in _codes_de_taxe_geles(session, context.organization_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "tax_rate_in_use",
                "message": (
                    f"Le taux « {taux.code} » figure sur au moins un devis gelé. "
                    "Bornez son application dans le temps plutôt que de le "
                    "supprimer : l'historique doit rester lisible."
                ),
            },
        )
    code = taux.code
    session.delete(taux)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="tax_rate.deleted",
        object_type="tax_rate",
        object_id=tax_rate_id,
        summary=f"Taux « {code} » supprimé (jamais appliqué à un devis gelé)",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )


def _membre(appartenance: Membership, utilisateur: User) -> MemberOut:
    return MemberOut(
        id=appartenance.id,
        user_id=utilisateur.id,
        email=utilisateur.email,
        full_name=utilisateur.full_name,
        role=appartenance.role,
        role_label=Role(appartenance.role).label_fr,
        is_active=appartenance.is_active,
    )


def _appartenances(session: Session, organization_id: str) -> list[tuple[Membership, User]]:
    return [
        (appartenance, utilisateur)
        for appartenance, utilisateur in session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == organization_id)
            .order_by(User.email)
        ).all()
    ]


def _refuser_derniere_administration(
    session: Session,
    organization_id: str,
    *,
    appartenance: Membership,
    role_apres: str,
    actif_apres: bool,
) -> None:
    """Refuse le geste qui laisserait l'organisation sans administrateur.

    Sans ce refus, un administrateur pouvait se rétrograder ou se désactiver
    et personne — pas même lui — ne pouvait plus rouvrir les réglages : la
    seule issue aurait été une intervention en base, exactement ce que ce
    travail cherche à supprimer.
    """
    etait_administrateur_actif = (
        appartenance.role == Role.ORG_ADMIN.value and appartenance.is_active
    )
    reste_administrateur_actif = role_apres == Role.ORG_ADMIN.value and actif_apres
    if not etait_administrateur_actif or reste_administrateur_actif:
        return
    restants = [
        autre
        for autre, _ in _appartenances(session, organization_id)
        if autre.id != appartenance.id and autre.role == Role.ORG_ADMIN.value and autre.is_active
    ]
    if restants:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "last_administrator",
            "message": (
                "C'est le dernier administrateur actif de l'organisation. "
                "Nommez d'abord quelqu'un d'autre administrateur."
            ),
        },
    )


@router.get(
    "/members",
    response_model=list[MemberOut],
    summary="Collaborateurs de l'organisation",
)
def list_members(
    context: TenantContext = Depends(require(Permission.USER_MANAGE)),
    session: Session = Depends(session_scope),
) -> list[MemberOut]:
    return [_membre(a, u) for a, u in _appartenances(session, context.organization_id)]


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un collaborateur",
)
def invite_member(
    payload: MemberInvite,
    context: TenantContext = Depends(require(Permission.USER_MANAGE)),
    session: Session = Depends(session_scope),
) -> MemberOut:
    """Ouvre le droit d'entrer, et rien d'autre.

    Aucun mot de passe n'est créé et aucun message n'est envoyé : la personne
    se connecte par le fournisseur d'identité, et la liaison se fait à sa
    première connexion sur son adresse vérifiée — même principe que le
    bootstrap du premier administrateur.

    Sans cette route, une organisation neuve restait à une seule personne :
    aucun écran ne permettait d'ajouter un métreur ou un lecteur, et il
    fallait écrire en base pour composer une équipe.
    """
    courriel = payload.email.strip().lower()
    utilisateur = session.scalars(select(User).where(User.email == courriel)).one_or_none()
    if utilisateur is None:
        utilisateur = User(
            email=courriel,
            full_name=payload.full_name.strip(),
            locale=context.organization.locale,
            is_active=True,
        )
        session.add(utilisateur)
        session.flush()

    existante = session.scalars(
        select(Membership).where(
            Membership.user_id == utilisateur.id,
            Membership.organization_id == context.organization_id,
        )
    ).one_or_none()
    if existante is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_member",
                "message": f"« {courriel} » fait déjà partie de cette organisation.",
            },
        )

    appartenance = Membership(
        user_id=utilisateur.id,
        organization_id=context.organization_id,
        role=payload.role.value,
        is_active=True,
    )
    session.add(appartenance)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="member.invited",
        object_type="membership",
        object_id=appartenance.id,
        summary=f"{courriel} ajouté comme {payload.role.label_fr}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return _membre(appartenance, utilisateur)


@router.patch(
    "/members/{membership_id}",
    response_model=MemberOut,
    summary="Changer le rôle d'un collaborateur ou lui retirer l'accès",
)
def update_member(
    membership_id: str,
    payload: MemberUpdate,
    context: TenantContext = Depends(require(Permission.USER_MANAGE)),
    session: Session = Depends(session_scope),
) -> MemberOut:
    """L'accès se retire, il ne se supprime pas.

    Les événements d'audit désignent l'auteur de chaque geste : effacer la
    personne rendrait illisible l'historique qu'elle a écrit.
    """
    trouve = next(
        (
            (a, u)
            for a, u in _appartenances(session, context.organization_id)
            if a.id == membership_id
        ),
        None,
    )
    if trouve is None:
        # 404 et non 403 : dire « interdit » confirmerait l'existence de cet
        # identifiant chez quelqu'un d'autre.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Collaborateur introuvable."},
        )
    appartenance, utilisateur = trouve
    changements = payload.model_dump(exclude_unset=True)
    if not changements:
        return _membre(appartenance, utilisateur)

    nouveau_role = changements.get("role")
    role_apres = nouveau_role.value if nouveau_role is not None else appartenance.role
    actif_apres = changements.get("is_active", appartenance.is_active)
    _refuser_derniere_administration(
        session,
        context.organization_id,
        appartenance=appartenance,
        role_apres=role_apres,
        actif_apres=actif_apres,
    )

    avant = {"role": appartenance.role, "is_active": appartenance.is_active}
    appartenance.role = role_apres
    appartenance.is_active = actif_apres
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="member.updated",
        object_type="membership",
        object_id=appartenance.id,
        summary=f"Accès de {utilisateur.email} modifié",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"avant": avant, "apres": {"role": role_apres, "is_active": actif_apres}},
    )
    return _membre(appartenance, utilisateur)


@router.get("/tax-rates", response_model=list[TaxRateOut], summary="Taux de taxe")
def list_tax_rates(
    context: TenantContext = Depends(current_context),
    session: Session = Depends(session_scope),
) -> list[TaxRateRow]:
    return list(
        session.scalars(
            select(TaxRateRow)
            .where(TaxRateRow.organization_id == context.organization_id)
            .order_by(TaxRateRow.code)
        ).all()
    )
