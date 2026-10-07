"""Document metadata and append-only human validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from metreo_domain.errors import UnknownUnitError

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Document, DocumentRevision, PlanCalibration, ValidationDecision
from ..schemas import (
    AnomalieDePlan,
    CadreDePlan,
    CalibrationCreate,
    CalibrationOut,
    DocumentCreate,
    DocumentOut,
    DocumentRevisionOut,
    DocumentStatusUpdate,
    FragmentDeTexte,
    MesureCreate,
    MesureDePdf,
    MesureDePlan,
    MesuresDePdf,
    PlanLu,
    PointDEcran,
    TextesDePlan,
    ValidationDecisionCreate,
    ValidationDecisionOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import (
    calibration_de_plan,
    documents,
    exports,
    lecture_de_plan,
    mesures_de_plan,
    mesures_pdf,
    rendu_de_plan,
    rendu_pdf,
    tuiles,
)
from ..services.document_storage import (
    TAILLE_MORCEAU,
    ContenuRefuse,
    StockageLocal,
    TropVolumineux,
    nom_original_sur,
)
from ..services.travail_documentaire import TravailRefuse, executer_etape_dans
from ..transactions import RouteTransactionnelle

router = APIRouter(tags=["documents"], route_class=RouteTransactionnelle)


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentOut],
    summary="Lister les documents d'un projet",
)
def list_project_documents(
    project_id: str,
    include_archived: bool = Query(default=False, description="Inclure les documents archivés"),
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> list[Document]:
    return documents.list_documents(
        session,
        organization_id=context.organization_id,
        project_id=project_id,
        include_archived=include_archived,
    )


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un document logique",
)
def create_project_document(
    project_id: str,
    payload: DocumentCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
) -> Document:
    return documents.create_document(
        session,
        context=context,
        project_id=project_id,
        title=payload.title,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentOut,
    summary="Lire les métadonnées d'un document",
)
def get_document(
    document_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> Document:
    return documents.get_document(
        session,
        organization_id=context.organization_id,
        document_id=document_id,
    )


@router.get(
    "/documents/{document_id}/revisions",
    response_model=list[DocumentRevisionOut],
    summary="Lister les révisions d'un document",
)
def list_document_revisions(
    document_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> list[DocumentRevision]:
    return documents.list_revisions(
        session,
        organization_id=context.organization_id,
        document_id=document_id,
    )


#: De quoi couvrir bornes et en-têtes de parties multipart, sans jamais
#: refuser un fichier qui tient sous le plafond.
MARGE_ENVELOPPE_MULTIPART = 64 * 1024


def _refus_http(erreur: Exception) -> HTTPException:
    """Traduire un refus métier en réponse HTTP, sans jamais inventer un 500.

    Un contenu refusé est une erreur de l'appelant : il doit la lire et
    corriger son dépôt. Le code machine vient du domaine et ne change pas ;
    seul le message est destiné à un humain.
    """
    code = getattr(erreur, "code", "invalid_upload")
    message = getattr(erreur, "message", str(erreur))
    statut = {
        "file_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "duplicate_content": status.HTTP_409_CONFLICT,
        "document_archived": status.HTTP_409_CONFLICT,
        "storage_collision": status.HTTP_409_CONFLICT,
        "not_found": status.HTTP_404_NOT_FOUND,
    }.get(code, status.HTTP_422_UNPROCESSABLE_ENTITY)
    return HTTPException(status_code=statut, detail={"code": code, "message": message})


def _morceaux(fichier: UploadFile):
    """Le flux, morceau par morceau.

    Starlette a déjà déversé la partie multipart dans son propre fichier
    tampon — en mémoire jusqu'à 1 Mio, sur disque au-delà — avant que cette
    fonction ne soit appelée : le processus ne détient donc jamais les 25 Mio
    d'un seul tenant. Ce que cette lecture par morceaux garantit en plus, c'est
    que la RECOPIE vers le volume ne les reconstitue pas non plus, et que le
    plafond est constaté pendant la copie et non après.
    """
    while morceau := fichier.file.read(TAILLE_MORCEAU):
        yield morceau


@router.post(
    "/documents/{document_id}/revisions",
    response_model=DocumentRevisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Joindre un fichier en nouvelle révision immuable",
)
async def upload_document_revision(
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> DocumentRevision:
    """Le premier fichier fait la révision 1 ; le suivant en fait une de plus.

    Rien n'est jamais remplacé : une révision publiée est immuable, et la
    précédente reste téléchargeable. Le nom, l'extension et le type annoncés
    par le client sont des indications ; c'est la signature des octets reçus
    qui décide, et c'est elle qui est conservée.
    """
    # Un pré-filtre grossier, et rien de plus : inutile d'absorber 500 Mio pour
    # les rejeter ensuite. `Content-Length` mesure l'ENVELOPPE multipart —
    # bornes, en-têtes de partie — et non le fichier ; la comparer telle quelle
    # au plafond refusait un fichier de 512 octets sous un plafond de 512.
    # Mesuré. On laisse donc passer une marge d'enveloppe largement suffisante,
    # et c'est la copie, seule à connaître la taille réelle, qui tranche.
    annoncee = request.headers.get("content-length")
    if (
        annoncee
        and annoncee.isdigit()
        and int(annoncee) > settings.max_upload_bytes + MARGE_ENVELOPPE_MULTIPART
    ):
        raise _refus_http(TropVolumineux(settings.max_upload_bytes))

    stockage = StockageLocal(settings.storage_root)
    try:
        return documents.add_revision(
            session,
            context=context,
            document_id=document_id,
            stockage=stockage,
            morceaux=_morceaux(file),
            filename=nom_original_sur(file.filename),
            declared_media_type=file.content_type,
            plafond=settings.max_upload_bytes,
        )
    except (ContenuRefuse, TropVolumineux, documents.RevisionRefusee) as erreur:
        raise _refus_http(erreur) from erreur


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/content",
    summary="Télécharger l'original d'une révision",
    response_class=StreamingResponse,
)
def download_document_revision(
    document_id: str,
    revision_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Rend l'original tel qu'il a été reçu, en pièce jointe et jamais à l'écran.

    `attachment` et `nosniff` ensemble : un PDF ou un HTML déguisé rendu dans
    l'origine de l'application y exécuterait ses propres scripts et lirait le
    jeton de session. Le fichier est donc remis au système d'exploitation de
    l'utilisateur, jamais interprété par la page.

    Le chemin relu est celui que le serveur a écrit, repris en base ; aucune
    partie n'en vient de la requête.
    """
    try:
        revision = documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    stockage = StockageLocal(settings.storage_root)
    taille = stockage.taille(revision.storage_key)
    if taille is None:
        # Le fichier a disparu du volume alors que la base le référence. C'est
        # une panne d'exploitation, pas une erreur de l'appelant : on le dit,
        # plutôt que de rendre zéro octet en prétendant que c'est le document.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "content_missing",
                "message": "L'original de cette révision est absent du stockage.",
            },
        )

    documents.record_download(session, context=context, revision=revision)

    # Le corps est produit APRÈS la fermeture de la session par `session_scope`,
    # qui aura donc déjà validé la transaction — d'où la lecture de tout ce dont
    # la réponse a besoin AVANT de rendre le générateur, qui ne touche plus
    # qu'au disque.
    nom, _, extension = revision.original_filename.rpartition(".")
    return StreamingResponse(
        stockage.lire(revision.storage_key),
        media_type=revision.media_type,
        headers={
            "Content-Disposition": exports.content_disposition(
                nom or revision.original_filename, extension or "bin"
            ),
            "Content-Length": str(taille),
            # Posé ici en plus du proxy : en développement et sur le banc de
            # recette, le navigateur parle DIRECTEMENT à l'API, sans Caddy pour
            # ajouter l'en-tête. Une protection qui dépend du déploiement n'en
            # est pas une.
            "X-Content-Type-Options": "nosniff",
            "X-Document-Sha256": revision.sha256,
        },
    )


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentOut,
    summary="Archiver ou réactiver un document",
)
def update_document_status(
    document_id: str,
    payload: DocumentStatusUpdate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
) -> Document:
    try:
        return documents.set_document_status(
            session,
            context=context,
            document_id=document_id,
            status=payload.status,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur


@router.post(
    "/extraction-proposals/{proposal_id}/decisions",
    response_model=ValidationDecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer une décision humaine",
)
def create_validation_decision(
    proposal_id: str,
    payload: ValidationDecisionCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_VALIDATE)),
    session: Session = Depends(session_scope),
) -> ValidationDecision:
    """Enregistre une décision, et refuse une correction qui n'en est pas une.

    Le 422 porte un code stable et une phrase que l'écran affiche telle quelle :
    « 3,8 m environ » n'est pas une quantité, et le dire au moment de la saisie
    vaut mieux que le découvrir le jour où cette valeur alimentera un métré.
    """
    try:
        return documents.record_validation_decision(
            session,
            context=context,
            proposal_id=proposal_id,
            payload=payload,
        )
    except calibration_de_plan.ValeurRetenueRefusee as refus:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": refus.code, "message": refus.message},
        ) from refus


