"""Document metadata and append-only human validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Document, DocumentRevision, ValidationDecision
from ..schemas import (
    DocumentCreate,
    DocumentOut,
    DocumentRevisionOut,
    DocumentStatusUpdate,
    ValidationDecisionCreate,
    ValidationDecisionOut,
)
from ..security.auth import TenantContext, require
from ..security.roles import Permission
from ..services import documents, exports
from ..services.document_storage import (
    TAILLE_MORCEAU,
    ContenuRefuse,
    StockageLocal,
    TropVolumineux,
    nom_original_sur,
)

router = APIRouter(tags=["documents"])


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
