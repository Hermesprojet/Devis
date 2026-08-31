"""Le cycle commercial d'un devis remis : transmission, consultation, réponse.

Trois tables, et une frontière qui les explique.

`quote_events` est l'HISTOIRE : ce qui est arrivé au devis, dans l'ordre, sans
retour. L'état commercial n'est stocké nulle part — il se déduit de ces
événements et de la date du jour. C'est ce qui rend « Expiré » exact sans tâche
planifiée, et ce qui interdit à un état enregistré de diverger de son propre
journal. Un déclencheur refuse `UPDATE` et `DELETE` : la promesse
« append-only » n'est pas une convention entre gens bien élevés. Une erreur se
corrige par un événement `correction` qui désigne l'original avec un motif
obligatoire.

`quote_share_links` est le LIEN public. Le secret n'y est pas : seule son
empreinte SHA-256. Une copie de la base ne permet donc pas d'ouvrir un devis.

`quote_public_sessions` est la session courte ouverte après échange du secret.
Elle ne porte aucun droit propre : elle désigne un lien, et tout ce que le lien
perd — révocation, expiration — elle le perd au même instant.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | None = None
depends_on: str | None = None


CANAUX = ("public_link", "email", "phone", "meeting", "other")
GENRES = (
    "link_created",
    "link_revoked",
    "transmitted",
    "viewed",
    "accepted",
    "declined",
    "correction",
)

RELATIONS: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "quote_events",
        "issued_quote_id",
        "issued_quotes",
        "fk_quote_events_quote_tenant",
        "CASCADE",
    ),
    (
        "quote_share_links",
        "issued_quote_id",
        "issued_quotes",
        "fk_quote_share_links_quote_tenant",
        "CASCADE",
    ),
    (
        "quote_public_sessions",
        "share_link_id",
        "quote_share_links",
        "fk_quote_public_sessions_link_tenant",
        "CASCADE",
    ),
)

#: Ce que la base rend quand on tente de réécrire l'histoire.
REFUS = "quote_event_append_only: un événement de devis ne se modifie ni ne s'efface"


def _litteral(texte: str) -> str:
    """Un littéral SQL sûr, apostrophes doublées.

    Mesuré : « ne s'efface » fermait la chaîne au milieu du message et
    SQLite refusait le déclencheur sur « near "efface": syntax error ».
    Un message est du texte français ; il en portera d'autres.
    """
    return "'" + texte.replace("'", "''") + "'"


def _composite(nom: str) -> sa.ForeignKeyConstraint:
    _enfant, colonne, parent, name, action = next(r for r in RELATIONS if r[3] == nom)
    return sa.ForeignKeyConstraint(
        [colonne, "organization_id"],
        [f"{parent}.id", f"{parent}.organization_id"],
        name=name,
        ondelete=action,
    )


def _declencheurs_append_only() -> None:
    """Refuse `UPDATE` et `DELETE` sur `quote_events`, sur les deux moteurs.

    La suppression du devis parent reste possible : `CASCADE` la porte, et le
    déclencheur laisse passer une ligne dont le devis est lui-même parti — sans
    quoi la purge d'une organisation, seule suppression admise, deviendrait
    impossible par un chemin détourné.
    """
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_quote_events_pas_de_modification "
            "BEFORE UPDATE ON quote_events FOR EACH ROW "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        op.execute(
            "CREATE TRIGGER trg_quote_events_pas_de_suppression "
            "BEFORE DELETE ON quote_events FOR EACH ROW "
            "WHEN EXISTS (SELECT 1 FROM issued_quotes WHERE id = OLD.issued_quote_id) "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        return

    op.execute(
        "CREATE OR REPLACE FUNCTION metreo_quote_events_append_only() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "    IF TG_OP = 'DELETE' THEN\n"
        "        IF EXISTS (SELECT 1 FROM issued_quotes WHERE id = OLD.issued_quote_id) THEN\n"
        f"            RAISE EXCEPTION {_litteral(REFUS)} USING ERRCODE = 'restrict_violation';\n"
        "        END IF;\n"
        "        RETURN OLD;\n"
        "    END IF;\n"
        f"    RAISE EXCEPTION {_litteral(REFUS)} USING ERRCODE = 'restrict_violation';\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;"
    )
    op.execute(
        "CREATE TRIGGER trg_quote_events_append_only "
        "BEFORE UPDATE OR DELETE ON quote_events FOR EACH ROW "
        "EXECUTE FUNCTION metreo_quote_events_append_only()"
    )


def upgrade() -> None:
    op.create_table(
        "quote_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("issued_quote_id", sa.String(length=36), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("respondent_name", sa.String(length=200), nullable=True),
        sa.Column("respondent_email", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("corrects_event_id", sa.String(length=36), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('" + "','".join(GENRES) + "')", name="ck_quote_event_kind"),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('" + "','".join(CANAUX) + "')",
            name="ck_quote_event_channel",
        ),
        sa.CheckConstraint(
            "(corrects_event_id IS NULL AND correction_reason IS NULL) "
            "OR (corrects_event_id IS NOT NULL AND correction_reason IS NOT NULL "
            "AND kind = 'correction')",
            name="ck_quote_event_correction",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issued_quote_id"], ["issued_quotes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["corrects_event_id"], ["quote_events.id"]),
        _composite("fk_quote_events_quote_tenant"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_events_organization_id", "quote_events", ["organization_id"])
    op.create_index("ix_quote_events_quote", "quote_events", ["issued_quote_id", "recorded_at"])

    op.create_table(
        "quote_share_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("issued_quote_id", sa.String(length=36), nullable=False),
        sa.Column("secret_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issued_quote_id"], ["issued_quotes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"]),
        _composite("fk_quote_share_links_quote_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_quote_share_links_id_organization"),
        sa.UniqueConstraint("secret_sha256", name="uq_quote_share_link_secret"),
    )
    op.create_index(
        "ix_quote_share_links_organization_id", "quote_share_links", ["organization_id"]
    )
    op.create_index("ix_quote_share_links_quote", "quote_share_links", ["issued_quote_id"])

    op.create_table(
        "quote_public_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("share_link_id", sa.String(length=36), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_link_id"], ["quote_share_links.id"], ondelete="CASCADE"),
        _composite("fk_quote_public_sessions_link_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256", name="uq_quote_public_session_token"),
    )
    op.create_index(
        "ix_quote_public_sessions_organization_id", "quote_public_sessions", ["organization_id"]
    )

    _declencheurs_append_only()


def downgrade() -> None:
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_quote_events_pas_de_modification")
        op.execute("DROP TRIGGER IF EXISTS trg_quote_events_pas_de_suppression")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_quote_events_append_only ON quote_events")
        op.execute("DROP FUNCTION IF EXISTS metreo_quote_events_append_only()")

    op.drop_index("ix_quote_public_sessions_organization_id", table_name="quote_public_sessions")
    op.drop_table("quote_public_sessions")
    op.drop_index("ix_quote_share_links_quote", table_name="quote_share_links")
    op.drop_index("ix_quote_share_links_organization_id", table_name="quote_share_links")
    op.drop_table("quote_share_links")
    op.drop_index("ix_quote_events_quote", table_name="quote_events")
    op.drop_index("ix_quote_events_organization_id", table_name="quote_events")
    op.drop_table("quote_events")
