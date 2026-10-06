"""Une demande de connexion dit si elle a exigé une réauthentification.

Le bouton « utiliser un autre compte » envoyait `prompt=login`, qui **demande**
au fournisseur d'afficher à nouveau son écran. La documentation d'Auth0 est
explicite : ce paramètre ne garantit rien lorsque l'identité vient d'un
fournisseur amont comme Google. Le mécanisme qui l'impose est `max_age`, et
son résultat se constate dans la revendication `auth_time` du jeton d'identité.

Metreo n'envoyait ni l'un ni l'autre : il ne pouvait donc ni demander la
réauthentification, ni constater qu'elle avait eu lieu. Le bouton promettait un
changement de compte qu'il n'était pas en mesure d'obtenir.

**Pourquoi une colonne, et pas seulement une lecture d'`auth_time`.** Au
retour, `auth_time` seule est ininterprétable : une authentification antérieure
à la demande est parfaitement normale quand on n'a rien demandé — c'est le
fonctionnement même d'une session SSO. Sans savoir si la réauthentification
avait été EXIGÉE, le contrôle signalerait un problème sur toutes les
connexions ordinaires, et l'avertissement ne voudrait plus rien dire.

La colonne arrive à `false` pour toutes les transactions existantes, ce qui est
la vérité : aucune d'elles n'a demandé de réauthentification, puisque le départ
ne savait pas le faire. Les transactions en cours au moment de la migration
sont donc traitées exactement comme elles l'ont été au départ.

Réversible : `downgrade` retire la colonne. Aucune donnée métier n'y vit — une
transaction de connexion est éphémère et se reconstruit au prochain départ.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a40506"
down_revision = "c7d8e9fa0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "login_transactions",
        sa.Column(
            "reauthentication_requested",
            sa.Boolean(),
            nullable=False,
            # `server_default` et pas seulement `default` : la valeur doit
            # exister pour les lignes DÉJÀ en base, que l'ORM ne touche pas.
            # Sans elle, l'ajout d'une colonne NOT NULL échoue sur une table
            # non vide — et `login_transactions` l'est dès qu'une connexion
            # est en cours au moment du déploiement.
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("login_transactions", "reauthentication_requested")
