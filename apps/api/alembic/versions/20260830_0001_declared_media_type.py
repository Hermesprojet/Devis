"""Le type ANNONCÉ par le client, conservé à part du type détecté.

Une révision porte déjà `media_type` : celui que le serveur a lu dans les
octets, et le seul qui décide. Ce qu'annonçait le client est une autre chose —
une allégation, parfois fausse, parfois révélatrice. Les confondre reviendrait
à faire confiance à l'un des deux ; les séparer permet de constater plus tard
qu'un dépôt annonçait « application/pdf » pour un contenu qui n'en était pas.

Colonne facultative : les révisions déposées avant cette migration n'ont
jamais eu d'annonce à conserver, et `NULL` le dit mieux qu'une valeur
inventée. Rien n'est réécrit, rien n'est supprimé, et le retour arrière se
borne à retirer la colonne.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_revisions",
        sa.Column("declared_media_type", sa.String(120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_revisions", "declared_media_type")
