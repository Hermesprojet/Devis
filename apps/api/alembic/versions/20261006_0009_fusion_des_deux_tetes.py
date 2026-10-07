"""Les deux tranches se rejoignent : une seule tête, et rien d'autre.

Revision ID: f3a4b5c60708
Revises: ('d1e2f3a40506', 'a4b5c6d70809')
Create Date: 2026-10-06

**Pourquoi cette migration existe, et pourquoi elle est vide.**

Deux tranches ont avancé en parallèle, et aucune ne doit rien à l'autre :

- `d1e2f3a40506` ajoute `login_transactions.reauthentication_requested`, pour
  que le constat d'`auth_time` sache si la réauthentification avait été exigée ;
- `e2f3a4b50607` ajoute `plan_calibrations` et élargit l'ancrage d'une citation
  au cas page + boîte, pour qu'une mesure prise sur un PDF ait une provenance.

Les chaîner l'une derrière l'autre aurait été plus simple à écrire, et faux :
la branche placée en second serait devenue immigrable **à elle seule** — sa
propre CI tomberait sur une révision absente de son arbre — et l'ordre de
fusion des deux demandes de fusion aurait cessé d'être libre, sans qu'aucune
contrainte de schéma le justifie.

Elles arrivent donc avec **deux têtes**, ce que `scripts/schema_drift_gate.py`
refuse — à juste titre : deux têtes laissent l'ordre d'application indéterminé,
et deux déploiements pourraient appliquer le même schéma dans deux ordres
différents. La réponse prévue par Alembic pour ce cas est une migration de
FUSION : elle déclare les deux parents, ne touche à rien, et rend l'ordre
déterminé.

**Elle n'a donc ni `upgrade` ni `downgrade` à écrire**, et c'est une propriété,
pas un oubli : les deux tranches modifient des tables disjointes — l'une
`login_transactions`, l'autre `plan_calibrations` et `source_citations` — donc
leur rencontre ne demande aucune réconciliation. Le jour où deux tranches
toucheraient la même colonne, une fusion vide serait un mensonge, et ce serait
ici qu'il faudrait écrire la réconciliation.

Vérifié sur un vrai PostgreSQL 16 + PostGIS : une seule tête, montée, descente
et remontée complètes, et la porte de dérive ne propose aucune opération.
"""

from __future__ import annotations

revision = "f3a4b5c60708"
#: Un TUPLE, et c'est toute la migration : il dit à Alembic que ces deux
#: révisions se rejoignent ici, et que la suite de l'arbre part d'un seul point.
#:
#: **Le second parent a changé le 7 octobre 2026**, et il devait changer. La
#: tranche des plans a reçu une révision de plus — `a4b5c6d70809`, qui ouvre à
#: une purge autorisée la suppression d'une révision publiée. Elle descend de
#: `e2f3a4b50607`, qui a donc cessé d'être une tête. Continuer à citer
#: `e2f3a4b50607` ici aurait laissé `a4b5c6d70809` à part, et la base serait
#: repartie à deux têtes — exactement ce que cette migration existe pour
#: empêcher. On cite donc la tête de la tranche, pas un de ses maillons.
down_revision = ("d1e2f3a40506", "a4b5c6d70809")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rien à faire : les deux parents touchent des tables disjointes."""


def downgrade() -> None:
    """Rien à défaire. Redescendre rouvre les deux têtes, ce qui est correct :
    c'est exactement l'état d'avant la fusion."""
