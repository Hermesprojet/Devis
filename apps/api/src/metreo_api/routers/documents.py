"""Document metadata and append-only human validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Document, DocumentRevision, ValidationDecision
from ..schemas import (
    AnomalieDePlan,
    CadreDePlan,
    DocumentCreate,
    DocumentOut,
    DocumentRevisionOut,
    DocumentStatusUpdate,
    FragmentDeTexte,
    MesureDePlan,
    PlanLu,
    TextesDePlan,
    ValidationDecisionCreate,
    ValidationDecisionOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import (
    documents,
    exports,
    lecture_de_plan,
    mesures_de_plan,
    rendu_de_plan,
    rendu_pdf,
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
    return documents.record_validation_decision(
        session,
        context=context,
        proposal_id=proposal_id,
        payload=payload,
    )


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
                cadre=CadreDePlan(
                    x0=str(cadre[0]), y0=str(cadre[1]), x1=str(cadre[2]), y1=str(cadre[3])
                ),
            )
            for f in tranche
            if isinstance(cadre := f.get("cadre"), list) and len(cadre) == 4
        ],
    )
