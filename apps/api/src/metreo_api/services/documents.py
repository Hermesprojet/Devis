"""Tenant-aware metadata operations for documentary records.

No binary content, storage provider, OCR or extracted value is handled here.
Every read starts from an explicit organisation and every audit event contains
identifiers and state only — never a document title, filename, free-form
validation reason or extracted value.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Document,
    DocumentRevision,
    DocumentStepRun,
    ExtractionProposal,
    Project,
    User,
    ValidationDecision,
    new_id,
    utcnow,
)
from ..schemas import ValidationDecisionCreate
from ..security.auth import TenantContext
from ..transactions import compenser
from . import audit
from .document_storage import StockageLocal
from .locking import lock_owned
from .tenant import get_owned, owned_query

DOCUMENT_PIPELINE_STEPS = frozenset(
    {
        "receive_security",
        "detection",
        "native_text",
        "ocr",
        "tables",
        "segmentation",
        "classification",
        "structured_extraction",
        "indexing",
        "consistency",
        "human_review",
    }
)

# Only stable machine codes cross this boundary.  A provider exception or a
# document excerpt is mapped to one of these before persistence; no free-form
# failure message is accepted by the service.
SAFE_STEP_ERROR_CODES = frozenset(
    {
        "invalid_output",
        "malware_detected",
        "processing_failed",
        "provider_unavailable",
        "timeout",
        "unsupported_media_type",
    }
)


class DocumentStepRunRefused(Exception):
    """Typed refusal whose message never includes document or provider data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _nonblank_version(value: str, *, code: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        raise DocumentStepRunRefused(code)
    return normalized


def _duration(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DocumentStepRunRefused("invalid_step_duration")
    return value


def list_documents(
    session: Session,
    *,
    organization_id: str,
    project_id: str,
    include_archived: bool = False,
) -> list[Document]:
    """Lister les documents d'un projet, après avoir prouvé qu'il est à nous.

    Les archivés sont hors de la liste courante par défaut, et rendus sur
    demande explicite : archiver range, cela ne détruit pas. `owned_query`
    écarte déjà `deleted_at`, qui est une autre notion — aucune route ne le
    pose, et l'archivage ne le touche pas.
    """
    get_owned(session, Project, organization_id, project_id, label="Projet")
    query = owned_query(Document, organization_id).where(Document.project_id == project_id)
    if not include_archived:
        query = query.where(Document.status == "active")
    return list(session.scalars(query.order_by(Document.created_at.desc())).all())


def create_document(
    session: Session,
    *,
    context: TenantContext,
    project_id: str,
    title: str,
) -> Document:
    """Create a logical document; no file or storage key is accepted."""
    get_owned(session, Project, context.organization_id, project_id, label="Projet")
    document = Document(
        organization_id=context.organization_id,
        project_id=project_id,
        title=title,
        created_by=context.user.id,
    )
    session.add(document)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.created",
        object_type="document",
        object_id=document.id,
        summary="Document créé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"project_id": project_id},
    )
    return document


def get_document(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> Document:
    return get_owned(
        session,
        Document,
        organization_id,
        document_id,
        label="Document",
    )


def list_revisions(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> list[DocumentRevision]:
    """List safe revision metadata after a tenant-scoped parent lookup."""
    get_document(session, organization_id=organization_id, document_id=document_id)
    revisions = list(
        session.scalars(
            select(DocumentRevision)
            .where(
                DocumentRevision.organization_id == organization_id,
                DocumentRevision.document_id == document_id,
            )
            .order_by(DocumentRevision.revision_number.desc())
        ).all()
    )
    _attacher_auteurs(session, revisions)
    return revisions


def _attacher_auteurs(session: Session, revisions: list[DocumentRevision]) -> None:
    """Poser l'adresse de l'auteur sur chaque révision, en UNE requête.

    Le schéma de sortie la lit comme un attribut ordinaire. Résoudre l'auteur
    ligne par ligne ferait une requête par révision pour un écran qui les
    affiche toutes.
    """
    identifiants = {r.created_by for r in revisions if r.created_by}
    adresses: dict[str, str] = {}
    if identifiants:
        adresses = {
            str(identifiant): str(courriel)
            for identifiant, courriel in session.execute(
                select(User.id, User.email).where(User.id.in_(identifiants))
            ).all()
        }
    for revision in revisions:
        revision.author_email = adresses.get(revision.created_by or "")  # type: ignore[attr-defined]


def next_revision_number(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
) -> int:
    """Allocate the next revision number while holding the parent document.

    PostgreSQL serialises concurrent allocators on the document row. SQLite
    serialises writers at file level; the unique constraint remains the final
    defence on both engines.
    """
    lock_owned(
        session,
        Document,
        organization_id,
        document_id,
        label="Document",
    )
    current = session.scalar(
        select(func.max(DocumentRevision.revision_number)).where(
            DocumentRevision.organization_id == organization_id,
            DocumentRevision.document_id == document_id,
        )
    )
    return int(current or 0) + 1


class RevisionRefusee(Exception):
    """Un dépôt refusé pour une raison métier, pas pour son contenu."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_revision(
    session: Session,
    *,
    organization_id: str,
    document_id: str,
    revision_id: str,
) -> DocumentRevision:
    """La révision, ou 404 — jamais celle d'un autre document ni d'un autre tenant.

    Le parent est vérifié d'abord : sans cela, un identifiant de révision
    valide suffirait à traverser un document auquel l'appelant n'a pas accès.
    """
    get_document(session, organization_id=organization_id, document_id=document_id)
    revision = session.scalars(
        select(DocumentRevision).where(
            DocumentRevision.organization_id == organization_id,
            DocumentRevision.document_id == document_id,
            DocumentRevision.id == revision_id,
        )
    ).one_or_none()
    if revision is None:
        raise RevisionRefusee("not_found", "Révision introuvable.")
    return revision


def add_revision(
    session: Session,
    *,
    context: TenantContext,
    document_id: str,
    stockage: StockageLocal,
    morceaux: Iterable[bytes],
    filename: str | None,
    declared_media_type: str | None,
    plafond: int,
) -> DocumentRevision:
    """Attacher un original à un document, en une révision immuable de plus.

    L'ordre est choisi, et c'est là tout le sujet :

    1. le document est vérifié AVANT d'écrire quoi que ce soit — inutile de
       recevoir 25 Mio pour découvrir ensuite qu'ils ne nous concernent pas ;
    2. le fichier est écrit SANS tenir le moindre verrou. Un dépôt dure ; tenir
       la ligne du document verrouillée pendant ce temps bloquerait tout autre
       dépôt sur le même document ;
    3. le numéro de révision n'est alloué qu'ensuite, sous verrou, et c'est
       cette section courte qui sérialise deux dépôts simultanés ;
    4. si l'insertion échoue, l'original qui vient d'être écrit est retiré :
       un fichier sans ligne en base ne serait plus jamais retrouvable.

    La révision naît `published`. Elle n'a pas d'état intermédiaire à décrire :
    le fichier est complet et vérifié avant que la ligne n'existe. Les triggers
    de la migration la rendent immuable dès cet instant, sur SQLite comme sur
    PostgreSQL.
    """
    document = get_document(
        session, organization_id=context.organization_id, document_id=document_id
    )
    if document.status != "active":
        raise RevisionRefusee(
            "document_archived",
            "Ce document est archivé. Réactivez-le avant d'y joindre une révision.",
        )

    revision_id = new_id()
    original = stockage.ecrire(
        organization_id=context.organization_id,
        document_id=document_id,
        revision_id=revision_id,
        morceaux=morceaux,
        plafond=plafond,
        declared_media_type=declared_media_type,
    )
    # Les octets sont posés ; la base ne le sait pas encore, et ne saura jamais
    # les retirer. À partir d'ici et jusqu'à la validation, ce fichier n'existe
    # QUE sous condition : si la transaction est annulée — par un refus, une
    # contrainte, ou un `commit` qui échoue —, cette compensation le retire.
    # Elle est idempotente : `supprimer` accepte un fichier déjà parti.
    compenser(
        session,
        lambda: stockage.supprimer(original.storage_key),
        f"retirer l'original {revision_id} du volume",
    )

    numero = next_revision_number(
        session, organization_id=context.organization_id, document_id=document_id
    )
    # Le doublon est constaté APRÈS le verrou : deux dépôts simultanés du
    # même contenu doivent en voir un seul passer, et le second l'apprendre.
    deja = session.scalars(
        select(DocumentRevision).where(
            DocumentRevision.organization_id == context.organization_id,
            DocumentRevision.document_id == document_id,
            DocumentRevision.sha256 == original.sha256,
        )
    ).first()
    if deja is not None:
        raise RevisionRefusee(
            "duplicate_content",
            f"Ce contenu est déjà la révision {deja.revision_number} de ce document, "
            "au bit près. Rien n'a été remplacé.",
        )

    revision = DocumentRevision(
        id=revision_id,
        organization_id=context.organization_id,
        document_id=document_id,
        revision_number=numero,
        sha256=original.sha256,
        byte_size=original.byte_size,
        media_type=original.media_type,
        declared_media_type=original.declared_media_type,
        storage_key=original.storage_key,
        original_filename=filename or "document",
        status="published",
        published_at=utcnow(),
        created_by=context.user.id,
    )
    session.add(revision)
    session.flush()
    # L'auteur, sur la réponse du dépôt comme sur celle de la liste : c'est
    # la même vue, elle doit porter les mêmes champs.
    revision.author_email = context.user.email  # type: ignore[attr-defined]

    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.revision_added",
        object_type="document_revision",
        object_id=revision.id,
        summary=f"Révision {numero} déposée ({original.byte_size} octets)",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        # Des FAITS sur le fichier, jamais un fragment de son contenu : une
        # empreinte, une taille, un type, et le type qu'annonçait le client.
        payload={
            "document_id": document_id,
            "revision_number": numero,
            "sha256": original.sha256,
            "byte_size": original.byte_size,
            "media_type": original.media_type,
            "declared_media_type": original.declared_media_type,
        },
    )
    return revision


def record_download(
    session: Session,
    *,
    context: TenantContext,
    revision: DocumentRevision,
) -> None:
    """Qui a emporté quel original, et quand.

    Un document de chantier peut être confidentiel : savoir qu'il est sorti,
    et par qui, fait partie de ce que l'entreprise doit pouvoir montrer. Le
    journal ne porte que des faits sur le fichier — jamais un octet de son
    contenu, jamais un jeton.
    """
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.downloaded",
        object_type="document_revision",
        object_id=revision.id,
        summary=f"Révision {revision.revision_number} téléchargée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "document_id": revision.document_id,
            "revision_number": revision.revision_number,
            "sha256": revision.sha256,
            "byte_size": revision.byte_size,
        },
    )


def set_document_status(
    session: Session,
    *,
    context: TenantContext,
    document_id: str,
    status: str,
) -> Document:
    """Archiver ou réactiver. Rien n'est détruit, ni en base ni sur le volume.

    L'archivage sort le document des listes courantes ; ses révisions, son
    historique et ses originaux restent intacts et téléchargeables. C'est la
    seule forme de retrait qu'offre l'interface : aucune route ne supprime un
    original, parce qu'un devis gelé peut le citer.
    """
    if status not in ("active", "archived"):
        raise RevisionRefusee("invalid_status", "Statut inconnu.")
    document = get_document(
        session, organization_id=context.organization_id, document_id=document_id
    )
    if document.status == status:
        return document
    document.status = status
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.archived" if status == "archived" else "document.reactivated",
        object_type="document",
        object_id=document.id,
        summary="Document archivé" if status == "archived" else "Document réactivé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    return document


def claim_step_run(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    step: str,
    pipeline_version: str,
    prompt_version: str,
    model_version: str,
) -> tuple[DocumentStepRun, bool]:
    """Claim exactly one versioned step run, returning ``(row, created)``.

    The immutable revision is the concurrency mutex.  Two workers claiming
    the same key queue on it; the second then observes the row created by the
    first instead of hitting the uniqueness constraint.  The database unique
    key remains the final defence against writers that bypass this service.
    """
    if step not in DOCUMENT_PIPELINE_STEPS:
        raise DocumentStepRunRefused("invalid_document_step")
    pipeline_version = _nonblank_version(
        pipeline_version,
        code="invalid_pipeline_version",
    )
    prompt_version = _nonblank_version(
        prompt_version,
        code="invalid_prompt_version",
    )
    model_version = _nonblank_version(
        model_version,
        code="invalid_model_version",
    )

    lock_owned(
        session,
        DocumentRevision,
        organization_id,
        revision_id,
        label="Révision documentaire",
    )
    existing = session.scalars(
        owned_query(DocumentStepRun, organization_id).where(
            DocumentStepRun.revision_id == revision_id,
            DocumentStepRun.step == step,
            DocumentStepRun.pipeline_version == pipeline_version,
            DocumentStepRun.prompt_version == prompt_version,
            DocumentStepRun.model_version == model_version,
        )
    ).one_or_none()
    if existing is not None:
        return existing, False

    started_at = utcnow()
    run = DocumentStepRun(
        organization_id=organization_id,
        revision_id=revision_id,
        step=step,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        model_version=model_version,
        status="running",
        attempt=1,
        started_at=started_at,
    )
    session.add(run)
    session.flush()
    return run, True


def succeed_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    duration_ms: int,
    finished_at: datetime | None = None,
) -> DocumentStepRun:
    """Finish a running step; repeating the same outcome is idempotent."""
    duration_ms = _duration(duration_ms)
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "succeeded":
        return run
    if run.status == "failed":
        raise DocumentStepRunRefused("step_already_failed")
    if run.status != "running":
        raise DocumentStepRunRefused("step_not_running")
    run.status = "succeeded"
    run.finished_at = finished_at or utcnow()
    run.duration_ms = duration_ms
    run.error_code = None
    run.error_summary = None
    session.flush()
    return run


def fail_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    error_code: str,
    duration_ms: int,
    finished_at: datetime | None = None,
) -> DocumentStepRun:
    """Record a failed step using a bounded, non-sensitive machine code."""
    duration_ms = _duration(duration_ms)
    if error_code not in SAFE_STEP_ERROR_CODES:
        raise DocumentStepRunRefused("invalid_step_error_code")
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "failed" and run.error_code == error_code:
        return run
    if run.status == "failed":
        raise DocumentStepRunRefused("step_already_failed")
    if run.status == "succeeded":
        raise DocumentStepRunRefused("step_already_succeeded")
    if run.status != "running":
        raise DocumentStepRunRefused("step_not_running")
    run.status = "failed"
    run.finished_at = finished_at or utcnow()
    run.duration_ms = duration_ms
    run.error_code = error_code
    # Deliberately no free-form provider exception or document text.
    run.error_summary = None
    session.flush()
    return run


def retry_failed_step_run(
    session: Session,
    *,
    organization_id: str,
    step_run_id: str,
    started_at: datetime | None = None,
) -> tuple[DocumentStepRun, bool]:
    """Restart one failed row without changing its idempotence key."""
    run = lock_owned(
        session,
        DocumentStepRun,
        organization_id,
        step_run_id,
        label="Étape documentaire",
    )
    if run.status == "running":
        return run, False
    if run.status == "succeeded":
        raise DocumentStepRunRefused("step_already_succeeded")
    if run.status != "failed":
        raise DocumentStepRunRefused("step_not_failed")
    run.status = "running"
    run.attempt += 1
    run.started_at = started_at or utcnow()
    run.finished_at = None
    run.duration_ms = None
    run.error_code = None
    run.error_summary = None
    session.flush()
    return run, True


def record_validation_decision(
    session: Session,
    *,
    context: TenantContext,
    proposal_id: str,
    payload: ValidationDecisionCreate,
) -> ValidationDecision:
    """Append one human decision without rewriting the machine proposal."""
    proposal = get_owned(
        session,
        ExtractionProposal,
        context.organization_id,
        proposal_id,
        label="Proposition",
    )
    decision = ValidationDecision(
        organization_id=context.organization_id,
        proposal_id=proposal.id,
        actor_user_id=context.user.id,
        decision=payload.decision,
        reason=payload.reason,
        before_value=payload.before_value,
        after_value=payload.after_value,
    )
    session.add(decision)
    session.flush()
    audit.record(
        session,
        organization_id=context.organization_id,
        action="document.validation_decided",
        object_type="validation_decision",
        object_id=decision.id,
        summary="Décision documentaire enregistrée",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "proposal_id": proposal.id,
            "decision": decision.decision,
        },
    )
    return decision
