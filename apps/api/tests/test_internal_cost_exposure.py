"""Ce qu'un lecteur reçoit vraiment quand il demande le chiffrage d'un devis.

`totals_for_display` est une **liste de refus** : elle retire sept clés nommées
du rendu du domaine — `total_direct_cost`, `total_cost_price`, et par ligne
`components`, `cost_by_kind`, `direct_cost`, `cost_price`, `markup_steps`. Tout
champ ajouté ensuite au `to_dict` du domaine part donc **exposé par défaut**.

Mesuré, sur `main` : en ajoutant une clé au rendu d'une ligne, elle arrive chez
un lecteur — `result.lines[].price.purchase_cost_note` — et la suite complète
reste verte, 735 tests. Le modèle de menaces classe pourtant les prix d'achat,
coûts horaires chargés et marges comme « la fuite la plus coûteuse
commercialement ».

Ce fichier ne change pas le masquage : il en fixe la surface. Toute clé qui
apparaîtrait dans le chiffrage rendu à un lecteur sans figurer ci-dessous fait
échouer le test, et il faut alors décider si elle doit être publique ou refusée.

La correction structurelle serait d'inverser `totals_for_display` en liste
d'autorisation. C'est un changement de comportement, et il vous revient.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_estimating import compute, price_the_missing_line

#: Les chemins du chiffrage qu'un rôle SANS `cost:read` peut voir. Un devis
#: doit rester lisible : quantités, prix de vente, taxes, totaux. Rien de ce
#: qui dit ce que le poste a coûté ni comment la marge a été construite.
VISIBLES_SANS_COST_READ: frozenset[str] = frozenset(
    {
        "blocking",
        "currency",
        "lines[].code",
        "lines[].designation",
        "lines[].included_in_total",
        "lines[].kind",
        "lines[].line_id",
        "lines[].missing_price",
        "lines[].price",
        "lines[].price.currency",
        "lines[].price.quantity",
        "lines[].price.selling_price_ht",
        "lines[].price.selling_price_ht_raw",
        "lines[].price.taxes[].amount",
        "lines[].price.taxes[].code",
        "lines[].price.taxes[].label",
        "lines[].price.taxes[].rate",
        "lines[].price.total_ttc",
        "lines[].price.unit",
        "lines[].price.unit_price_ht",
        "lines[].quantity",
        "lines[].unit",
        "options_total_ht",
        "taxes[].amount",
        # La base sur laquelle la TVA est calculée. Elle figure au pied du
        # devis par construction — c'est la somme des lignes imprimées — et un
        # client doit pouvoir la vérifier.
        "taxes[].taxable_base",
        "taxes[].code",
        "taxes[].label",
        "taxes[].rate",
        "total_selling_price_ht",
        "total_selling_price_ht_raw",
        "total_ttc",
        # Pendant brut du TTC, comme `total_selling_price_ht_raw` l'est du HT :
        # un total de vente, jamais un coût interne.
        "total_ttc_raw",
    }
)

#: Familles que le masquage doit retirer. Sans cette liste, le test ci-dessous
#: passerait aussi si le masquage ne retirait qu'une clé anodine.
FAMILLES_PROTEGEES: tuple[str, ...] = (
    "components",
    "cost_by_kind",
    "cost_price",
    "direct_cost",
    "markup_steps",
)


def _chemins(noeud: Any, prefixe: str = "") -> set[str]:
    """Tous les chemins de la structure, listes traversées en entier.

    Ne traverser que le premier élément d'une liste ne prouverait rien : la
    première ligne d'un devis est une section, sans prix, et toute la structure
    de prix resterait inexplorée.
    """
    if isinstance(noeud, dict):
        return {
            chemin
            for cle, valeur in noeud.items()
            for chemin in _chemins(valeur, f"{prefixe}.{cle}" if prefixe else cle)
        }
    if isinstance(noeud, list):
        return {chemin for valeur in noeud for chemin in _chemins(valeur, f"{prefixe}[]")}
    return {prefixe}


@pytest.fixture()
def chiffrages(seeded_client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Le même chiffrage vu par l'administrateur, puis par le lecteur."""
    admin = login(seeded_client, "admin@dubois.demo")
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=admin).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=admin
    ).json()[0]
    price_the_missing_line(seeded_client, admin, estimate)
    return (
        compute(seeded_client, admin, estimate, version),
        compute(seeded_client, lecteur, estimate, version),
    )


def test_a_reader_receives_exactly_the_declared_fields_and_nothing_more(
    chiffrages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _admin, lecteur = chiffrages
    assert lecteur["includes_internal_costs"] is False

    recus = _chemins(lecteur["result"])
    en_trop = recus - VISIBLES_SANS_COST_READ
    assert en_trop == set(), (
        "Le chiffrage rendu à un lecteur porte des champs non déclarés : "
        f"{sorted(en_trop)}. `totals_for_display` retire des clés nommées, donc "
        "tout nouveau champ du rendu du domaine part exposé. Décidez s'il est "
        "public — et ajoutez-le ici — ou s'il doit être retiré."
    )

    manquants = VISIBLES_SANS_COST_READ - recus
    assert manquants == set(), (
        f"Des champs déclarés publics ont disparu du chiffrage : {sorted(manquants)}. "
        "Un devis doit rester lisible sans droit sur les coûts."
    )


def test_the_masking_actually_removes_the_costly_families(
    chiffrages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """Sans ce contrôle, la liste ci-dessus passerait sur un masquage inerte.

    Si `totals_for_display` cessait de masquer, le test précédent échouerait —
    mais on ne saurait pas sur quoi. Celui-ci nomme ce qui doit disparaître.
    """
    admin, lecteur = chiffrages
    assert admin["includes_internal_costs"] is True

    retires = _chemins(admin["result"]) - _chemins(lecteur["result"])
    assert retires, "l'administrateur et le lecteur reçoivent le même chiffrage"

    for famille in FAMILLES_PROTEGEES:
        assert any(famille in chemin for chemin in retires), (
            f"« {famille} » n'est plus retiré au lecteur ; les chemins retirés "
            f"sont {sorted(retires)}"
        )


def test_no_masked_family_survives_anywhere_in_the_reader_payload(
    chiffrages: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """Retirer d'un endroit ne suffit pas si la même donnée reparaît ailleurs."""
    _admin, lecteur = chiffrages
    recus = _chemins(lecteur["result"])
    survivants = [
        chemin for chemin in sorted(recus) for famille in FAMILLES_PROTEGEES if famille in chemin
    ]
    assert survivants == [], f"famille protégée encore visible : {survivants}"