# ---------------------------------------------------------------------------
# La lecture d'un plan
# ---------------------------------------------------------------------------
#
# Trois routes, et une seule qui travaille. L'analyse est SYNCHRONE, ce qui
# n'est pas un choix d'architecture mais l'état des lieux : aucune file
# d'attente n'existe dans le dépôt, et en inventer une est une autre tranche.
# Mesuré sur deux plans d'exécution réels, l'analyse complète prend 7,3 s et
# 8,8 s. C'est long pour une requête, c'est pourquoi la taille du fichier est
# plafonnée ici et pas dans le worker : `scripts/lire_un_plan.py` lit le même
# plan sans cette limite, parce que personne n'attend sa réponse.


def _plan_lu(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    stockage: StockageLocal,
) -> PlanLu:
    """Assemble la réponse depuis l'artefact de constat et les propositions."""
    constat = lecture_de_plan.lire_le_constat(
        stockage, organization_id=organization_id, revision_id=revision_id
    )
    if constat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "plan_non_analyse",
                "message": "Ce plan n'a pas encore été analysé.",
            },
        )
    contenu = constat.contenu
    motif = contenu.get("motif_du_refus")
    mesures = mesures_de_plan.lister(
        session, organization_id=organization_id, revision_id=revision_id
    )
    format_lu = str(contenu.get("format") or "dxf")
    return PlanLu(
        revision_id=revision_id,
        format="pdf" if format_lu == "pdf" else "dxf",
        pages=int(contenu.get("pages") or 0),
        dimensions_des_pages=[
            [float(cote) for cote in paire]
            for paire in (contenu.get("dimensions_des_pages") or [])
            if isinstance(paire, list) and len(paire) == 2
        ],
        porte_du_texte=bool(contenu.get("porte_du_texte")),
        fragments_lus=int(contenu.get("fragments_lus") or 0),
        apercus=[page for page in (contenu.get("apercus") or []) if isinstance(page, int)],
        mesurable=bool(contenu.get("mesurable")),
        unite_source=contenu.get("unite_source"),
        insunits=contenu.get("insunits"),
        version_dxf=contenu.get("version_dxf"),
        feuilles=list(contenu.get("feuilles") or []),
        calques=dict(contenu.get("calques") or {}),
        entites=dict(contenu.get("entites") or {}),
        refuse=bool(contenu.get("refuse")),
        motif_du_refus=(
            AnomalieDePlan(code=str(motif.get("code")), message=str(motif.get("message")))
            if isinstance(motif, dict)
            else None
        ),
        anomalies=[
            AnomalieDePlan(code=str(a.get("code", "")), message=str(a.get("message", "")))
            for a in (contenu.get("anomalies") or [])
            if isinstance(a, dict)
        ],
        # Pour un PDF, « image disponible » veut dire « la page 1 a un
        # aperçu » : c'est ce que l'écran affiche d'abord, et c'est la seule
        # page dont l'absence empêche d'afficher quoi que ce soit.
        image_disponible=(
            bool(contenu.get("apercus"))
            if format_lu == "pdf"
            else lecture_de_plan.image_disponible(
                stockage, organization_id=organization_id, revision_id=revision_id
            )
        ),
        mesures=[
            MesureDePlan(
                proposal_id=m.proposal_id,
                citation_id=m.citation_id,
                valeur_document=m.valeur_document,
                unite_document=m.unite_document,
                famille=m.famille,
                fiabilite=m.fiabilite,
                origine_de_la_mesure=m.origine_de_la_mesure,
                texte_impose=m.texte_impose,
                reserves=[AnomalieDePlan(code=r["code"], message=r["message"]) for r in m.reserves],
                confiance=m.confiance,
                calque=m.calque,
                feuille=m.feuille,
                object_ref=m.object_ref,
                cadre=(
                    CadreDePlan(x0=m.cadre[0], y0=m.cadre[1], x1=m.cadre[2], y1=m.cadre[3])
                    if m.cadre
                    else None
                ),
                decision=m.decision,
                valeur_corrigee=m.valeur_corrigee,
            )
            for m in mesures
        ],
    )


