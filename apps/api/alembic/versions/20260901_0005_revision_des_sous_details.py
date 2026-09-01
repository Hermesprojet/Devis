"""Un jeton de concurrence sur les sous-détails de prix.

Rendre les sous-détails modifiables depuis l'interface ouvre une question que
la seule lecture ne posait pas : **deux personnes qui éditent le même
sous-détail**. Sans jeton, la seconde écriture écrase la première sans que
personne ne l'apprenne — et un sous-détail porte les rendements et les prix de
ressources de toute une entreprise.

`updated_at` existe déjà et aurait pu servir. Deux réserves l'écartent :

1. il ne bouge pas quand seuls les COMPOSANTS changent, or c'est le cas
   courant — on corrige un rendement, pas le libellé du sous-détail ;
2. deux écritures tombant dans la même graduation d'horloge porteraient le
   même jeton, et la seconde passerait pour la première.

Un entier incrémenté à chaque modification ne laisse aucun de ces doutes.
C'est la seule raison de cette migration : la garantie demandée — refuser
clairement une modification fondée sur une version devenue obsolète — ne se
tient pas sans elle.

Les lignes existantes partent à 1 : elles n'ont jamais été modifiées par ce
chemin, puisque ce chemin n'existait pas.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9fa01"
down_revision: str | None = "a5b6c7d8e9fa"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # En deux temps : la colonne arrive avec un défaut serveur pour que les
    # lignes existantes soient remplies, puis le défaut est retiré. Le laisser
    # ferait diverger le schéma des modèles — `alembic check` le dirait — et
    # surtout il masquerait un oubli d'initialisation dans le code applicatif.
    op.add_column(
        "composite_prices",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    with op.batch_alter_table("composite_prices") as batch:
        batch.alter_column("revision", server_default=None)


def downgrade() -> None:
    op.drop_column("composite_prices", "revision")
