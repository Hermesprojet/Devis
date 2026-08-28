"""Invariants métier du §6, vérifiés dans le domaine pur.

Les poser dans les schémas Pydantic ne suffit pas : un appel direct au moteur —
recalcul depuis un instantané, script, test — les contournerait.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_domain import bounds
from metreo_domain.errors import PricingConfigurationError
from metreo_domain.estimate import EstimateLineInput, LineKind, compute_estimate
from metreo_domain.money import Money
from metreo_domain.pricing import (
    LumpSumComponent,
    MarkupPolicy,
    ResourceKind,
    compute_line_price,
)
from metreo_domain.units import Quantity


def _component(amount: str = "100") -> LumpSumComponent:
    return LumpSumComponent("Poste", ResourceKind.OTHER, Money(amount, "EUR"))


class TestCardinality:
    def test_a_line_past_the_component_limit_is_refused(self) -> None:
        components = tuple(_component() for _ in range(bounds.MAX_COMPONENTS_PER_LINE + 1))
        with pytest.raises(PricingConfigurationError) as excinfo:
            compute_line_price(
                quantity=Quantity.of(1, "fft"),
                components=components,
                currency="EUR",
                markup=MarkupPolicy(),
            )
        assert "maximum" in excinfo.value.message

    def test_the_limit_itself_is_accepted(self) -> None:
        components = tuple(_component("1") for _ in range(bounds.MAX_COMPONENTS_PER_LINE))
        result = compute_line_price(
            quantity=Quantity.of(1, "fft"),
            components=components,
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert result.direct_cost.amount == Decimal(bounds.MAX_COMPONENTS_PER_LINE)

    def test_an_estimate_past_the_line_limit_is_refused_before_computing(self) -> None:
        """La limite doit couper avant le coût, pas après."""
        lines = tuple(
            EstimateLineInput(
                line_id=f"l{i}",
                code=str(i),
                designation="Poste",
                kind=LineKind.ITEM,
                quantity=Quantity.of(1, "fft"),
                components=(_component("1"),),
            )
            for i in range(bounds.MAX_LINES_PER_ESTIMATE + 1)
        )
        with pytest.raises(PricingConfigurationError):
            compute_estimate(lines, currency="EUR", markup=MarkupPolicy())


class TestLumpSumAndZeroQuantity:
    """Règle métier tranchée : un forfait est compté une fois, quantité ou non.

    Un composant proportionnel à zéro donne zéro — c'est arithmétique. Un
    forfait, lui, représente un montant fixe : l'installation de chantier coûte
    ce qu'elle coûte, que le poste porte 0 ou 1. Le compter zéro fois parce que
    la quantité est nulle ferait disparaître un coût réel du devis.
    """

    def test_a_lump_sum_is_counted_once_even_at_zero_quantity(self) -> None:
        result = compute_line_price(
            quantity=Quantity.of(0, "fft"),
            components=(_component("100"),),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert result.direct_cost.amount == Decimal("100")

    def test_the_unit_price_of_a_zero_quantity_line_is_zero_without_dividing(self) -> None:
        """Le prix unitaire n'est pas calculable : il vaut zéro, sans division."""
        result = compute_line_price(
            quantity=Quantity.of(0, "fft"),
            components=(_component("100"),),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert result.unit_price_ht.amount == Decimal(0)
        assert result.selling_price_ht.amount == Decimal("100")

    def test_a_proportional_component_at_zero_quantity_gives_zero(self) -> None:
        from metreo_domain.pricing import ConsumptionComponent

        result = compute_line_price(
            quantity=Quantity.of(0, "m2"),
            components=(
                ConsumptionComponent(
                    label="Grave",
                    kind=ResourceKind.MATERIAL,
                    consumption=Decimal("0.35"),
                    resource_unit_code="t",
                    unit_price=Money("18", "EUR"),
                ),
            ),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert result.direct_cost.amount == Decimal(0)
