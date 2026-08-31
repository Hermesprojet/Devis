"""Un devis émis ne disparaît plus par cascade ordinaire.

**Le défaut, reproduit avant d'être corrigé.** Sur une base PostgreSQL jetable,
cinq suppressions ont été jouées, chacune sur son propre devis émis :

    suppression physique du projet    → DEVIS PERDU, PDF ORPHELIN
    suppression de l'estimation       → DEVIS PERDU, PDF ORPHELIN
    suppression de la version gelée   → DEVIS PERDU, PDF ORPHELIN
    suppression du client             → refusée (le chantier la retenait déjà)
    suppression directe du devis      → DEVIS PERDU, PDF ORPHELIN

Quatre fois sur cinq, la ligne disparaissait sans un mot et le PDF restait sur
le volume — un fichier que plus aucune ligne ne désignait, et un document déjà
transmis à un client dont l'entreprise n'avait plus trace. L'audit, lui,
conservait `quote.issued` : le journal affirmait une émission dont l'objet
n'existait plus.

**La correction, en deux temps.**

1. Chantier, estimation et version gelée retiennent en `RESTRICT`, sur la clé
   simple comme sur la composite — les deux, sans quoi le résultat dépendrait
   de l'ordre de déclenchement (voir `test_deletion_determinism.py`).
2. Un déclencheur refuse la suppression DIRECTE d'un devis tant que son
   organisation existe. Sans lui, `DELETE FROM issued_quotes` restait ouvert,
   et aucune clé étrangère ne pouvait s'y opposer.

**Ce qui reste possible, et pourquoi.** Supprimer l'organisation entière
emporte encore ses devis : `organization_id` garde `CASCADE`, et le déclencheur
laisse passer une suppression dont l'organisation est elle-même partie. C'est
la condition posée : la purge d'une organisation reste hors périmètre tant
qu'une politique de conservation et d'effacement n'a pas été décidée. La
rendre impossible ici la déciderait par accident.

**Les suppressions métier ne changent pas.** Un projet se supprime
logiquement (`deleted_at`), une fiche client s'archive : ni l'un ni l'autre
n'atteint la base, et le devis reste lisible dans la liste inter-projets.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | None = None
depends_on: str | None = None


#: Les relations que cette révision REPOSE, avec leur action définitive.
#:
#: `d2e3f4a5b6c7` les a posées en `CASCADE` ; celle-ci les reprend. Le contrôle
#: de dérive lit les révisions dans l'ordre de la chaîne et retient la
#: dernière : c'est celle-là que la base porte.
RELATIONS: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "issued_quotes",
        "project_id",
        "projects",
        "fk_issued_quotes_project_tenant",
        "RESTRICT",
    ),
    (
        "issued_quotes",
        "estimate_version_id",
        "estimate_versions",
        "fk_issued_quotes_version_tenant",
        "RESTRICT",
    ),
)

#: Les clés SIMPLES qui doublent les composites ci-dessus, plus celle de
#: l'estimation, qui n'a pas de composite. Nom PostgreSQL, colonne, parent.
SIMPLES: tuple[tuple[str, str, str], ...] = (
    ("issued_quotes_project_id_fkey", "project_id", "projects"),
    ("issued_quotes_estimate_id_fkey", "estimate_id", "estimates"),
    ("issued_quotes_estimate_version_id_fkey", "estimate_version_id", "estimate_versions"),
)

#: Le message que la base rend quand on tente de supprimer un devis remis. Il
#: porte un code stable : l'API le traduit en 409 plutôt qu'en 500.
REFUS = (
    "issued_quote_conserve: un devis émis ne se supprime pas ; "
    "il reste la trace de ce qui a été transmis au client"
)


def _litteral(texte: str) -> str:
    """Un littéral SQL sûr, apostrophes doublées.

    Ce message n'en porte pas, mais le suivant en portait une — « ne
    s'efface » — et SQLite refusait le déclencheur sur une erreur de
    syntaxe. Une garde ici plutôt qu'un message contraint là-bas.
    """
    return "'" + texte.replace("'", "''") + "'"


def _table_issued_quotes(action: str) -> sa.Table:
    """La table telle que SQLite doit la reconstruire, actions comprises.

    Décrite ici plutôt que réfléchie : les clés simples ont été créées sans
    nom, et SQLite ne sait pas en supprimer une qu'il ne peut pas nommer. Une
    reconstruction complète est le seul geste sûr, et elle exige la définition
    exacte de la table.
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete=action),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"], ondelete=action),
        sa.ForeignKeyConstraint(["estimate_version_id"], ["estimate_versions.id"], ondelete=action),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
            name="fk_issued_quotes_project_tenant",
            ondelete=action,
        ),
        sa.ForeignKeyConstraint(
            ["estimate_version_id", "organization_id"],
            ["estimate_versions.id", "estimate_versions.organization_id"],
            name="fk_issued_quotes_version_tenant",
            ondelete=action,
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
        # Les index font partie de la définition : `copy_from` reconstruit la
        # table à partir d'elle, et une définition qui les tait les EFFACE.
        # Mesuré — après la première version de cette révision, `alembic check`
        # réclamait sous SQLite la recréation des trois index.
        sa.Index("ix_issued_quotes_organization_id", "organization_id"),
        sa.Index("ix_issued_quotes_project_id", "project_id"),
        sa.Index("ix_issued_quotes_org_project", "organization_id", "project_id"),
    )


def _reposer(action: str) -> None:
    """Repose les cinq clés avec `action`, sur l'un ou l'autre moteur."""
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        # SQLite ne modifie pas une contrainte : il faut refaire la table. Les
        # clés simples n'ayant pas de nom, `copy_from` porte la définition
        # complète, et Alembic recopie les lignes.
        # `recreate="always"` : sans lui, un bloc sans opération ne recrée
        # rien du tout et les actions restaient inchangées — mesuré, les clés
        # sortaient encore en `CASCADE` après l'`upgrade`.
        with op.batch_alter_table(
            "issued_quotes",
            copy_from=_table_issued_quotes(action),
            recreate="always",
        ):
            pass
        return

    for nom, colonne, parent in SIMPLES:
        op.drop_constraint(nom, "issued_quotes", type_="foreignkey")
        op.create_foreign_key(nom, "issued_quotes", parent, [colonne], ["id"], ondelete=action)
    for _enfant, colonne, parent, nom, _action in RELATIONS:
        op.drop_constraint(nom, "issued_quotes", type_="foreignkey")
        op.create_foreign_key(
            nom,
            "issued_quotes",
            parent,
            [colonne, "organization_id"],
            ["id", "organization_id"],
            ondelete=action,
        )


def _poser_le_declencheur() -> None:
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_issued_quotes_conservation "
            "BEFORE DELETE ON issued_quotes FOR EACH ROW "
            # La condition, et non un refus sec : une organisation supprimée
            # emporte ses devis, et c'est le seul cas admis.
            "WHEN EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        return
    op.execute(
        "CREATE OR REPLACE FUNCTION metreo_conserver_les_devis_emis() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "    IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN\n"
        f"        RAISE EXCEPTION {_litteral(REFUS)}\n"
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
    op.execute("DROP TRIGGER IF EXISTS trg_issued_quotes_conservation ON issued_quotes")


def upgrade() -> None:
    _reposer("RESTRICT")
    _poser_le_declencheur()


def downgrade() -> None:
    lien = op.get_bind()
    if lien.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_issued_quotes_conservation")
    else:
        _retirer_le_declencheur()
        op.execute("DROP FUNCTION IF EXISTS metreo_conserver_les_devis_emis()")
    _reposer("CASCADE")
