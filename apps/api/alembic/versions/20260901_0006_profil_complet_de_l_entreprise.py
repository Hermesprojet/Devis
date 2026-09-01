"""L'entreprise émettrice reçoit une adresse, des coordonnées et un logo.

Un devis est un document commercial : il dit qui l'émet, où lui écrire et à
qui téléphoner. Le nôtre n'imprimait que le nom, la raison sociale et le
numéro d'entreprise — de quoi identifier l'émetteur dans un registre, pas de
quoi lui répondre. Ces colonnes ferment ce manque, et rien de plus.

**Aucune valeur n'est fabriquée pour les organisations existantes.** Toutes
les colonnes arrivent NULL. Deviner « Belgique » depuis `country_code`,
ou reprendre l'adresse d'un chantier, écrirait une identité fausse en tête
d'un document qui part chez un client. L'écran demande de compléter ; une
NOUVELLE émission refuse tant que le minimum manque ; les devis déjà émis
portent leur instantané et ne bougent pas.

Le logo est décrit par sept colonnes qui n'ont de sens qu'ensemble, et une
contrainte le dit. La compensation applicative — écrire le fichier, puis la
base, et retirer le fichier si la base refuse — protège le cas courant ; cette
contrainte protège le cas où la compensation elle-même échouerait.

Réversible : `downgrade` retire les colonnes et rend le schéma d'avant. Il ne
retire PAS les fichiers de logo du volume — une migration ne détruit pas des
octets qu'elle n'a pas écrits, et un retour arrière suivi d'un retour avant
retrouverait des fichiers orphelins plutôt que des fichiers perdus.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9fa0102"
down_revision: str | None = "b6c7d8e9fa01"
branch_labels: str | None = None
depends_on: str | None = None

#: (nom, type) — toutes nullables, toutes sans défaut serveur.
COLONNES: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("address", sa.String(length=255)),
    ("address_complement", sa.String(length=255)),
    ("postal_code", sa.String(length=20)),
    ("city", sa.String(length=120)),
    ("email", sa.String(length=255)),
    ("phone", sa.String(length=40)),
    ("website", sa.String(length=255)),
    ("logo_storage_key", sa.String(length=512)),
    ("logo_sha256", sa.String(length=64)),
    ("logo_byte_size", sa.Integer()),
    ("logo_media_type", sa.String(length=120)),
    ("logo_width", sa.Integer()),
    ("logo_height", sa.Integer()),
    ("logo_updated_at", sa.DateTime()),
)

#: Les quatre gardes du logo, nommées comme dans les modèles.
CONTRAINTES: tuple[tuple[str, str], ...] = (
    (
        "ck_organization_logo_complet",
        "(logo_storage_key IS NULL AND logo_sha256 IS NULL AND logo_byte_size IS NULL "
        " AND logo_media_type IS NULL AND logo_width IS NULL AND logo_height IS NULL) "
        "OR (logo_storage_key IS NOT NULL AND logo_sha256 IS NOT NULL "
        " AND logo_byte_size IS NOT NULL AND logo_media_type IS NOT NULL "
        " AND logo_width IS NOT NULL AND logo_height IS NOT NULL)",
    ),
    ("ck_organization_logo_byte_size_positive", "logo_byte_size IS NULL OR logo_byte_size > 0"),
    ("ck_organization_logo_sha256_length", "logo_sha256 IS NULL OR length(logo_sha256) = 64"),
    (
        "ck_organization_logo_dimensions_positive",
        "(logo_width IS NULL OR logo_width > 0) AND (logo_height IS NULL OR logo_height > 0)",
    ),
)


def upgrade() -> None:
    # Les colonnes d'abord, hors lot : `add_column` seul ne reconstruit pas la
    # table sous SQLite, donc rien à recréer derrière.
    for nom, type_ in COLONNES:
        op.add_column("organizations", sa.Column(nom, type_, nullable=True))
    # Les contraintes ensuite, dans un lot : SQLite ne sait pas ajouter une
    # contrainte de contrôle à une table existante autrement qu'en la
    # reconstruisant, et `batch_alter_table` est la façon dont ce dépôt le fait.
    with op.batch_alter_table("organizations") as lot:
        for nom, condition in CONTRAINTES:
            lot.create_check_constraint(nom, condition)


def downgrade() -> None:
    with op.batch_alter_table("organizations") as lot:
        for nom, _ in CONTRAINTES:
            lot.drop_constraint(nom, type_="check")
    for nom, _ in reversed(COLONNES):
        op.drop_column("organizations", nom)
