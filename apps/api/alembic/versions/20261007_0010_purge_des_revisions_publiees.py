"""Une purge autorisée peut détruire une révision publiée. Rien d'autre ne le peut.

Revision ID: a4b5c6d70809
Revises: e2f3a4b50607
Create Date: 2026-10-07

**Le défaut, reproduit avant correction.** Sur une organisation ayant déposé un
seul document, le cycle complet de purge — demander, autoriser, exécuter —
s'arrêtait net :

    sqlite3.IntegrityError: published document revision is immutable
    [SQL: DELETE FROM organizations WHERE organizations.id = ?]

La purge **échouait entièrement**. Pas « laissait des fichiers » : échouait.
Toute organisation ayant déposé un plan, un métré ou n'importe quel document
était donc indestructible, et l'écrit de purge restait en `executing` sans que
rien ne soit détruit.

**Pourquoi.** `20260828_0003` pose un déclencheur d'immuabilité sur
`document_revisions` : une révision PUBLIÉE ne peut être ni modifiée ni
supprimée. La règle est juste et elle doit le rester — c'est elle qui fait
qu'une citation rouverte retrouve le texte qu'elle citait. Mais elle ne
prévoyait aucune exception, et la suppression d'une organisation cascade
jusque-là.

**Ce que cette révision change, et ce qu'elle ne change pas.**

- La **modification** d'une révision publiée reste refusée, toujours, sans
  exception. Une purge détruit ; elle ne réécrit pas.
- La **suppression** devient possible à une seule condition, celle que
  `20260831_0004` a déjà posée pour les devis émis : il existe pour cette
  organisation une purge en `executing` dont `authorized_until` n'est pas
  dépassé, **selon l'horloge de la BASE**. Aucune application ne peut
  contourner cela en mentant sur l'heure.

C'est donc la même porte, gardée par la même clé, que celle qui laisse partir
un devis émis. Deux règles d'immuabilité et une seule exception : la
destruction écrite, autorisée et minutée d'une organisation.

**Ce qui aurait été plus simple et qui serait faux :** retirer le déclencheur
de suppression. Il ne protège pas d'une purge, il protège d'un `DELETE`
ordinaire — celui d'un script de maintenance, d'une migration maladroite ou
d'un appel d'API qui n'aurait pas dû exister. Le retirer pour faire passer la
purge reviendrait à ouvrir la porte à tout le monde pour laisser entrer une
seule personne.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4b5c6d70809"
down_revision: str | Sequence[str] | None = "e2f3a4b50607"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Le message, identique à celui d'avant : les tests qui l'attendent le
#: trouvent, et un exploitant qui le cherche dans les journaux aussi.
REFUS = "published document revision is immutable"

#: L'état d'une purge dont la fenêtre est ouverte. Recopié de
#: `20260831_0004` plutôt qu'importé : une migration ne doit rien devoir à une
#: autre à l'exécution, et une chaîne de quatre caractères n'est pas une règle
#: métier qu'on risque de voir diverger.
EN_EXECUTION = "executing"


def _litteral(valeur: str) -> str:
    return "'" + valeur.replace("'", "''") + "'"


def _maintenant(dialecte: str) -> str:
    return "datetime('now')" if dialecte == "sqlite" else "now()"


def _condition_de_purge(dialecte: str) -> str:
    """« Il n'existe PAS de purge ouverte » — donc on refuse.

    Écrite en négatif parce que c'est ainsi qu'un déclencheur `BEFORE DELETE`
    se lit : il se déclenche pour INTERDIRE, et la condition dit quand.
    """
    return (
        "NOT EXISTS (SELECT 1 FROM organization_purges "
        "WHERE organization_id = OLD.organization_id "
        f"AND status = {_litteral(EN_EXECUTION)} "
        f"AND authorized_until > {_maintenant(dialecte)})"
    )


def upgrade() -> None:
    dialecte = op.get_bind().dialect.name

    if dialecte == "sqlite":
        # SQLite a deux déclencheurs distincts : seul celui de SUPPRESSION
        # change. Celui de modification reste intact, et c'est le point.
        op.execute("DROP TRIGGER IF EXISTS trg_document_revisions_published_delete")
        op.execute(
            "CREATE TRIGGER trg_document_revisions_published_delete "
            "BEFORE DELETE ON document_revisions FOR EACH ROW "
            "WHEN OLD.published_at IS NOT NULL "
            f"AND {_condition_de_purge('sqlite')} "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        return

    if dialecte != "postgresql":  # pragma: no cover - aucun autre dialecte servi
        return

    # PostgreSQL n'avait qu'UN déclencheur pour les deux verbes. Il faut donc
    # les séparer : la modification garde la fonction d'origine, la suppression
    # reçoit la sienne, avec l'exception.
    op.execute("DROP TRIGGER IF EXISTS trg_document_revisions_published_mutation ON document_revisions")
    op.execute(
        "CREATE TRIGGER trg_document_revisions_published_update "
        "BEFORE UPDATE ON document_revisions FOR EACH ROW "
        "EXECUTE FUNCTION metreo_reject_published_revision_mutation()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION metreo_supprimer_une_revision_publiee() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "    IF OLD.published_at IS NOT NULL AND "
        + _condition_de_purge("postgresql")
        + " THEN\n"
        f"        RAISE EXCEPTION {_litteral(REFUS)}\n"
        "            USING ERRCODE = '23514';\n"
        "    END IF;\n"
        "    RETURN OLD;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;"
    )
    op.execute(
        "CREATE TRIGGER trg_document_revisions_published_delete "
        "BEFORE DELETE ON document_revisions FOR EACH ROW "
        "EXECUTE FUNCTION metreo_supprimer_une_revision_publiee()"
    )


def downgrade() -> None:
    """Restaure l'immuabilité sans exception — et redonne donc le défaut.

    Assumé et dit : la descente rend le schéma d'avant, défaut compris. Une
    descente qui « améliorerait » l'état d'arrivée ne serait pas une descente.
    """
    dialecte = op.get_bind().dialect.name

    if dialecte == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_document_revisions_published_delete")
        op.execute(
            "CREATE TRIGGER trg_document_revisions_published_delete "
            "BEFORE DELETE ON document_revisions "
            "WHEN OLD.published_at IS NOT NULL "
            f"BEGIN SELECT RAISE(ABORT, {_litteral(REFUS)}); END"
        )
        return

    if dialecte != "postgresql":  # pragma: no cover
        return

    op.execute("DROP TRIGGER IF EXISTS trg_document_revisions_published_delete ON document_revisions")
    op.execute("DROP TRIGGER IF EXISTS trg_document_revisions_published_update ON document_revisions")
    op.execute("DROP FUNCTION IF EXISTS metreo_supprimer_une_revision_publiee()")
    op.execute(
        "CREATE TRIGGER trg_document_revisions_published_mutation "
        "BEFORE UPDATE OR DELETE ON document_revisions FOR EACH ROW "
        "EXECUTE FUNCTION metreo_reject_published_revision_mutation()"
    )