@router.post(
    "/documents/{document_id}/revisions/{revision_id}/plan/analyse",
    response_model=PlanLu,
    summary="Lire un plan : son constat, son image et ses mesures proposées",
)
def analyse_plan_revision(
    document_id: str,
    revision_id: str,
    relancer: bool = Query(
        default=False,
        description="Reprendre une analyse en échec, sans changer sa clé d'idempotence",
    ),
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> PlanLu:
    """Lance la lecture déterministe, et rend le constat qu'elle produit.

    Deux étapes sont jouées, dans cet ordre et pour cette raison : `cad_read`
    lit le fichier et propose les mesures, `page_render` produit l'image. Si
    la seconde échoue, la PREMIÈRE reste acquise — des mesures justes ne
    doivent pas disparaître parce qu'un dessin n'a pas pu être tracé. L'état
    de chacune est consigné séparément dans `document_step_runs`.

    Aucun modèle de langage n'intervient : tout est déterministe, et la
    réponse ne contient que ce que le fichier porte.
    """
    try:
        revision = documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    try:
        lecture_de_plan.verifier_que_cest_un_plan(revision)
    except lecture_de_plan.PlanNonLisible as refus:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": refus.code, "message": refus.message},
        ) from refus

    if revision.byte_size > settings.plan_sync_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "plan_trop_lourd",
                "message": (
                    "Ce plan dépasse "
                    f"{settings.plan_sync_max_bytes // (1024 * 1024)} Mio, la limite de "
                    "l'analyse immédiate. Il reste lisible hors ligne, par le "
                    "traitement déporté."
                ),
            },
        )

    stockage = StockageLocal(settings.storage_root)
    # Les étapes dépendent du format, et leur ORDRE aussi : `lecture_de_plan`
    # porte les deux, parce que la raison de cet ordre est une propriété du
    # format et pas une préférence de ce routeur.
    for etape, travail in lecture_de_plan.etapes_pour(revision, stockage):
        try:
            executer_etape_dans(
                session,
                organization_id=context.organization_id,
                revision_id=revision_id,
                step=etape,
                pipeline_version=mesures_de_plan.PIPELINE_VERSION,
                prompt_version=mesures_de_plan.PROMPT_VERSION,
                model_version=mesures_de_plan.MODEL_VERSION,
                stockage=stockage,
                travail=travail,
                relancer=relancer,
            )
        except TravailRefuse as refus:
            if refus.code == "etape_deja_reussie":
                # Rejouer une étape acquise n'est pas une erreur de l'appelant
                # qu'il faudrait corriger : c'est un double clic. On passe à
                # la suivante, et la réponse rendra l'état réel.
                continue
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": refus.code, "message": refus.message},
            ) from refus

    return _plan_lu(
        session,
        organization_id=context.organization_id,
        revision_id=revision_id,
        stockage=stockage,
    )


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/plan",
    response_model=PlanLu,
    summary="Relire le constat d'un plan et ses mesures proposées",
)
def get_plan_revision(
    document_id: str,
    revision_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> PlanLu:
    """Ne relit PAS le fichier : relit l'artefact que l'analyse a posé.

    Rouvrir le DXF coûterait plusieurs secondes à chaque affichage d'écran, et
    rendrait un constat qui pourrait différer de celui sur lequel les mesures
    ont été proposées. L'artefact est la réponse, et l'original immuable est
    ce qui permet de le refaire.
    """
    try:
        documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    return _plan_lu(
        session,
        organization_id=context.organization_id,
        revision_id=revision_id,
        stockage=StockageLocal(settings.storage_root),
    )


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/plan/image",
    summary="Servir l'image d'un plan, en SVG",
    response_class=StreamingResponse,
)
def get_plan_image(
    document_id: str,
    revision_id: str,
    page: int = Query(
        default=1,
        ge=1,
        description="La page à servir. Ignorée pour un DXF, qui n'en a qu'une.",
    ),
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Rend le SVG du plan, sous une politique de refus total.

    **Un SVG est un document, pas une image.** Chargé par une balise `<img>`
    il est inerte : aucun script, aucun gestionnaire d'événement, aucune
    requête sortante. Atteint directement par son URL, dans l'origine de
    l'application, il exécuterait ce qu'il porte — et le jeton de session vit
    dans cette origine. D'où les en-têtes ci-dessous, qui ne servent QUE ce
    second cas.

    Trois protections, et aucune n'est redondante :

    1. le contenu lui-même est vérifié avant d'être posé sur le volume
       (`rendu_de_plan` refuse un rendu portant script, gestionnaire, image
       externe ou lien) — c'est la seule qui vaille aussi pour une URL
       `blob:`, qui ne porte aucun en-tête ;
    2. ces en-têtes, pour un accès direct à l'URL ;
    3. le client l'affiche par `<img>`, jamais en ligne dans la page.

    `style-src 'unsafe-inline'` est nécessaire et suffisant : le rendu ne
    produit qu'une feuille de style en ligne — vérifié à l'exécution sur deux
    plans réels, qui ne contiennent ni `<script`, ni `<image`, ni `xlink:href`,
    ni même une balise `<text>`.
    """
    try:
        revision = documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    stockage = StockageLocal(settings.storage_root)
    # Un PDF rend un PNG par page, un DXF un SVG unique. Le type servi est
    # celui de l'artefact, et les en-têtes de refus total s'appliquent aux
    # deux : un PNG n'exécute rien, mais le même chemin sert les deux et une
    # exception par format serait une exception à oublier.
    est_pdf = revision.media_type == "application/pdf"
    if est_pdf:
        cle = rendu_pdf.cle_de_l_apercu(context.organization_id, revision_id, page)
        type_servi = "image/png"
    else:
        cle = rendu_de_plan.cle_du_rendu(context.organization_id, revision_id)
        type_servi = "image/svg+xml"
    taille = stockage.taille(cle)
    if taille is None:
        # 409 et non 404 : la révision existe, elle est bien à cette
        # organisation, et c'est l'IMAGE qui manque. Un 404 se confondrait
        # avec le refus d'une révision d'un autre tenant, qui doit rester
        # indiscernable d'une révision inexistante.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "image_de_plan_absente",
                "message": (
                    f"L'aperçu de la page {page} de ce plan n'a pas encore été produit."
                    if est_pdf
                    else "L'image de ce plan n'a pas encore été produite."
                ),
            },
        )

    return StreamingResponse(
        stockage.lire(cle),
        media_type=type_servi,
        headers={
            "Content-Length": str(taille),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
        },
    )


#: Combien de fragments de texte une réponse rend au plus.
#:
#: Mesuré sur un plan d'exécution réel : 4 351 fragments sur une page. Les
#: rendre tous ferait une réponse de plusieurs mégaoctets pour afficher un
#: écran, et le navigateur en pose la plupart hors de la zone visible.
#:
#: Cinq cents suffisent à couvrir un cartouche et une série de cotes, et la
#: réponse DIT le total : un écran qui en montre 500 sur 4 351 doit pouvoir
#: l'écrire, sans quoi l'utilisateur croira avoir tout vu.
PLAFOND_FRAGMENTS_SERVIS = 500


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/plan/textes",
    response_model=TextesDePlan,
    summary="Les textes extraits d'un PDF, situés sur son aperçu",
)
def get_plan_textes(
    document_id: str,
    revision_id: str,
    page: int | None = Query(
        default=None,
        ge=1,
        description="Ne rendre que les textes de cette page. Toutes par défaut.",
    ),
    depuis: int = Query(
        default=0,
        ge=0,
        description="Sauter les N premiers fragments de la sélection.",
    ),
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> TextesDePlan:
    """Ne relit PAS le PDF : relit l'artefact que l'extraction a posé.

    Même raison que pour le constat — rouvrir le fichier coûterait jusqu'à une
    seconde par écran, et rendrait des fragments qui pourraient différer de
    ceux sur lesquels une mesure a été confirmée.

    **Ces fragments ne sont pas des mesures.** Ce sont des textes situés, tels
    que le dessinateur les a écrits. Rien ici n'affirme que « 5000 » vaut
    5 000 mm : le dire demande une échelle, et une échelle demande une
    confirmation humaine.
    """
    try:
        documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    stockage = StockageLocal(settings.storage_root)
    fragments = lecture_de_plan.lire_les_fragments(
        stockage, organization_id=context.organization_id, revision_id=revision_id
    )
    if fragments is None:
        # 404 avec un code nommé, comme `plan_non_analyse` : « jamais
        # extrait » est un état, et l'écran doit pouvoir proposer l'analyse.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "textes_non_extraits",
                "message": (
                    "Les textes de ce document n'ont pas été extraits. Un DXF "
                    "n'en porte pas : ses cotations sont des mesures, et elles "
                    "sont rendues par la route du plan."
                ),
            },
        )

    retenus = [f for f in fragments if page is None or f.get("page") == page]
    tranche = retenus[depuis : depuis + PLAFOND_FRAGMENTS_SERVIS]

    constat = lecture_de_plan.lire_le_constat(
        stockage, organization_id=context.organization_id, revision_id=revision_id
    )
    extracteur = ""
    if constat is not None:
        extracteur = str(constat.contenu.get("extracteur") or "")

    return TextesDePlan(
        revision_id=revision_id,
        # Le total de la SÉLECTION, pas celui de la tranche : c'est lui qui
        # permet d'écrire « 500 sur 4 351 » plutôt que de laisser croire que
        # tout est là.
        total=len(retenus),
        page=page,
        extracteur=extracteur,
        fragments=[
            FragmentDeTexte(
                texte=str(f.get("texte", "")),
                page=int(f.get("page") or 1),
                position=str(f.get("position") or "exacte"),
                # Un fragment sans position est rendu quand même : son texte
                # est une information, et c'est son emplacement qui manque.
                cadre=(
                    CadreDePlan(
                        x0=str(cadre[0]),
                        y0=str(cadre[1]),
                        x1=str(cadre[2]),
                        y1=str(cadre[3]),
                    )
                    if isinstance(cadre := f.get("cadre"), list) and len(cadre) == 4
                    else None
                ),
            )
            for f in tranche
        ],
    )


# ---------------------------------------------------------------------------
# Calibrer un PDF, puis le mesurer
# ---------------------------------------------------------------------------


def _calibration_out(calibration: PlanCalibration) -> CalibrationOut:
    zone = None
    if calibration.zone_x0 is not None:
        zone = [
            str(calibration.zone_x0),
            str(calibration.zone_y0),
            str(calibration.zone_x1),
            str(calibration.zone_y1),
        ]
    return CalibrationOut(
        id=calibration.id,
        page=calibration.page,
        distance_reelle=str(calibration.distance_reelle),
        unite=calibration.unite,
        facteur_lisible=calibration_de_plan.facteur_lisible(calibration),
        resolution_du_pointage=str(calibration.resolution_du_pointage),
        motif=calibration.motif,
        zone=zone,
        created_at=calibration.created_at,
    )


def _refus_de_calibration(erreur: Exception, code: str, message: str) -> HTTPException:
    """422 et un code nommé : la demande est recevable, le plan ne s'y prête pas.

    Pas 400 : la requête est bien formée. Pas 500 : rien n'a cassé. C'est le
    document — ou l'absence d'échelle — qui empêche de répondre, et l'écran
    doit pouvoir le dire à l'utilisateur dans ses mots.
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": code, "message": message},
    )


