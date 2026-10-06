"""L'échelle d'un PDF se déclare, et la déclaration se conserve.

Un PDF ne porte aucune unité de dessin : ses coordonnées sont des points
PostScript, qui décrivent la feuille et ne disent rien de l'ouvrage. Mesurer
dessus demande un nombre que le fichier ne contient pas, et que personne ne
peut deviner — l'échelle.

`plan_calibrations` conserve ce que quelqu'un a DÉCLARÉ : deux points sur la
page, la distance qui les sépare dans la réalité, et l'unité de cette distance.
Le facteur, lui, n'est pas stocké : il se recalcule à l'identique depuis ces
trois champs, et un facteur stocké finirait par diverger de ce dont il est issu.

Deux champs méritent une phrase chacun.

`resolution_du_pointage` enregistre à quelle finesse les deux points ont été
posés, en points PostScript par pixel. C'est lui qui porte toute l'incertitude
des mesures qui en descendront. Mesuré sur quatre plans réels : à la résolution
de l'aperçu pleine page, un pixel vaut 12 à 42 millimètres d'ouvrage ; sur une
vue agrandie, 0,6 à 6 mm. Sans ce champ, on ne saurait plus laquelle des deux a
servi, et toutes les mesures se vaudraient.

`zone_*` borne la page où la calibration s'applique. Une même feuille porte
souvent un plan au 1:50 et un détail au 1:20 : appliquer l'échelle du plan au
détail donnerait une mesure deux fois et demie trop grande, et parfaitement
plausible. `NULL` signifie « toute la page », ce qui est le cas courant.

---

Cette migration élargit aussi `ck_source_citation_ancrage`.

Une citation devait jusqu'ici s'ancrer sur une page ET une plage de caractères,
ou sur un `object_id`. Une mesure prise sur un PDF n'a ni l'un ni l'autre :
elle ne cite aucun texte, elle cite un ENDROIT — une page et une boîte. Il
aurait fallu lui inventer une plage factice ou un faux handle, et les deux
auraient écrit en base une provenance fausse, indiscernable après coup d'une
citation de texte.

L'ancrage accepte donc un troisième cas : page + boîte. La contrainte reste
bâtie uniquement en `IS NULL` / `IS NOT NULL`, pour la raison écrite à côté
d'elle dans `models.py` — une comparaison arithmétique y deviendrait nulle,
donc satisfaite, sur la valeur même qu'elle vise.

Réversible : `downgrade` retire la table et remet la contrainte d'origine. Il
refusera de s'exécuter si des citations ancrées par page + boîte existent —
les détruire silencieusement ferait disparaître des mesures qu'un humain a
peut-être déjà validées.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import metreo_api.db

def montant() -> metreo_api.db.Amount:
    """Le type des dix colonnes numériques de cette table.

    `Amount` est un `TypeDecorator` : NUMERIC(28,10) sur PostgreSQL,
    VARCHAR(64) sur SQLite. Écrire `sa.String(64)` ici — ce que faisait la
    première version de cette migration — donnait donc le bon schéma sur
    SQLite et un schéma FAUX sur PostgreSQL, où les dix colonnes devenaient du
    texte. La porte de dérive l'a relevé : dix `modify_type` d'écart entre le
    modèle et la base migrée.

    Une FONCTION plutôt qu'une constante, comme dans la migration initiale :
    chaque colonne reçoit sa propre instance, et aucune ne peut être affectée
    par ce qu'Alembic ferait à celle d'une voisine.
    """
    return metreo_api.db.Amount(precision=28, scale=10)

revision = "e2f3a4b50607"
#: La tête de CETTE branche, et non celle de la branche de connexion.
#:
#: Les deux tranches sont indépendantes : la calibration d'un plan ne doit rien
#: à `max_age`. Les chaîner l'une derrière l'autre rendrait cette branche
#: immigrable à elle seule — sa CI tomberait sur une révision absente — et
#: imposerait un ordre de fusion qu'aucune des deux ne justifie.
#:
#: Il y aura donc DEUX têtes quand les deux branches se rejoindront, et
#: `scripts/schema_drift_gate.py` les refuse à juste titre : deux têtes
#: laissent l'ordre d'application indéterminé. La réponse est une migration de
#: FUSION, écrite au moment de l'assemblage du candidat, qui déclare
#: `down_revision = ("d1e2f3a40506", "e2f3a4b50607")` et ne fait rien d'autre.
#: C'est le geste prévu par Alembic pour ce cas, et il est écrit ici pour que
#: celui qui assemble n'ait pas à le redécouvrir.
down_revision = "d8e9fa010203"
branch_labels = None
depends_on = None

#: Les deux formes de la contrainte d'ancrage, avant et après.
ANCRAGE_AVANT = (
    "((char_start IS NULL AND char_end IS NULL) "
    "OR (char_start IS NOT NULL AND char_end IS NOT NULL)) "
    "AND ((page IS NOT NULL AND char_start IS NOT NULL) OR object_id IS NOT NULL)"
)
ANCRAGE_APRES = (
    "((char_start IS NULL AND char_end IS NULL) "
    "OR (char_start IS NOT NULL AND char_end IS NOT NULL)) "
    "AND ((page IS NOT NULL AND char_start IS NOT NULL) "
    "OR (page IS NOT NULL AND x0 IS NOT NULL) "
    "OR object_id IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "plan_calibrations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("u0", montant(), nullable=False),
        sa.Column("v0", montant(), nullable=False),
        sa.Column("u1", montant(), nullable=False),
        sa.Column("v1", montant(), nullable=False),
        sa.Column("distance_reelle", montant(), nullable=False),
        sa.Column("unite", sa.String(length=16), nullable=False),
        sa.Column("resolution_du_pointage", montant(), nullable=False),
        sa.Column("zone_x0", montant(), nullable=True),
        sa.Column("zone_y0", montant(), nullable=True),
        sa.Column("zone_x1", montant(), nullable=True),
        sa.Column("zone_y1", montant(), nullable=True),
        sa.Column("motif", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id"],
            ["document_revisions.organization_id", "document_revisions.id"],
            name="fk_plan_calibrations_org_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_plan_calibrations_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_plan_calibration_org_id"),
        sa.CheckConstraint("page >= 1", name="ck_plan_calibration_page"),
        sa.CheckConstraint(
            "CAST(distance_reelle AS NUMERIC) > 0",
            name="ck_plan_calibration_distance",
        ),
        sa.CheckConstraint(
            "CAST(resolution_du_pointage AS NUMERIC) > 0",
            name="ck_plan_calibration_resolution",
        ),
        sa.CheckConstraint("length(trim(unite)) > 0", name="ck_plan_calibration_unite"),
        sa.CheckConstraint(
            "(zone_x0 IS NULL AND zone_y0 IS NULL AND zone_x1 IS NULL AND zone_y1 IS NULL) "
            "OR (zone_x0 IS NOT NULL AND zone_y0 IS NOT NULL "
            "AND zone_x1 IS NOT NULL AND zone_y1 IS NOT NULL)",
            name="ck_plan_calibration_zone_complete",
        ),
    )
    op.create_index(
        "ix_plan_calibrations_org_revision",
        "plan_calibrations",
        ["organization_id", "revision_id"],
    )
    op.create_index(
        "ix_plan_calibrations_organization_id", "plan_calibrations", ["organization_id"]
    )

    # SQLite ne sait pas modifier une contrainte en place : `batch_alter_table`
    # recrée la table et recopie les lignes. Sur PostgreSQL, la même API émet
    # un simple DROP/ADD CONSTRAINT.
    with op.batch_alter_table("source_citations") as lot:
        lot.drop_constraint("ck_source_citation_ancrage", type_="check")
        lot.create_check_constraint("ck_source_citation_ancrage", ANCRAGE_APRES)


def downgrade() -> None:
    # Une citation ancrée par page + boîte ne satisferait plus la contrainte
    # d'origine. La migration REFUSE plutôt que de détruire : ces citations
    # portent des mesures qu'un humain a peut-être déjà validées, et leur
    # suppression silencieuse ferait disparaître son travail.
    restantes = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM source_citations "
                "WHERE page IS NOT NULL AND x0 IS NOT NULL AND char_start IS NULL "
                "AND object_id IS NULL"
            )
        )
        .scalar()
        or 0
    )
    if restantes:
        raise RuntimeError(
            f"{restantes} citation(s) sont ancrées par page et boîte, un ancrage "
            "que la contrainte d'origine ne connaît pas. Les retirer ferait "
            "disparaître des mesures de plan, et les décisions humaines qui s'y "
            "rattachent. Supprimez-les explicitement avant de redescendre."
        )

    with op.batch_alter_table("source_citations") as lot:
        lot.drop_constraint("ck_source_citation_ancrage", type_="check")
        lot.create_check_constraint("ck_source_citation_ancrage", ANCRAGE_AVANT)

    op.drop_index("ix_plan_calibrations_organization_id", table_name="plan_calibrations")
    op.drop_index("ix_plan_calibrations_org_revision", table_name="plan_calibrations")
    op.drop_table("plan_calibrations")
