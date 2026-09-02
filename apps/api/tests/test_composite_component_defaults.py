"""Ce que vaut un composant de sous-détail quand la fiche ne dit rien.

`component_from_spec` traduit une fiche de sous-détail stockée en composant du
moteur de calcul. Trois de ses valeurs par défaut changent directement un prix :

* un taux de perte absent vaut **zéro**, pas « un peu » ;
* une taille d'équipe absente vaut **une** personne ;
* une rotation de camion s'arrondit **vers le haut** — on ne facture pas
  trois quarts de voyage.

Mesuré, sur `main`, par une campagne de mutation : six mutations appliquées à
ce module, **zéro tuée**. Passer le taux de perte par défaut à 0,1, la taille
d'équipe à 2, ou l'arrondi vers le bas laissait la suite complète verte — et
chacun de ces trois changements déplace le prix rendu au client.

Chaque défaut est vérifié avec sa contrepartie explicite. Sans elle, un test
qui constate « 100 » ne distingue pas un défaut correct d'un paramètre
purement ignoré.

Le moteur lui-même — `metreo_domain.pricing` — a ses propres tests ; ce qui
manquait est l'adaptateur qui le nourrit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from metreo_domain.pricing import PricingConfigurationError, ResourceKind
from metreo_domain.units import Quantity

CENT_M3 = Quantity.of("100", "m3")


def _calcule(spec: dict[str, Any], quantite: Quantity = CENT_M3):
    from metreo_api.services.composites import component_from_spec

    composant = component_from_spec(spec, "EUR")
    return composant, composant.compute(quantite, "EUR")


def _consommation(**extra: Any) -> dict[str, Any]:
    return {
        "component_type": "consumption",
        "label": "Sable stabilisé",
        "consumption": "1",
        "resource_unit_code": "m3",
        "unit_price": "10",
        **extra,
    }


def _cadence(**extra: Any) -> dict[str, Any]:
    return {
        "component_type": "output_rate",
        "label": "Pelle hydraulique",
        "output_rate": "10",
        "hourly_rate": "100",
        **extra,
    }


def _rotation(**extra: Any) -> dict[str, Any]:
    return {
        "component_type": "rotation",
        "label": "Camion 30 m³",
        "payload_value": "30",
        "payload_unit_code": "m3",
        "cost_per_rotation": "100",
        **extra,
    }


def test_a_missing_loss_ratio_means_no_loss_at_all() -> None:
    _composant, sans = _calcule(_consommation())
    assert sans.resource_quantity.value == Decimal("100")
    assert sans.amount.amount == Decimal("1000")

    # La contrepartie : le champ est bien lu quand il est donné.
    _composant, avec = _calcule(_consommation(loss_ratio="0.1"))
    assert avec.resource_quantity.value == Decimal("110")
    assert avec.amount.amount == Decimal("1100")


def test_a_missing_crew_size_means_one_person() -> None:
    _composant, seul = _calcule(_cadence())
    assert seul.resource_quantity.value == Decimal("10")
    assert seul.amount.amount == Decimal("1000")

    _composant, en_binome = _calcule(_cadence(crew_size="2"))
    assert en_binome.amount.amount == Decimal("2000"), (
        "doubler l'équipe doit doubler le coût de main-d'œuvre"
    )


def test_rotations_are_rounded_up_by_default() -> None:
    """100 m³ à 30 m³ par voyage font quatre voyages, pas trois et un tiers.

    Sans l'arrondi, le moteur garde la fraction exacte — mesuré :
    3,333… rotations et 333,33 € au lieu de 4 et 400 €. Un camion ne fait pas
    un tiers de voyage, et l'écart est de 66,67 € sur ce seul composant.
    """
    _composant, arrondi = _calcule(_rotation())
    assert arrondi.resource_quantity.value == Decimal("4")
    assert arrondi.amount.amount == Decimal("400")

    _composant, fractionne = _calcule(_rotation(round_up=False))
    assert fractionne.resource_quantity.value != Decimal("4")
    assert fractionne.resource_quantity.value < Decimal("4")
    assert fractionne.amount.amount < Decimal("400")


def test_a_rotation_without_a_stated_kind_is_transport() -> None:
    """La ventilation par nature de coût en dépend, pas le total."""
    composant, _resultat = _calcule(_rotation())
    assert composant.kind is ResourceKind.TRANSPORT

    # Une nature explicite n'est pas écrasée.
    composant, _resultat = _calcule(_rotation(resource_kind="equipment"))
    assert composant.kind is ResourceKind.EQUIPMENT


def test_an_incomplete_specification_is_a_business_error_not_a_crash() -> None:
    """C'est la raison d'être du contrôle en tête de `component_from_spec`.

    Son docstring l'annonce : un instantané gelé ou une ligne historique peut
    apporter une fiche incomplète, et `to_decimal(None)` produisait alors un
    `TypeError` — donc un HTTP 500 — là où la donnée est simplement invalide.
    """
    incomplete = _consommation()
    del incomplete["unit_price"]

    with pytest.raises(PricingConfigurationError) as refus:
        _calcule(incomplete)
    assert "unit_price" in str(refus.value)

    # Contrôle : la même fiche, complète, se calcule.
    _composant, resultat = _calcule(_consommation())
    assert resultat.amount.amount == Decimal("1000")
