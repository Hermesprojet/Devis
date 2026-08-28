"""Adversarial SQL proofs for the documentary relational foundation.

These tests deliberately bypass future HTTP schemas and services.  Every
critical invariant must survive a direct ORM or SQL write on both SQLite and
PostgreSQL, because the database is the final tenant and audit boundary.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from metreo_api.db import Base, get_engine, get_session_factory
from metreo_api.models import (
    Document,
    DocumentRevision,
    DocumentStepRun,
    ExtractionProposal,
    Membership,
    Organization,
    Project,
    SourceCitation,
    User,
    ValidationDecision,
)


@dataclass(frozen=True)
class Graph:
    organization: Organization
    user: User
    project: Project
    document: Document
    revision: DocumentRevision
    citation: SourceCitation
    proposal: ExtractionProposal


@pytest.fixture()
def db_session(migrated: None) -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _id() -> str:
    return str(uuid4())


def _add_identity(
    session: Session,
    *,
    label: str,
) -> tuple[Organization, User, Project]:
    organization = Organization(id=_id(), name=f"Organisation {label}")
    user = User(
        id=_id(),
        email=f"{label}-{_id()}@example.invalid",
        full_name=f"Humain {label}",
    )
    membership = Membership(
        id=_id(),
        user_id=user.id,
        organization_id=organization.id,
        role="estimator",
    )
    project = Project(
        id=_id(),
        organization_id=organization.id,
        reference=f"REF-{label}-{_id()[:8]}",
        name=f"Projet {label}",
    )
    session.add_all((organization, user, membership, project))
    session.commit()
    return organization, user, project


def _add_graph(
    session: Session,
    *,
    label: str = "A",
    published: bool = False,
) -> Graph:
    organization, user, project = _add_identity(session, label=label)
    document = Document(
        id=_id(),
        organization_id=organization.id,
        project_id=project.id,
        title=f"Cahier des charges {label}",
        created_by=user.id,
    )
    revision = DocumentRevision(
        id=_id(),
        organization_id=organization.id,
        document_id=document.id,
        revision_number=1,
        sha256="a" * 64,
        byte_size=128,
        media_type="application/pdf",
        storage_key=f"tenant/{organization.id}/revision.pdf",
        original_filename="cahier.pdf",
        status="published" if published else "draft",
        published_at=datetime.now(UTC).replace(tzinfo=None) if published else None,
        created_by=user.id,
    )
    citation = SourceCitation(
        id=_id(),
        organization_id=organization.id,
        revision_id=revision.id,
        page=1,
        char_start=0,
        char_end=12,
        x0=Decimal("0.10"),
        y0=Decimal("0.20"),
        x1=Decimal("0.80"),
        y1=Decimal("0.90"),
        extractor="native-text-v1",
        confidence=Decimal("0.95"),
    )
    proposal = ExtractionProposal(
        id=_id(),
        organization_id=organization.id,
        revision_id=revision.id,
        citation_id=citation.id,
        schema_name="lot",
        schema_version="1",
        value={"label": "Terrassement"},
        confidence=Decimal("0.90"),
        pipeline_version="pipeline-1",
        prompt_version="prompt-1",
        model_version="model-1",
    )
    # No ORM relationships are declared on these persistence-only models.
    # Flush in dependency order so the fixture itself does not rely on
    # SQLAlchemy guessing an insertion order from mapper relationships.
    session.add(document)
    session.flush()
    session.add(revision)
    session.flush()
    session.add(citation)
    session.flush()
    session.add(proposal)
    session.commit()
    return Graph(organization, user, project, document, revision, citation, proposal)


def _revision(graph: Graph, **changes: object) -> DocumentRevision:
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "document_id": graph.document.id,
        "revision_number": 2,
        "sha256": "b" * 64,
        "byte_size": 256,
        "media_type": "application/pdf",
        "storage_key": f"tenant/{graph.organization.id}/revision-2.pdf",
        "original_filename": "revision-2.pdf",
        "status": "draft",
        "published_at": None,
        "created_by": graph.user.id,
    }
    values.update(changes)
    return DocumentRevision(**values)  # type: ignore[arg-type]


def _step(graph: Graph, **changes: object) -> DocumentStepRun:
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "revision_id": graph.revision.id,
        "step": "ocr",
        "pipeline_version": "pipeline-1",
        "prompt_version": "none",
        "model_version": "ocr-1",
        "status": "pending",
        "attempt": 1,
    }
    values.update(changes)
    return DocumentStepRun(**values)  # type: ignore[arg-type]


def _citation(graph: Graph, **changes: object) -> SourceCitation:
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "revision_id": graph.revision.id,
        "page": 2,
        "char_start": 10,
        "char_end": 20,
        "x0": Decimal("0.10"),
        "y0": Decimal("0.10"),
        "x1": Decimal("0.90"),
        "y1": Decimal("0.90"),
        "extractor": "ocr-v1",
        "confidence": Decimal("0.80"),
    }
    values.update(changes)
    return SourceCitation(**values)  # type: ignore[arg-type]


def _proposal(graph: Graph, **changes: object) -> ExtractionProposal:
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "revision_id": graph.revision.id,
        "citation_id": graph.citation.id,
        "schema_name": "quantity",
        "schema_version": "1",
        "value": {"value": "12.5", "unit": "m3"},
        "confidence": Decimal("0.80"),
        "pipeline_version": "pipeline-1",
        "prompt_version": "prompt-1",
        "model_version": "model-1",
        "status": "proposed",
    }
    values.update(changes)
    return ExtractionProposal(**values)  # type: ignore[arg-type]


def _decision(graph: Graph, **changes: object) -> ValidationDecision:
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "proposal_id": graph.proposal.id,
        "actor_user_id": graph.user.id,
        "decision": "accepted",
        "reason": "Vérification humaine.",
        "before_value": None,
        "after_value": None,
    }
    values.update(changes)
    return ValidationDecision(**values)  # type: ignore[arg-type]


def _commit_is_refused(session: Session, row: object) -> None:
    session.add(row)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_migration_and_models_have_no_schema_drift(migrated: None) -> None:
    """Zéro écart sous PostgreSQL ; sous SQLite, exactement l'écart déjà nommé.

    La révision `b4f2c7d81a05` pose une action référentielle sur huit clés
    composites de la Phase 1, et elle ne le fait **que sur PostgreSQL** :
    l'analyseur SQLite refuse `ON DELETE SET NULL (colonne)`, et la forme nue y
    viderait `organization_id`, NOT NULL. Aucune déclaration unique ne convient
    aux deux moteurs, donc le modèle déclare ce que PostgreSQL porte et SQLite
    ne le porte pas.

    L'exception n'est pas une règle générique — « ignorer les contraintes
    `_tenant` » laisserait passer une neuvième relation ou une action changée.
    C'est la table nominative de `test_referential_action_drift`, avec le nom,
    le nombre et la clause exacte de chacune ; elle est elle-même confrontée au
    tableau `RELATIONS` de la révision. Partagée plutôt que recopiée : deux
    listes divergeraient dès la première correction.

    Tout le reste — les six tables documentaires, leurs colonnes, leurs clés
    composites, leurs index — est comparé sans aucune indulgence, sur les deux
    moteurs.
    """
    from .test_referential_action_drift import unexpected_sqlite_drift

    engine = get_engine()
    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}),
            Base.metadata,
        )
    if engine.dialect.name == "postgresql":
        assert differences == []
        return
    anomalies = unexpected_sqlite_drift(list(differences))
    assert anomalies == [], "\n".join(anomalies)


def test_all_six_tables_and_composite_tenant_keys_exist(migrated: None) -> None:
    inspector = inspect(get_engine())
    expected = {
        "documents",
        "document_revisions",
        "document_step_runs",
        "source_citations",
        "extraction_proposals",
        "validation_decisions",
    }
    assert expected <= set(inspector.get_table_names())
    for table in expected:
        assert "organization_id" in {column["name"] for column in inspector.get_columns(table)}

    expected_fk_columns: dict[str, set[tuple[str, ...]]] = {
        "documents": {("organization_id", "project_id")},
        "document_revisions": {("organization_id", "document_id")},
        "document_step_runs": {("organization_id", "revision_id")},
        "source_citations": {("organization_id", "revision_id")},
        "extraction_proposals": {
            ("organization_id", "revision_id", "citation_id"),
        },
        "validation_decisions": {
            ("organization_id", "proposal_id"),
            ("actor_user_id", "organization_id"),
        },
    }
    for table, composite_keys in expected_fk_columns.items():
        actual = {
            tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys(table)
        }
        assert composite_keys <= actual


@pytest.mark.parametrize(
    "changes",
    (
        {"title": " "},
        {"status": "approved"},
    ),
)
def test_document_checks_are_database_constraints(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    graph = _add_graph(db_session)
    values: dict[str, object] = {
        "id": _id(),
        "organization_id": graph.organization.id,
        "project_id": graph.project.id,
        "title": "Rapport géologique",
        "status": "active",
        "created_by": graph.user.id,
    }
    values.update(changes)
    _commit_is_refused(db_session, Document(**values))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("revision_number", 0),
        ("byte_size", 0),
        ("sha256", "not-a-sha256"),
        ("media_type", " "),
        ("storage_key", ""),
        ("original_filename", " "),
        ("status", "approved"),
        ("status", "published"),
    ),
)
def test_revision_checks_are_database_constraints(
    db_session: Session,
    field: str,
    value: object,
) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _revision(graph, **{field: value}))


def test_revision_number_is_unique_inside_one_document(db_session: Session) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _revision(graph, revision_number=1))


@pytest.mark.parametrize(
    "changes",
    (
        {"step": "invented"},
        {"status": "invented"},
        {"attempt": 0},
        {"duration_ms": -1},
        {"status": "failed", "error_code": None},
        {"status": "failed", "error_code": " "},
        {"pipeline_version": " "},
        {"prompt_version": ""},
        {"model_version": " "},
    ),
)
def test_step_checks_are_database_constraints(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _step(graph, **changes))


def test_step_idempotence_key_has_no_nullable_hole(db_session: Session) -> None:
    graph = _add_graph(db_session)
    first = _step(graph)
    db_session.add(first)
    db_session.commit()
    _commit_is_refused(db_session, _step(graph, attempt=2))
    columns = {
        column["name"]: column for column in inspect(get_engine()).get_columns("document_step_runs")
    }
    assert columns["pipeline_version"]["nullable"] is False
    assert columns["prompt_version"]["nullable"] is False
    assert columns["model_version"]["nullable"] is False


@pytest.mark.parametrize(
    "changes",
    (
        {"page": 0},
        {"char_start": -1},
        {"char_start": 10, "char_end": 10},
        {"x0": Decimal("-0.01")},
        {"y0": Decimal("-0.01")},
        {"x1": Decimal("1.01")},
        {"y1": Decimal("1.01")},
        {"x0": Decimal("0.90"), "x1": Decimal("0.90")},
        {"y0": Decimal("0.90"), "y1": Decimal("0.90")},
        {"confidence": Decimal("-0.01")},
        {"confidence": Decimal("1.01")},
        {"extractor": " "},
    ),
)
def test_citation_checks_are_database_constraints(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _citation(graph, **changes))


@pytest.mark.parametrize(
    "changes",
    (
        {"confidence": Decimal("-0.01")},
        {"confidence": Decimal("1.01")},
        {"status": "approved"},
        {"schema_name": " "},
        {"schema_version": ""},
        {"pipeline_version": " "},
        {"prompt_version": ""},
        {"model_version": " "},
        {"citation_id": None},
        {"confidence": None},
    ),
)
def test_proposal_checks_and_required_evidence_are_database_constraints(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _proposal(graph, **changes))


@pytest.mark.parametrize(
    "changes",
    (
        {"decision": "approved"},
        {"reason": " "},
        {"actor_user_id": None},
        {"decision": "corrected", "before_value": None, "after_value": {"value": "2"}},
        {"decision": "corrected", "before_value": {"value": "1"}, "after_value": None},
        {"decision": "accepted", "before_value": {"value": "1"}},
        {"decision": "rejected", "after_value": {"value": "2"}},
    ),
)
def test_decision_checks_are_database_constraints(
    db_session: Session,
    changes: dict[str, object],
) -> None:
    graph = _add_graph(db_session)
    _commit_is_refused(db_session, _decision(graph, **changes))


def test_cross_tenant_document_project_reference_is_refused(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    organization_b, user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(
        db_session,
        Document(
            id=_id(),
            organization_id=organization_b.id,
            project_id=graph.project.id,
            title="Référence interdite",
            created_by=user_b.id,
        ),
    )


def test_cross_tenant_revision_document_reference_is_refused(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    organization_b, user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(
        db_session,
        _revision(
            graph,
            organization_id=organization_b.id,
            created_by=user_b.id,
        ),
    )


def test_cross_tenant_citation_revision_reference_is_refused(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    organization_b, _user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(
        db_session,
        _citation(graph, organization_id=organization_b.id),
    )


def test_cross_tenant_proposal_citation_reference_is_refused(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    organization_b, _user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(
        db_session,
        _proposal(graph, organization_id=organization_b.id),
    )


def test_decision_actor_must_belong_to_the_same_tenant(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    _organization_b, user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(db_session, _decision(graph, actor_user_id=user_b.id))


def test_cross_tenant_decision_proposal_reference_is_refused(db_session: Session) -> None:
    graph = _add_graph(db_session, label="A")
    organization_b, user_b, _project_b = _add_identity(db_session, label="B")
    _commit_is_refused(
        db_session,
        _decision(
            graph,
            organization_id=organization_b.id,
            actor_user_id=user_b.id,
        ),
    )


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_published_revision_is_immutable_in_sql(
    db_session: Session,
    operation: str,
) -> None:
    graph = _add_graph(db_session, published=True)
    statement = (
        text("UPDATE document_revisions SET original_filename = :value WHERE id = :id")
        if operation == "update"
        else text("DELETE FROM document_revisions WHERE id = :id")
    )
    parameters = {"id": graph.revision.id, "value": "interdit.pdf"}
    with pytest.raises(IntegrityError, match="published document revision is immutable"):
        db_session.execute(statement, parameters)
        db_session.commit()
    db_session.rollback()
    assert db_session.get(DocumentRevision, graph.revision.id) is not None


def test_draft_revision_remains_mutable_and_deletable(db_session: Session) -> None:
    graph = _add_graph(db_session)
    graph.revision.original_filename = "renommee.pdf"
    db_session.commit()
    assert graph.revision.original_filename == "renommee.pdf"
    db_session.delete(graph.revision)
    db_session.commit()
    assert db_session.get(DocumentRevision, graph.revision.id) is None


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_validation_decision_is_append_only_in_sql(
    db_session: Session,
    operation: str,
) -> None:
    graph = _add_graph(db_session)
    decision = _decision(graph)
    db_session.add(decision)
    db_session.commit()
    statement = (
        text("UPDATE validation_decisions SET reason = :value WHERE id = :id")
        if operation == "update"
        else text("DELETE FROM validation_decisions WHERE id = :id")
    )
    parameters = {"id": decision.id, "value": "interdit"}
    with pytest.raises(IntegrityError, match="validation decision is append-only"):
        db_session.execute(statement, parameters)
        db_session.commit()
    db_session.rollback()
    assert db_session.get(ValidationDecision, decision.id) is not None


def test_human_decision_does_not_rewrite_proposal_or_business_data(
    db_session: Session,
) -> None:
    graph = _add_graph(db_session)
    before = {
        table: db_session.scalar(text(f"SELECT count(*) FROM {table}"))
        for table in ("boq_items", "price_items", "estimate_versions")
    }
    decision = _decision(
        graph,
        decision="corrected",
        before_value={"label": "Terrassement"},
        after_value={"label": "Terrassements"},
    )
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(graph.proposal)

    assert graph.proposal.status == "proposed"
    assert graph.proposal.value == {"label": "Terrassement"}
    after = {table: db_session.scalar(text(f"SELECT count(*) FROM {table}")) for table in before}
    assert after == before


def test_decimal_citation_values_round_trip_without_float(db_session: Session) -> None:
    graph = _add_graph(db_session)
    db_session.expire(graph.citation)
    citation = db_session.scalar(
        select(SourceCitation).where(SourceCitation.id == graph.citation.id)
    )
    assert citation is not None
    for value in (
        citation.x0,
        citation.y0,
        citation.x1,
        citation.y1,
        citation.confidence,
    ):
        assert isinstance(value, Decimal)
