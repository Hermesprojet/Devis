"""Bornes métier : capacité de stockage, limites, et messages lisibles."""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_domain import bounds
from metreo_domain.bounds import OutOfBoundsError


class TestStorageCapacity:
    def test_no_accepted_value_can_saturate_the_sql_column(self) -> None:
        """L'invariant qui justifie NUMERIC(28, 10)."""
        assert bounds.worst_case_stored_value() < bounds.SQL_MAX_ABS

    def test_the_margin_is_wide_enough_to_absorb_a_bound_being_raised(self) -> None:
        # Six ordres de grandeur : une borne peut être multipliée par mille
        # sans que la précision SQL devienne le facteur limitant.
        ratio = bounds.SQL_MAX_ABS / bounds.worst_case_stored_value()
        assert ratio >= Decimal("1e6")

    def test_every_bound_stays_below_capacity_on_its_own(self) -> None:
        for line in bounds.headroom_report():
            assert line["maximum"] < bounds.SQL_MAX_ABS, line["bound"]

    def test_useful_decimals_never_exceed_what_is_stored(self) -> None:
        """Promettre plus de décimales que la colonne n'en garde serait un piège."""
        for bound in bounds.ALL_BOUNDS:
            assert bound.useful_decimals <= bounds.SQL_SCALE, bound.name

    def test_the_product_of_two_input_maxima_would_overflow(self) -> None:
        """La raison d'être de la borne sur le total.

        Quantité maximale × prix unitaire maximal dépasse la capacité : ce
        n'est pas un oubli, c'est pourquoi le total est borné séparément.
        """
        product = bounds.QUANTITY.maximum * bounds.UNIT_PRICE.maximum
        # SQL_MAX_ABS est une borne exclusive : 10^18 ne tient déjà pas.
        assert product >= bounds.SQL_MAX_ABS
        with pytest.raises(OutOfBoundsError):
            bounds.check_total(product)


class TestLimits:
    @pytest.mark.parametrize("bound", bounds.ALL_BOUNDS, ids=lambda b: b.name)
    def test_the_maximum_itself_is_accepted(self, bound: bounds.Bound) -> None:
        assert bound.check(bound.maximum) == bound.maximum

    @pytest.mark.parametrize("bound", bounds.ALL_BOUNDS, ids=lambda b: b.name)
    def test_one_unit_past_the_maximum_is_refused(self, bound: bounds.Bound) -> None:
        with pytest.raises(OutOfBoundsError):
            bound.check(bound.maximum + Decimal(1))

    @pytest.mark.parametrize("bound", bounds.ALL_BOUNDS, ids=lambda b: b.name)
    def test_the_inclusive_minimum_is_accepted(self, bound: bounds.Bound) -> None:
        if bound.minimum_inclusive:
            assert bound.check(bound.minimum) == bound.minimum

    @pytest.mark.parametrize("bound", bounds.ALL_BOUNDS, ids=lambda b: b.name)
    def test_the_exclusive_minimum_is_refused(self, bound: bounds.Bound) -> None:
        if not bound.minimum_inclusive:
            with pytest.raises(OutOfBoundsError):
                bound.check(bound.minimum)

    def test_a_zero_output_rate_is_refused_because_it_is_a_divisor(self) -> None:
        with pytest.raises(OutOfBoundsError):
            bounds.OUTPUT_RATE.check(Decimal(0))

    def test_a_zero_density_is_refused(self) -> None:
        with pytest.raises(OutOfBoundsError):
            bounds.DENSITY.check(Decimal(0))

    def test_a_negative_quantity_is_refused(self) -> None:
        with pytest.raises(OutOfBoundsError):
            bounds.QUANTITY.check(Decimal(-1))

    def test_a_negative_total_is_accepted_because_a_credit_note_is_legitimate(
        self,
    ) -> None:
        assert bounds.TOTAL.check(Decimal("-1000")) == Decimal("-1000")


class TestReadableFailure:
    def test_the_error_names_the_bound_the_value_and_both_limits(self) -> None:
        with pytest.raises(OutOfBoundsError) as excinfo:
            bounds.QUANTITY.check(Decimal("1e12"), label="quantité du poste 3.2")
        payload = excinfo.value.to_dict()
        assert payload["code"] == "out_of_bounds"
        assert payload["context"]["bound"] == "quantity"
        assert payload["context"]["maximum"] == "1E+9"
        assert "quantité du poste 3.2" in payload["message"]

    def test_the_message_suggests_the_likely_cause(self) -> None:
        """Une virgule déplacée est la cause la plus fréquente : le dire."""
        with pytest.raises(OutOfBoundsError) as excinfo:
            bounds.DENSITY.check(Decimal("1e6"))
        assert "virgule" in excinfo.value.message

    def test_a_density_in_grams_per_cubic_centimetre_is_caught(self) -> None:
        """2,4 g/cm³ saisi tel quel au lieu de 2400 kg/m³ : accepté, hélas.

        Ce cas ne peut pas être attrapé par une borne — 2,4 est une masse
        volumique valide en kg/m³ pour un matériau très léger. Il relève du
        contrôle d'ordre de grandeur, pas de la plage. Le test existe pour
        documenter la limite de cette protection.
        """
        assert bounds.DENSITY.check(Decimal("2.4")) == Decimal("2.4")
