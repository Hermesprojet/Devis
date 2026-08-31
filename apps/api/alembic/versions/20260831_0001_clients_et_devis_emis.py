"""Un répertoire de clients, et le devis qu'on leur remet.

Deux tables et une colonne.

`clients` donne au destinataire commercial une existence propre à
l'organisation : une raison sociale, un numéro d'entreprise facultatif, une
adresse de FACTURATION — distincte de celle du chantier —, un contact, et un
état actif/archivé. Jusqu'ici il n'y avait que deux chaînes libres sur le
projet.

`projects.client_id` relie un chantier à une fiche. La colonne est FACULTATIVE
et rien n'est converti : `client_name` et `client_reference` restent tels
quels. Deviner que « Ets Dupont » et « ETS DUPONT SPRL » sont la même
entreprise est une décision commerciale, pas une transformation de schéma —
et une fusion automatique serait irréversible. Les anciens projets restent donc
lisibles, et l'application demande de choisir ou de créer une fiche avant la
première émission.

`issued_quotes` est le devis REMIS : un numéro unique dans l'organisation, une
date d'émission, une validité, et quatre instantanés — organisation, client,
chantier, document — qui figent ce que le papier dit. Le fichier PDF vit sur le
même volume que les pièces de chantier ; la ligne en porte la clé, l'empreinte
et la taille.

`uq_issued_quote_version` interdit d'émettre deux fois la même version : une
correction passe par une nouvelle version, jamais par la réécriture d'un
document déjà transmis.

Le retour arrière retire les deux tables et la colonne. Il détruit donc les
devis émis et les fiches clients — c'est le prix d'un `downgrade`, et il est
dit ici plutôt que découvert.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


#: Les frontières multi-tenant que cette révision pose, et l'action de chacune.
#:
#: Cette table est lue — jamais recopiée — par
#: `apps/api/tests/test_referential_action_drift.py`, qui refuse qu'une action
#: vive d'un seul côté. Chaque composite reflète l'action de la clé simple
#: qu'elle double : sans ce reflet, le résultat d'une suppression dépendrait de
#: l'ordre de création des contraintes (révision `b4f2c7d81a05`).
RELATIONS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("projects", "client_id", "clients", "fk_projects_client_tenant", None),
    (
        "issued_quotes",
        "project_id",
        "projects",
        "fk_issued_quotes_project_tenant",
        "CASCADE",
    ),
    (
        "issued_quotes",
        "estimate_version_id",
        "estimate_versions",
        "fk_issued_quotes_version_tenant",
        "CASCADE",
    ),
    ("issued_quotes", "client_id", "clients", "fk_issued_quotes_client_tenant", None),
)


def _composite(nom: str) -> sa.ForeignKeyConstraint:
    """La contrainte décrite par `RELATIONS`, construite depuis elle.

    La fabriquer plutôt que la recopier : une table de vérité dupliquée cesse
    d'en être une à la première divergence.
    """
    _enfant, colonne, parent, name, action = next(r for r in RELATIONS if r[3] == nom)
    return sa.ForeignKeyConstraint(
        [colonne, "organization_id"],
        [f"{parent}.id", f"{parent}.organization_id"],
        name=name,
        ondelete=action,
    )


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("company_number", sa.String(length=50), nullable=True),
        sa.Column("billing_address", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_client_status"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_clients_id_organization"),
    )
    op.create_index("ix_clients_organization_id", "clients", ["organization_id"])
    op.create_index("ix_clients_org_status", "clients", ["organization_id", "status"])

    with op.batch_alter_table("projects") as lot:
        lot.add_column(sa.Column("client_id", sa.String(length=36), nullable=True))
        lot.create_foreign_key("fk_projects_client", "clients", ["client_id"], ["id"])
        lot.create_foreign_key(
            "fk_projects_client_tenant",
            "clients",
            ["client_id", "organization_id"],
            ["id", "organization_id"],
            ondelete=RELATIONS[0][4],
        )

    # La clé que doublera la frontière multi-tenant du devis émis. Elle
    # n'existait pas : `estimate_versions` n'avait jamais été la CIBLE d'une
    # clé composite, seulement sa source.
    with op.batch_alter_table("estimate_versions") as lot:
        lot.create_unique_constraint(
            "uq_estimate_versions_id_organization", ["id", "organization_id"]
        )

    op.create_table(
        "issued_quotes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("estimate_id", sa.String(length=36), nullable=False),
        sa.Column("estimate_version_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=60), nullable=False),
        sa.Column("sequence_year", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("organization_snapshot", sa.JSON(), nullable=False),
        sa.Column("client_snapshot", sa.JSON(), nullable=False),
        sa.Column("project_snapshot", sa.JSON(), nullable=False),
        sa.Column("document_snapshot", sa.JSON(), nullable=False),
        sa.Column("include_internal_costs", sa.Boolean(), nullable=False),
        sa.Column("pdf_storage_key", sa.String(length=500), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf_byte_size", sa.Integer(), nullable=False),
        sa.Column("issued_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["estimate_version_id"], ["estimate_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
        # Frontières multi-tenant tenues par la base, comme partout ailleurs :
        # un devis émis ne peut désigner un projet, une version ou un client
        # d'une autre organisation.
        _composite("fk_issued_quotes_project_tenant"),
        _composite("fk_issued_quotes_version_tenant"),
        _composite("fk_issued_quotes_client_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_issued_quotes_id_organization"),
        sa.UniqueConstraint("organization_id", "number", name="uq_issued_quote_number"),
        sa.UniqueConstraint("estimate_version_id", name="uq_issued_quote_version"),
    )
    op.create_index("ix_issued_quotes_organization_id", "issued_quotes", ["organization_id"])
    op.create_index("ix_issued_quotes_project_id", "issued_quotes", ["project_id"])
    op.create_index(
        "ix_issued_quotes_org_project", "issued_quotes", ["organization_id", "project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_issued_quotes_org_project", table_name="issued_quotes")
    op.drop_index("ix_issued_quotes_project_id", table_name="issued_quotes")
    op.drop_index("ix_issued_quotes_organization_id", table_name="issued_quotes")
    op.drop_table("issued_quotes")

    with op.batch_alter_table("estimate_versions") as lot:
        lot.drop_constraint("uq_estimate_versions_id_organization", type_="unique")

    with op.batch_alter_table("projects") as lot:
        lot.drop_constraint("fk_projects_client_tenant", type_="foreignkey")
        lot.drop_constraint("fk_projects_client", type_="foreignkey")
        lot.drop_column("client_id")

    op.drop_index("ix_clients_org_status", table_name="clients")
    op.drop_index("ix_clients_organization_id", table_name="clients")
    op.drop_table("clients")
