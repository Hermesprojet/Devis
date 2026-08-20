"""Les bornes doivent s'appliquer aux montants CALCULÉS, pas aux seules entrées.

Régression P0-1 de la revue indépendante : `check_total` existait et n'était
appelée nulle part. Une quantité et un prix unitaire chacun dans sa plage
produisaient un total de 1,285 × 10^18 — au-dessus de la capacité de
`NUMERIC(28, 10)` — sans qu'aucune couche ne le refuse.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_domain import bounds
from metreo_domain.bounds import OutOfBoundsError
from metreo_domain.estimate import EstimateLineInput, LineKind, compute_estimate
from metreo_domain.money import Money, to_decimal
from metreo_domain.pricing import (
    LumpSumComponent,
    MarkupPolicy,
    ResourceKind,
    compute_flat_line_price,
    compute_line_price,
)
from metreo_domain.units import Quantity


def _lump(amount: str) -> tuple[LumpSumComponent, ...]:
    return (LumpSumComponent("Poste", ResourceKind.OTHER, Money(amount, "EUR")),)


class TestComputedLineAmounts:
    def test_a_line_total_past_the_bound_is_refused(self) -> None:
        """Le cas exact de la revue : prix de bibliothèque × quantité.

        Quantité et prix unitaire sont chacun à leur maximum accepté ; leur
        produit vaut 10^18 et n'a aucun droit d'atteindre une colonne.
        Un forfait ne reproduit pas ce cas : il ne se multiplie pas par la
        quantité.
        """
        with pytest.raises(OutOfBoundsError) as excinfo:
            compute_flat_line_price(
                quantity=Quantity.of(bounds.QUANTITY.maximum, "m3"),
                unit_price=Money(bounds.UNIT_PRICE.maximum, "EUR"),
                currency="EUR",
                markup=MarkupPolicy(),
            )
        assert excinfo.value.code == "out_of_bounds"

    def test_the_exact_maximum_total_is_accepted(self) -> None:
        result = compute_line_price(
            quantity=Quantity.of(1, "fft"),
            components=_lump(str(bounds.TOTAL.maximum)),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert result.selling_price_ht.amount == bounds.TOTAL.maximum

    def test_one_unit_past_the_maximum_total_is_refused(self) -> None:
        with pytest.raises(OutOfBoundsError):
            compute_line_price(
                quantity=Quantity.of(1, "fft"),
                components=_lump(str(bounds.TOTAL.maximum + Decimal(1))),
                currency="EUR",
                markup=MarkupPolicy(),
            )

    def test_markups_can_push_an_acceptable_cost_past_the_bound(self) -> None:
        """Le déboursé tient, la marge le fait sortir : le refus doit venir."""
        with pytest.raises(OutOfBoundsError):
            compute_line_price(
                quantity=Quantity.of(1, "fft"),
                components=_lump(str(bounds.TOTAL.maximum)),
                currency="EUR",
                markup=MarkupPolicy(margin_rate=Decimal("0.10")),
            )


class TestAggregateTotals:
    def test_lines_individually_valid_can_overflow_the_estimate_total(self) -> None:
        """Chaque ligne tient, leur somme non. L'agrégat doit être contrôlé."""
        half = bounds.TOTAL.maximum / 2
        lines = tuple(
            EstimateLineInput(
                line_id=f"l{i}",
                code=f"{i}",
                designation="Poste",
                kind=LineKind.ITEM,
                quantity=Quantity.of(1, "fft"),
                components=_lump(str(half)),
            )
            for i in range(3)
        )
        with pytest.raises(OutOfBoundsError):
            compute_estimate(lines, currency="EUR", markup=MarkupPolicy())


class TestNonFiniteValues:
    @pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN", "sNaN"])
    def test_a_non_finite_decimal_is_refused_at_coercion(self, literal: str) -> None:
        """Infinity et NaN ne sont pas des montants : les refuser au plus tôt.

        Sans cela ils traversent le moteur, franchissent toute comparaison de
        borne — `Decimal('NaN') > x` est faux — et finissent en base.
        """
        with pytest.raises(OutOfBoundsError):
            to_decimal(literal)

    def test_a_non_finite_amount_would_break_a_bound_check(self) -> None:
        """Le piège que ce refus évite, et il est pire que prévu.

        On pourrait croire que `NaN > maximum` rend `False` et laisse donc
        passer la valeur. En réalité la comparaison **lève**
        `InvalidOperation` : sans refus en amont, une valeur non finie
        transforme un contrôle de borne en erreur non métier, donc en HTTP 500.
        """
        from decimal import InvalidOperation

        with pytest.raises(InvalidOperation):
            _ = Decimal("NaN") > bounds.TOTAL.maximum
