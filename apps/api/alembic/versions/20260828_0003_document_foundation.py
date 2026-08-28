"""Fondations relationnelles du pipeline documentaire.

**Cette révision a changé d'identifiant, et c'est délibéré.** Elle portait
`4d7c9a2e6f10` et descendait de `e2be18fcac1b`, ce qui créait une seconde tête
Alembic à côté de la chaîne multi-tenant : `alembic upgrade head` refusait de
choisir. Elle descend maintenant de `c9d3a5e71b62`, la dernière révision
multi-tenant.

Rechaîner en gardant l'ancien identifiant aurait été pire que la fourche.
Mesuré : une base ayant appliqué `4d7c9a2e6f10` était alors considérée à jour,
`upgrade head` rendait 0 révision appliquée et le code 0, et il lui manquait
**seize clés composites tenant** sans qu'aucun message ne le dise. Avec le
nouvel identifiant, la même base échoue bruyamment sur « Can't locate revision
identified by '4d7c9a2e6f10' ».

Conséquence assumée : **toute base ayant déjà appliqué l'ancienne révision doit
être recréée.** Aucune n'existe en production — la Phase 2A n'a jamais été
fusionnée — et les bases de développement se refont par `make migrate`.

L'unicité `uq_project_org_id (organization_id, id)` que posait cette révision a
été retirée : `uq_projects_id_organization (id, organization_id)`, posée par la
chaîne multi-tenant, couvre exactement le même besoin. PostgreSQL rattache une
clé étrangère à l'unicité par l'ENSEMBLE de ses colonnes, pas par leur ordre ;
en garder deux ne changeait rien au refus inter-tenant et rendait seulement
« laquelle est supprimable » dépendant de l'ordre des migrations — mesuré : la
clé étrangère s'appuie sur celle créée en premier.

Les six tables de cette migration ne traitent aucun fichier et n'appellent
aucun fournisseur. Elles rendent persistables les références, citations,
propositions et décisions humaines, avec isolation inter-tenant dans chaque
clé étrangère. Les deux invariants qui nécessitent l'état précédent d'une
ligne sont portés par des triggers : une révision publiée est immuable et une
décision humaine est append-only.

Revision ID: a7e5c04b93f8
Revises: c9d3a5e71b62
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import metreo_api.db

revision: str = "a7e5c04b93f8"
down_revision: str | None = "c9d3a5e71b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_immutability_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_document_revisions_published_update
            BEFORE UPDATE ON document_revisions
            WHEN OLD.published_at IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'published document revision is immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_document_revisions_published_delete
            BEFORE DELETE ON document_revisions
            WHEN OLD.published_at IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'published document revision is immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_validation_decisions_append_only_update
            BEFORE UPDATE ON validation_decisions
            BEGIN
                SELECT RAISE(ABORT, 'validation decision is append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_validation_decisions_append_only_delete
            BEFORE DELETE ON validation_decisions
            BEGIN
                SELECT RAISE(ABORT, 'validation decision is append-only');
            END
            """
        )
        return

    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION metreo_reject_published_revision_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.published_at IS NOT NULL THEN
                    RAISE EXCEPTION 'published document revision is immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_document_revisions_published_mutation
            BEFORE UPDATE OR DELETE ON document_revisions
            FOR EACH ROW
            EXECUTE FUNCTION metreo_reject_published_revision_mutation()
            """
        )
        op.execute(
            """
            CREATE FUNCTION metreo_reject_validation_decision_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'validation decision is append-only'
                    USING ERRCODE = '23514';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_validation_decisions_append_only
            BEFORE UPDATE OR DELETE ON validation_decisions
            FOR EACH ROW
            EXECUTE FUNCTION metreo_reject_validation_decision_mutation()
            """
        )


def _drop_immutability_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for name in (
            "trg_document_revisions_published_update",
            "trg_document_revisions_published_delete",
            "trg_validation_decisions_append_only_update",
            "trg_validation_decisions_append_only_delete",
        ):
            op.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        return

    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_document_revisions_published_mutation ON document_revisions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_validation_decisions_append_only ON validation_decisions"
        )
        op.execute("DROP FUNCTION IF EXISTS metreo_reject_published_revision_mutation()")
        op.execute("DROP FUNCTION IF EXISTS metreo_reject_validation_decision_mutation()")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_document_status"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_document_title_nonempty"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            name="fk_documents_org_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_document_org_id"),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("ix_documents_org_project", "documents", ["organization_id", "project_id"])

    op.create_table(
        "document_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision_number > 0", name="ck_document_revision_number_positive"),
        sa.CheckConstraint("byte_size > 0", name="ck_document_revision_byte_size_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_document_revision_sha256_length"),
        sa.CheckConstraint(
            "length(trim(media_type)) > 0 AND length(trim(storage_key)) > 0 "
            "AND length(trim(original_filename)) > 0",
            name="ck_document_revision_required_text",
        ),
        sa.CheckConstraint("status IN ('draft','published')", name="ck_document_revision_status"),
        sa.CheckConstraint(
            "(status != 'draft' OR published_at IS NULL) AND "
            "(status != 'published' OR published_at IS NOT NULL)",
            name="ck_document_revision_publication",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            name="fk_document_revisions_org_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "document_id",
            "revision_number",
            name="uq_document_revision_number",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_document_revision_org_id"),
    )
    op.create_index(
        "ix_document_revisions_organization_id", "document_revisions", ["organization_id"]
    )
    op.create_index(
        "ix_document_revisions_org_document",
        "document_revisions",
        ["organization_id", "document_id"],
    )

    op.create_table(
        "document_step_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("step", sa.String(length=40), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "step IN ('receive_security','detection','native_text','ocr','tables',"
            "'segmentation','classification','structured_extraction','indexing',"
            "'consistency','human_review')",
            name="ck_document_step_run_step",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed')",
            name="ck_document_step_run_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_document_step_run_attempt"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_document_step_run_duration",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_document_step_run_failure_code",
        ),
        sa.CheckConstraint(
            "length(trim(pipeline_version)) > 0 AND length(trim(prompt_version)) > 0 "
            "AND length(trim(model_version)) > 0",
            name="ck_document_step_run_versions_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id"],
            ["document_revisions.organization_id", "document_revisions.id"],
            name="fk_document_step_runs_org_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "revision_id",
            "step",
            "pipeline_version",
            "prompt_version",
            "model_version",
            name="uq_document_step_run_idempotence",
        ),
    )
    op.create_index(
        "ix_document_step_runs_organization_id", "document_step_runs", ["organization_id"]
    )
    op.create_index(
        "ix_document_step_runs_org_revision",
        "document_step_runs",
        ["organization_id", "revision_id"],
    )

    op.create_table(
        "source_citations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("x0", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("y0", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("x1", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("y1", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("sheet", sa.String(length=120), nullable=True),
        sa.Column("layer", sa.String(length=120), nullable=True),
        sa.Column("object_id", sa.String(length=120), nullable=True),
        sa.Column("extractor", sa.String(length=120), nullable=False),
        sa.Column("confidence", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("page >= 1", name="ck_source_citation_page"),
        sa.CheckConstraint("char_start >= 0", name="ck_source_citation_char_start"),
        sa.CheckConstraint("char_end > char_start", name="ck_source_citation_char_range"),
        sa.CheckConstraint(
            "x0 >= 0 AND x0 <= 1 AND y0 >= 0 AND y0 <= 1 AND "
            "x1 >= 0 AND x1 <= 1 AND y1 >= 0 AND y1 <= 1 AND "
            "x0 < x1 AND y0 < y1",
            name="ck_source_citation_bbox",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_source_citation_confidence",
        ),
        sa.CheckConstraint(
            "length(trim(extractor)) > 0",
            name="ck_source_citation_extractor_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id"],
            ["document_revisions.organization_id", "document_revisions.id"],
            name="fk_source_citations_org_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "revision_id",
            "id",
            name="uq_source_citation_org_revision_id",
        ),
    )
    op.create_index("ix_source_citations_organization_id", "source_citations", ["organization_id"])
    op.create_index(
        "ix_source_citations_org_revision",
        "source_citations",
        ["organization_id", "revision_id"],
    )

    op.create_table(
        "extraction_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("citation_id", sa.String(length=36), nullable=False),
        sa.Column("schema_name", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_extraction_proposal_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','accepted','corrected','rejected')",
            name="ck_extraction_proposal_status",
        ),
        sa.CheckConstraint(
            "length(trim(schema_name)) > 0 AND length(trim(schema_version)) > 0 "
            "AND length(trim(pipeline_version)) > 0 "
            "AND length(trim(prompt_version)) > 0 "
            "AND length(trim(model_version)) > 0",
            name="ck_extraction_proposal_versions_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id", "citation_id"],
            [
                "source_citations.organization_id",
                "source_citations.revision_id",
                "source_citations.id",
            ],
            name="fk_extraction_proposals_org_revision_citation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_extraction_proposal_org_id"),
    )
    op.create_index(
        "ix_extraction_proposals_organization_id",
        "extraction_proposals",
        ["organization_id"],
    )
    op.create_index(
        "ix_extraction_proposals_org_revision",
        "extraction_proposals",
        ["organization_id", "revision_id"],
    )

    op.create_table(
        "validation_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_value", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("after_value", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('accepted','corrected','rejected')",
            name="ck_validation_decision_value",
        ),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_validation_decision_reason"),
        sa.CheckConstraint(
            "(decision != 'corrected' OR (before_value IS NOT NULL AND after_value IS NOT NULL)) "
            "AND (decision NOT IN ('accepted','rejected') OR "
            "(before_value IS NULL AND after_value IS NULL))",
            name="ck_validation_decision_correction_payload",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["extraction_proposals.organization_id", "extraction_proposals.id"],
            name="fk_validation_decisions_org_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_validation_decisions_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_validation_decisions_organization_id",
        "validation_decisions",
        ["organization_id"],
    )
    op.create_index(
        "ix_validation_decisions_org_proposal",
        "validation_decisions",
        ["organization_id", "proposal_id"],
    )

    _create_immutability_triggers()


def downgrade() -> None:
    _drop_immutability_triggers()
    op.drop_index("ix_validation_decisions_org_proposal", table_name="validation_decisions")
    op.drop_index("ix_validation_decisions_organization_id", table_name="validation_decisions")
    op.drop_table("validation_decisions")
    op.drop_index("ix_extraction_proposals_org_revision", table_name="extraction_proposals")
    op.drop_index("ix_extraction_proposals_organization_id", table_name="extraction_proposals")
    op.drop_table("extraction_proposals")
    op.drop_index("ix_source_citations_org_revision", table_name="source_citations")
    op.drop_index("ix_source_citations_organization_id", table_name="source_citations")
    op.drop_table("source_citations")
    op.drop_index("ix_document_step_runs_org_revision", table_name="document_step_runs")
    op.drop_index("ix_document_step_runs_organization_id", table_name="document_step_runs")
    op.drop_table("document_step_runs")
    op.drop_index("ix_document_revisions_org_document", table_name="document_revisions")
    op.drop_index("ix_document_revisions_organization_id", table_name="document_revisions")
    op.drop_table("document_revisions")
    op.drop_index("ix_documents_org_project", table_name="documents")
    op.drop_index("ix_documents_organization_id", table_name="documents")
    op.drop_table("documents")
