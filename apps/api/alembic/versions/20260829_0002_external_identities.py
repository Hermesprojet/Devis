"""Identités externes et transactions de connexion.

Deux tables, l'une durable et l'autre éphémère.

``external_identities`` porte le lien entre un compte local et une identité
chez un fournisseur, identifiée par le couple immuable ``(issuer, subject)``.
L'unicité est posée sur ce couple : une identité n'appartient qu'à un compte.

``login_transactions`` garde une demande de connexion entre le départ vers le
fournisseur et le retour du navigateur. Elle est en base et non en mémoire de
processus, sans quoi l'application ne pourrait pas tourner à plus d'une
instance : l'utilisateur part depuis l'une et revient sur l'autre.

Rien n'est supprimé par cette migration, et le retour arrière ne fait que
retirer les deux tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email_at_link", sa.String(255)),
        sa.Column("last_login_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identity_issuer_subject"),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])

    op.create_table(
        "login_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("return_to", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
        sa.Column("login_code", sa.String(64)),
        sa.Column("login_code_expires_at", sa.DateTime()),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id")),
        sa.UniqueConstraint("state", name="uq_login_transaction_state"),
        sa.UniqueConstraint("login_code", name="uq_login_transaction_code"),
    )


def downgrade() -> None:
    op.drop_table("login_transactions")
    op.drop_index("ix_external_identities_user_id", table_name="external_identities")
    op.drop_table("external_identities")
