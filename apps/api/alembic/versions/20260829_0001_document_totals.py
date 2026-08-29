"""Les totaux du document, figés au gel.

Le devis remis au client s'additionne : son Total HT est la somme des lignes
imprimées, sa TVA porte sur la base imprimée, son TTC est la somme des deux.
Ces nombres ne coïncident pas avec les totaux bruts déjà stockés, et une
version gelée doit conserver durablement ceux qu'elle portait le jour du gel —
sans quoi une correction ultérieure du moteur réécrirait un devis déjà remis.

Deux colonnes nouvelles plutôt qu'une réinterprétation des anciennes.
`total_selling_price_ht` et `total_ttc` gardent exactement leur sens : les
valeurs **brutes**. Faire porter deux significations à une même colonne selon
la date de la ligne aurait été un piège permanent.

Rétroalimentation des versions déjà gelées
------------------------------------------
Elle est **déterministe et sans recalcul** : l'instantané stocké porte déjà,
ligne par ligne, le total HT tel qu'il fut imprimé. Le total documentaire est
leur somme, et la TVA de chaque taux celle de la base imprimée correspondante.
Aucun moteur n'est rejoué — on ne relit que des nombres immuables.

Une version gelée dont l'instantané ne permet pas cette reconstruction garde
`NULL`. C'est un état explicite : « le nombre imprimé n'est pas connu », jamais
remplacé en silence par l'arrondi du brut. Aucune donnée n'est supprimée, et le
retour arrière ne fait que retirer les deux colonnes.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op

from metreo_api.db import Amount

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "a7e5c04b93f8"
branch_labels = None
depends_on = None

_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}


def _quantize(value: Decimal, rounding: dict) -> Decimal:
    """Reproduit `RoundingPolicy.quantize` sans importer le domaine.

    Une migration ne doit pas dépendre du code applicatif du jour : celui-ci
    évoluera, la migration doit rester rejouable telle quelle.
    """
    scale = int(rounding.get("scale", 2))
    mode = _MODES.get(str(rounding.get("mode", "half_up")), ROUND_HALF_UP)
    return value.quantize(Decimal(1).scaleb(-scale), rounding=mode)


def _document_totals(snapshot: dict, rounding: dict) -> tuple[Decimal, Decimal] | None:
    """Les deux totaux imprimés, relus dans l'instantané. `None` si impossible."""
    result = snapshot.get("result")
    if not isinstance(result, dict):
        return None
    lines = result.get("lines")
    taxes = result.get("taxes")
    if not isinstance(lines, list) or not isinstance(taxes, list):
        return None

    total_ht = Decimal(0)
    bases: dict[str, Decimal] = {}
    try:
        for line in lines:
            if not isinstance(line, dict) or not line.get("included_in_total"):
                continue
            price = line.get("price")
            if not isinstance(price, dict):
                continue
            imprime = Decimal(str(price["selling_price_ht"]))
            total_ht += imprime
            for tax in price.get("taxes") or []:
                code = str(tax["code"])
                bases[code] = bases.get(code, Decimal(0)) + imprime

        total_tva = Decimal(0)
        for tax in taxes:
            code = str(tax["code"])
            taux = Decimal(str(tax["rate"]))
            total_tva += _quantize(bases.get(code, Decimal(0)) * taux, rounding)
    except (KeyError, TypeError, ValueError, ArithmeticError, InvalidOperation):
        # Un instantané d'une forme qu'on ne sait pas lire n'est pas une
        # erreur de migration : la ligne reste NULL, et elle le dit.
        return None

    return total_ht, total_ht + total_tva


def upgrade() -> None:
    # `Amount`, pas `sa.Numeric` : c'est le type que le modèle déclare, et la
    # porte de dérive compare les deux. Un `Numeric` nu passe les tests mais
    # fait proposer une modification de type à chaque `alembic check`.
    op.add_column("estimate_versions", sa.Column("document_total_ht", Amount(28, 10)))
    op.add_column("estimate_versions", sa.Column("document_total_ttc", Amount(28, 10)))

    connexion = op.get_bind()
    lignes = connexion.execute(
        sa.text(
            "SELECT id, snapshot, rounding FROM estimate_versions "
            "WHERE status = 'frozen' AND snapshot IS NOT NULL"
        )
    ).fetchall()

    reconstruites = 0
    for identifiant, snapshot_brut, rounding_brut in lignes:
        snapshot = snapshot_brut if isinstance(snapshot_brut, dict) else _charger(snapshot_brut)
        rounding = rounding_brut if isinstance(rounding_brut, dict) else _charger(rounding_brut)
        if snapshot is None:
            continue
        totaux = _document_totals(snapshot, rounding or {})
        if totaux is None:
            continue
        ht, ttc = totaux
        connexion.execute(
            sa.text(
                "UPDATE estimate_versions "
                "SET document_total_ht = :ht, document_total_ttc = :ttc "
                "WHERE id = :id"
            ),
            {"ht": str(ht), "ttc": str(ttc), "id": identifiant},
        )
        reconstruites += 1

    print(
        f"totaux documentaires : {reconstruites} version(s) gelée(s) reconstruite(s) "
        f"sur {len(lignes)} ; les autres restent NULL et le disent."
    )


def _charger(valeur: object) -> dict | None:
    if valeur is None:
        return None
    if isinstance(valeur, dict):
        return valeur
    try:
        charge = json.loads(valeur)
    except (TypeError, ValueError):
        return None
    return charge if isinstance(charge, dict) else None


def downgrade() -> None:
    # Les deux colonnes disparaissent ; rien d'autre n'est touché, et aucune
    # donnée préexistante n'est perdue.
    op.drop_column("estimate_versions", "document_total_ttc")
    op.drop_column("estimate_versions", "document_total_ht")