@router.post(
    "/documents/{document_id}/revisions/{revision_id}/plan/calibration",
    response_model=CalibrationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Déclarer l'échelle d'une page de PDF",
)
def post_calibration(
    document_id: str,
    revision_id: str,
    payload: CalibrationCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> CalibrationOut:
    """Enregistre ce qu'une personne déclare : deux points, et leur distance.

    **Un PDF ne porte aucune unité.** Cette route est donc le seul chemin par
    lequel une mesure devient possible — et il passe obligatoirement par une
    personne. Rien ici ne lit l'échelle du cartouche : elle ne vaut plus dès
    qu'un export a coché « ajuster à la page », et s'y fier produirait des
    quantités fausses et plausibles.

    `DOCUMENT_WRITE` et non `DOCUMENT_VALIDATE` : calibrer n'approuve rien. Les
    mesures qui en descendront resteront des propositions, et c'est leur
    acceptation qui demandera le droit de valider.
    """
    try:
        documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    try:
        calibration = calibration_de_plan.calibrer(
            session,
            stockage=StockageLocal(settings.storage_root),
            organization_id=context.organization_id,
            revision_id=revision_id,
            actor_user_id=context.user.id,
            page=payload.page,
            premier_ecran=(payload.premier.x, payload.premier.y),
            second_ecran=(payload.second.x, payload.second.y),
            distance_reelle=payload.distance_reelle,
            unite=payload.unite,
            resolution_du_pointage=payload.resolution_du_pointage,
            motif=payload.motif,
            zone=tuple(payload.zone) if payload.zone else None,  # type: ignore[arg-type]
        )
    except calibration_de_plan.CalibrationRefusee as refus:
        raise _refus_de_calibration(refus, refus.code, refus.message) from refus
    except mesures_pdf.MesureRefusee as refus:
        raise _refus_de_calibration(refus, refus.code, refus.message) from refus
    except UnknownUnitError as erreur:
        raise _refus_de_calibration(
            erreur,
            "unknown_unit",
            f"L'unité « {payload.unite} » n'est pas connue de Metreo.",
        ) from erreur

    return _calibration_out(calibration)


@router.post(
    "/documents/{document_id}/revisions/{revision_id}/plan/mesures",
    response_model=MesureDePdf,
    status_code=status.HTTP_201_CREATED,
    summary="Mesurer un segment ou une surface sur un PDF calibré",
)
def post_mesure_de_pdf(
    document_id: str,
    revision_id: str,
    payload: MesureCreate,
    context: TenantContext = Depends(require(Permission.DOCUMENT_WRITE)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> MesureDePdf:
    """Mesure, puis écrit une PROPOSITION — jamais une quantité approuvée.

    La mesure arrive avec son incertitude, calculée depuis la résolution à
    laquelle les points ont été posés. Au-delà du seuil, elle reste « à
    vérifier » : conservée, parce qu'un ordre de grandeur sert, et réservée,
    parce que personne ne l'a encore regardée.
    """
    try:
        documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    try:
        ecrite = calibration_de_plan.mesurer(
            session,
            stockage=StockageLocal(settings.storage_root),
            organization_id=context.organization_id,
            revision_id=revision_id,
            page=payload.page,
            type_de_mesure=payload.type,
            points_ecran=[(point.x, point.y) for point in payload.points],
            libelle=payload.libelle,
        )
    except calibration_de_plan.CalibrationRefusee as refus:
        raise _refus_de_calibration(refus, refus.code, refus.message) from refus
    except mesures_pdf.MesureRefusee as refus:
        raise _refus_de_calibration(refus, refus.code, refus.message) from refus

    relues = calibration_de_plan.lister(
        session, organization_id=context.organization_id, revision_id=revision_id
    )
    ajoutee = next(m for m in relues if m.proposal_id == ecrite.proposal_id)
    return _mesure_de_pdf(ajoutee)


def _mesure_de_pdf(mesure: calibration_de_plan.MesureALire) -> MesureDePdf:
    return MesureDePdf(
        proposal_id=mesure.proposal_id,
        citation_id=mesure.citation_id,
        page=mesure.page,
        type=mesure.type,
        libelle=mesure.libelle,
        valeur=mesure.valeur,
        unite=mesure.unite,
        incertitude=mesure.incertitude,
        incertitude_relative=mesure.incertitude_relative,
        fiabilite=mesure.fiabilite,
        reserves=list(mesure.reserves),
        points=[PointDEcran(x=x, y=y) for x, y in mesure.points_ecran],
        cadre=(
            CadreDePlan(
                x0=mesure.cadre[0],
                y0=mesure.cadre[1],
                x1=mesure.cadre[2],
                y1=mesure.cadre[3],
            )
            if mesure.cadre
            else None
        ),
        calibration=mesure.calibration,
        decision=mesure.decision,
        motif_de_la_decision=mesure.motif_de_la_decision,
        valeur_corrigee=mesure.valeur_corrigee,
        valeur_lisible=mesure.valeur_lisible,
        incertitude_lisible=mesure.incertitude_lisible,
        valeur_retenue_lisible=mesure.valeur_retenue_lisible,
        unite_retenue=mesure.unite_retenue,
        valeur_retenue=mesure.valeur_retenue,
        reprenable=mesure.reprenable,
    )


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/plan/mesures",
    response_model=MesuresDePdf,
    summary="Les échelles déclarées et les mesures prises sur un PDF",
)
def get_mesures_de_pdf(
    document_id: str,
    revision_id: str,
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
) -> MesuresDePdf:
    """Tout ce qu'il faut pour redessiner le travail fait sur ce plan.

    Les calibrations ET les mesures, parce que l'écran doit pouvoir afficher
    d'où vient chaque nombre. Une mesure sans son échéelle est un nombre sans
    provenance, et c'est exactement ce que ce produit refuse de montrer.
    """
    try:
        documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    return MesuresDePdf(
        revision_id=revision_id,
        calibrations=[
            _calibration_out(ligne)
            for ligne in calibration_de_plan.lister_les_calibrations(
                session,
                organization_id=context.organization_id,
                revision_id=revision_id,
            )
        ],
        mesures=[
            _mesure_de_pdf(mesure)
            for mesure in calibration_de_plan.lister(
                session,
                organization_id=context.organization_id,
                revision_id=revision_id,
            )
        ],
    )


@router.get(
    "/documents/{document_id}/revisions/{revision_id}/plan/tuile",
    summary="Agrandir une zone d'une page, pour la relire",
    response_class=StreamingResponse,
)
def get_tuile(
    document_id: str,
    revision_id: str,
    page: int = Query(default=1, ge=1),
    # La page entière par défaut. Une zone obligatoire ferait répondre 422 à
    # une demande sans paramètres — AVANT le contrôle d'appartenance — et un
    # identifiant d'un autre tenant deviendrait distinguable d'un identifiant
    # inexistant par le seul code de retour. L'invariant du dépôt est que les
    # deux sont indiscernables.
    x0: float = Query(default=0.0, ge=0.0, le=1.0),
    y0: float = Query(default=0.0, ge=0.0, le=1.0),
    x1: float = Query(default=1.0, ge=0.0, le=1.0),
    y1: float = Query(default=1.0, ge=0.0, le=1.0),
    context: TenantContext = Depends(require(Permission.DOCUMENT_READ)),
    session: Session = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Sert l'agrandissement d'une zone — depuis le cache quand elle y est.

    **Pourquoi cette route existe.** Mesuré sur quatre plans réels : sur
    l'aperçu pleine page, la hauteur médiane d'une ligne de texte est de 2,4 à
    3,6 pixels, et un pixel vaut 12 à 42 millimètres d'ouvrage aux échelles
    1:20 à 1:50. On ne peut donc ni relire une cote, ni pointer utilement. La
    tuile est ce qui rend les deux possibles.

    **Ce qu'elle coûte, et ce que ça implique.** Le rendu se fait dans un
    processus séparé qui meurt ensuite, parce que charger une page réserve 54 à
    422 Mo que PDFium ne rend pas au système. Première demande : 0,2 à 5,3
    secondes. Les suivantes : 0,3 milliseconde, depuis le volume. L'écran doit
    donc traiter cette route comme lente la première fois, et instantanée
    ensuite. La décision complète, avec ses limites de ressources, est l'ADR
    0008.
    """
    try:
        revision = documents.get_revision(
            session,
            organization_id=context.organization_id,
            document_id=document_id,
            revision_id=revision_id,
        )
    except documents.RevisionRefusee as erreur:
        raise _refus_http(erreur) from erreur

    if x1 <= x0 or y1 <= y0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "zone_invalide",
                "message": "La zone demandée est plate ou inversée.",
            },
        )

    stockage = StockageLocal(settings.storage_root)
    try:
        tuile = tuiles.obtenir(
            stockage,
            organization_id=context.organization_id,
            revision_id=revision_id,
            original=stockage.chemin(revision.storage_key),
            page=page,
            zone=(x0, y0, x1, y1),
        )
    except tuiles.TuileRefusee as refus:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": refus.code, "message": refus.message},
        ) from refus

    return StreamingResponse(
        iter([tuile.png]),
        media_type="image/png",
        headers={
            "Content-Length": str(len(tuile.png)),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
            # Pour le diagnostic : une tuile servie en une milliseconde vient
            # du volume, une servie en quatre secondes vient d'être rendue.
            "X-Metreo-Tuile": "cache" if tuile.depuis_le_cache else "rendue",
        },
    )
