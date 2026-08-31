"""Détruire une organisation devient un acte écrit, autorisé et borné.

**Le défaut, reproduit avant d'être corrigé.** Sur base PostgreSQL jetable, un
`DELETE FROM organizations` portant un devis émis donnait :

    ligne issued_quotes   1 → 0     le devis disparaît sans un mot
    journal d'audit       9 → 0     la trace de l'émission disparaît avec lui
    fichier PDF           présent   et il le reste, octet pour octet

Trois faits, et c'est le troisième qui condamne le montage précédent : une
purge motivée par un effacement **détruisait sa propre preuve tout en
conservant le document du client**. Exactement à l'envers.

La politique tient en une phrase : **rien ne se détruit sans un écrit
préalable qui dit ce qui va être détruit, et qui survit à la destruction.**

**Quatre pièces.**

1. `issued_quotes.organization_id` passe en `RESTRICT`. La dernière cascade
   silencieuse disparaît : `DELETE FROM organizations` échoue bruyamment tant
   qu'un devis émis subsiste.

2. Une table `organization_purges`, **sans clé étrangère**, seule de ce cas
   dans le dépôt. Un registre rattaché à ce qu'il enregistre meurt avec lui.
   Elle ne porte **aucun texte libre** : un code de motif pris dans une liste
   fermée, une référence opaque contrainte par la base, des empreintes et des
   chemins de stockage. Un registre censé prouver une destruction sans garder
   ce qu'elle a effacé ne peut pas offrir une zone où l'on écrit un nom.

3. Une **autorisation d'exécution bornée**, et c'est elle que le déclencheur
   interroge. Une demande seule — `requested` — n'autorise RIEN : une première
   version ouvrait la porte dès l'inscription, et une demande abandonnée la
   laissait ouverte indéfiniment. Seul `executing` autorise, et seulement tant
   que `authorized_until` n'est pas dépassé selon l'horloge de la BASE, jamais
   celle de l'appelant. Abandonnée, expirée, terminée, échouée : rien ne passe.

4. Une table `quote_retention_decisions`. Un nombre d'années seul n'est pas une
   décision, c'est une opinion sans auteur : la durée vient avec sa
   juridiction, sa source, la date où cette source a été consultée, sa date
   d'effet et son validateur. Le dépôt n'en sème AUCUNE — elles viendraient
   d'un droit qu'il ne détient pas. Sans décision, la destruction est refusée,
   et le refus conserve.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a5b6c7d8e9fa"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | None = None
depends_on: str | None = None


REFUS = (
    "issued_quote_conserve: un devis émis ne se détruit qu'au titre d'une purge "
    "inscrite au registre ; sans elle, il reste la trace de ce qui a été transmis"
)

#: Le SEUL statut sous lequel une purge autorise la destruction — et encore,
#: seulement dans sa fenêtre. `requested` n'y est pas : une demande n'est pas
#: une autorisation. `rows_deleted` non plus : il n'y a alors plus rien à
#: détruire, et le laisser ouvert prolongerait un droit sans objet.
EN_EXECUTION = "executing"


def _litteral(texte: str) -> str:
    """Un littéral SQL sûr : les apostrophes françaises se doublent.

    Sans cela le message ci-dessus referme la chaîne au milieu de « qu'au » et
    le DDL est une erreur de syntaxe — mesuré sur la révision précédente.
    """
    return "'" + texte.replace("'", "''") + "'"


def _maintenant(dialecte: str) -> str:
    """L'horloge de la BASE, dans sa syntaxe.

    C'est le point : une fenêtre validée par l'appelant ne prouve rien, il
    suffirait de mentir sur l'heure. PostgreSQL rend un `timestamptz` là où la
    colonne est naïve — d'où la conversion explicite en UTC, sans quoi la
    comparaison décale du fuseau du serveur. SQLite compare des chaînes ISO de
    largeur fixe, ce qui ordonne correctement.
    """
    if dialecte == "sqlite":
        return "datetime('now')"
    return "(now() AT TIME ZONE 'UTC')"


def _table_issued_quotes(action_organisation: str) -> sa.Table:
    """La table telle que SQLite doit la reconstruire.

    Reprise de `e3f4a5b6c7d8` : les clés simples sont anonymes, SQLite ne sait
    pas supprimer ce qu'il ne peut pas nommer, et seule une reconstruction
    complète est sûre. Les index font partie de la définition — les taire les
    effacerait.
    """
    metadonnees = sa.MetaData()
    return sa.Table(
        "issued_quotes",
        metadonnees,
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
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete=action_organisation
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["estimate_version_id"], ["estimate_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
            name="fk_issued_quotes_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["estimate_version_id", "organization_id"],
            ["estimate_versions.id", "estimate_versions.organization_id"],
            name="fk_issued_quotes_version_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "organization_id"],
            ["clients.id", "clients.organization_id"],
            name="fk_issued_quotes_client_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_issued_quotes_id_organization"),
        sa.UniqueConstraint("organization_id", "number", name="uq_issued_quote_number"),
        sa.UniqueConstraint("estimate_version_id", name="uq_issued_quote_version"),
        sa.Index("ix_issued_quotes_organization_id", "organization_id"),
        sa.Index("ix_issued_quotes_project_id", "project_id"),
        sa.Index("ix_issued_quotes_org_project", "organization_id", "project_id"),
    )


#: Le refus posé par `f4a5b6c7d8e9` sur le journal des événements. Recopié
#: parce que la reconstruction SQLite ci-dessous doit reposer ce déclencheur à
#: l'identique, et qu'importer une révision depuis une autre les couplerait.
REFUS_JOURNAL = "quote_event_append_only: un événement de devis ne se modifie ni ne s'efface"


def _reposer_le_lien_a_l_organisation(action: str) -> None:
    """Repose `issued_quotes.organization_id` avec `action`, sur les deux moteurs."""
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        # La reconstruction RENOMME `issued_quotes`. Or le déclencheur SQLite
        # qui protège `quote_events` la nomme dans son `WHEN` : pendant le
        # renommage il désigne une table absente, et le `ALTER TABLE … RENAME`
        # échoue sur « no such table: main.issued_quotes ». Mesuré.
        #
        # Il est donc écarté puis reposé à l'identique. PostgreSQL n'a pas ce
        # problème : la référence vit dans un corps de fonction, résolu à
        # l'exécution, et rien n'y est renommé.
        op.execute("DROP TRIGGER IF EXISTS trg_quote_events_pas_de_suppression")
        # `recreate="always"` : un corps vide serait un no-op silencieux, et
        # l'action resterait celle d'avant — mesuré sur la révision précédente.
        with op.batch_alter_table(
            "issued_quotes",
            copy_from=_table_issued_quotes(action),
            recreate="always",
        ):
            pass
        op.execute(
            "CREATE TRIGGER trg_quote_events_pas_de_suppression "
            "BEFORE DELETE ON quote_events FOR EACH ROW "
            "WHEN EXISTS (SELECT 1 FROM issued_quotes WHERE id = OLD.issued_quote_id) "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS_JOURNAL)}); END"
        )
        return
    op.drop_constraint("issued_quotes_organization_id_fkey", "issued_quotes", type_="foreignkey")
    op.create_foreign_key(
        "issued_quotes_organization_id_fkey",
        "issued_quotes",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete=action,
    )


def _poser_le_declencheur(*, avec_registre: bool) -> None:
    """Le déclencheur de conservation, dans l'une ou l'autre de ses conditions.

    `avec_registre=True` est la condition de cette révision : une purge
    inscrite autorise, tout le reste refuse. `False` restaure celle de
    `e3f4a5b6c7d8` — l'organisation existe-t-elle — pour la descente.
    """
    lien = op.get_bind()
    if avec_registre:
        condition_sqlite = (
            "WHEN NOT EXISTS (SELECT 1 FROM organization_purges "
            "WHERE organization_id = OLD.organization_id "
            f"AND status = {_litteral(EN_EXECUTION)} "
            f"AND authorized_until > {_maintenant('sqlite')}) "
        )
        condition_pg = (
            "    IF NOT EXISTS (SELECT 1 FROM organization_purges\n"
            "                   WHERE organization_id = OLD.organization_id\n"
            f"                     AND status = {_litteral(EN_EXECUTION)}\n"
            f"                     AND authorized_until > {_maintenant('postgresql')}) THEN\n"
        )
    else:
        condition_sqlite = (
            "WHEN EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) "
        )
        condition_pg = (
            "    IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN\n"
        )

    if lien.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_issued_quotes_conservation "
            "BEFORE DELETE ON issued_quotes FOR EACH ROW "
            + condition_sqlite
            + f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        return
    op.execute(
        "CREATE OR REPLACE FUNCTION metreo_conserver_les_devis_emis() RETURNS trigger AS $$\n"
        "BEGIN\n" + condition_pg + f"        RAISE EXCEPTION {_litteral(REFUS)}\n"
        "            USING ERRCODE = 'restrict_violation';\n"
        "    END IF;\n"
        "    RETURN OLD;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;"
    )
    op.execute(
        "CREATE TRIGGER trg_issued_quotes_conservation "
        "BEFORE DELETE ON issued_quotes FOR EACH ROW "
        "EXECUTE FUNCTION metreo_conserver_les_devis_emis()"
    )


def _retirer_le_declencheur() -> None:
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_issued_quotes_conservation")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_issued_quotes_conservation ON issued_quotes")


def upgrade() -> None:
    op.create_table(
        "organization_purges",
        sa.Column("id", sa.String(length=36), nullable=False),
        # Sans ForeignKey, délibérément : le registre doit survivre à ce qu'il
        # enregistre. Voir la note de classe de `OrganizationPurge`.
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.String(length=36), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        # La fenêtre d'exécution. Nulle tant qu'aucune n'est ouverte — et une
        # purge sans fenêtre n'autorise aucune suppression.
        sa.Column("authorized_at", sa.DateTime(), nullable=True),
        sa.Column("authorized_until", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        # Un code, pas une phrase : voir la note de classe.
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("retention_years_applied", sa.Integer(), nullable=False),
        sa.Column("retention_decision_id", sa.String(length=36), nullable=True),
        sa.Column("quote_count", sa.Integer(), nullable=False),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("files_deleted", sa.Integer(), nullable=False),
        sa.Column("files_failed", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'executing', 'rows_deleted', 'completed', 'failed')",
            name="ck_organization_purge_status",
        ),
        sa.CheckConstraint(
            "reason_code IN ('contract_ended', 'subject_request', 'retention_elapsed', "
            "'duplicate_organization', 'demo_reset', 'test_fixture')",
            name="ck_organization_purge_reason",
        ),
        sa.CheckConstraint(
            "(authorized_at IS NULL) = (authorized_until IS NULL)",
            name="ck_organization_purge_window",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organization_purges_org", "organization_purges", ["organization_id"])

    op.create_table(
        "quote_retention_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("years", sa.Integer(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=10), nullable=False),
        sa.Column("source_label", sa.String(length=300), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source_checked_on", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("validated_by", sa.String(length=36), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("years >= 0 AND years <= 100", name="ck_retention_years_range"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["validated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quote_retention_decisions_organization_id",
        "quote_retention_decisions",
        ["organization_id"],
    )
    op.create_index(
        "ix_retention_decisions_org",
        "quote_retention_decisions",
        ["organization_id", "effective_from"],
    )

    _reposer_le_lien_a_l_organisation("RESTRICT")
    # Le déclencheur se repose APRÈS la reconstruction SQLite : `recreate` copie
    # la table, et un déclencheur posé avant serait perdu avec l'ancienne.
    _retirer_le_declencheur()
    _poser_le_declencheur(avec_registre=True)


def downgrade() -> None:
    _reposer_le_lien_a_l_organisation("CASCADE")
    _retirer_le_declencheur()
    _poser_le_declencheur(avec_registre=False)

    op.drop_index("ix_retention_decisions_org", table_name="quote_retention_decisions")
    op.drop_index(
        "ix_quote_retention_decisions_organization_id", table_name="quote_retention_decisions"
    )
    op.drop_table("quote_retention_decisions")
    op.drop_index("ix_organization_purges_org", table_name="organization_purges")
    op.drop_table("organization_purges")
