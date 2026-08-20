from decimal import Decimal

import pytest

from metreo_domain.estimate import (
    EstimateLineInput,
    LineKind,
    MissingPricePolicy,
    compute_estimate,
    sensitivity,
)
from metreo_domain.money import DEFAULT_ROUNDING, Money
from metreo_domain.pricing import (
    LumpSumComponent,
    MarkupPolicy,
    OutputRateComponent,
    ResourceKind,
    TaxRate,
)
from metreo_domain.units import Quantity

VAT21 = TaxRate("VAT-BE-21", "TVA 21 %", Decimal("0.21"))
NEUTRAL = MarkupPolicy()


def line(line_id, code, qty, unit, price=None, kind=LineKind.ITEM):
    return EstimateLineInput(
        line_id=line_id,
        code=code,
        designation=code,
        quantity=Quantity.of(qty, unit),
        kind=kind,
        unit_price=Money(price, "EUR") if price is not None else None,
    )


def test_sections_carry_no_price_and_no_total():
    result = compute_estimate(
        (
            line("s1", "1", 0, "fft", kind=LineKind.SECTION),
            line("l1", "1.1", 100, "m2", "10.00"),
        ),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.lines[0].price is None
    assert result.total_selling_price_ht.amount == Decimal("1000.00")


def test_missing_price_blocks_by_default():
    result = compute_estimate(
        (line("l1", "1.1", 100, "m2", "10.00"), line("l2", "1.2", 5, "pce")),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.missing_price_line_ids == ("l2",)
    assert result.blocking is True


def test_missing_price_can_be_a_warning_when_the_company_allows_it():
    result = compute_estimate(
        (line("l1", "1.1", 100, "m2", "10.00"), line("l2", "1.2", 5, "pce")),
        currency="EUR",
        markup=NEUTRAL,
        missing_price_policy=MissingPricePolicy.WARN,
    )
    assert result.missing_price_line_ids == ("l2",)
    assert result.blocking is False


def test_unpriced_line_is_never_valued_at_zero_silently():
    result = compute_estimate((line("l2", "1.2", 5, "pce"),), currency="EUR", markup=NEUTRAL)
    assert result.lines[0].missing_price is True
    assert result.lines[0].included_in_total is False


def test_options_are_priced_but_excluded_from_the_base_total():
    result = compute_estimate(
        (
            line("l1", "1.1", 100, "m2", "10.00"),
            line("opt", "1.2", 50, "m2", "20.00", kind=LineKind.OPTION),
        ),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.total_selling_price_ht.amount == Decimal("1000.00")
    assert result.options_total_ht.amount == Decimal("1000.00")


def test_provisional_quantities_count_in_the_base_total():
    result = compute_estimate(
        (line("l1", "1.1", 10, "m3", "50.00", kind=LineKind.PROVISIONAL),),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.total_selling_price_ht.amount == Decimal("500.00")


def test_tax_totals_are_aggregated_per_rate():
    result = compute_estimate(
        (
            line("l1", "1.1", 100, "m2", "10.00"),
            line("l2", "1.2", 100, "m2", "5.00"),
        ),
        currency="EUR",
        markup=NEUTRAL,
        taxes=(VAT21,),
    )
    assert result.tax_totals[0][1].amount == Decimal("315.0000")
    assert DEFAULT_ROUNDING.quantize(result.total_ttc.amount) == Decimal("1815.00")


def test_per_line_markup_override_is_honoured():
    result = compute_estimate(
        (
            EstimateLineInput(
                line_id="l1",
                code="1.1",
                designation="Sous-traitance",
                quantity=Quantity.of(1, "fft"),
                unit_price=Money("1000", "EUR"),
                markup_override=MarkupPolicy(margin_rate=Decimal("0.05")),
            ),
        ),
        currency="EUR",
        markup=MarkupPolicy(margin_rate=Decimal("0.20")),
    )
    assert result.total_selling_price_ht.amount == Decimal("1050.00")


def test_serialisation_is_stable_and_rounded_once():
    result = compute_estimate(
        (line("l1", "1.1", 3, "m2", "1.005"),), currency="EUR", markup=NEUTRAL
    )
    payload = result.to_dict()
    assert payload["total_selling_price_ht"] == "3.02"
    assert payload["total_selling_price_ht_raw"] == "3.015"


def test_sensitivity_reports_deltas_against_the_base_case():
    def build(rate: Decimal):
        return compute_estimate(
            (
                EstimateLineInput(
                    line_id="l1",
                    code="1.1",
                    designation="Excavation",
                    quantity=Quantity.of(120, "m3"),
                    components=(
                        OutputRateComponent(
                            "Pelle",
                            ResourceKind.EQUIPMENT,
                            rate,
                            Money("90.00", "EUR"),
                        ),
                    ),
                ),
            ),
            currency="EUR",
            markup=NEUTRAL,
        )

    base = build(Decimal("30"))
    report = sensitivity(
        base,
        recompute=build,
        variations={"rendement -20 %": Decimal("24"), "rendement +20 %": Decimal("36")},
    )
    assert report["rendement -20 %"]["delta"] == "90.00"
    assert report["rendement +20 %"]["delta"] == "-60.00"


def test_lump_sum_component_ignores_the_line_quantity():
    result = compute_estimate(
        (
            EstimateLineInput(
                line_id="l1",
                code="0.1",
                designation="Installation de chantier",
                quantity=Quantity.of(1, "fft"),
                components=(
                    LumpSumComponent("Amenée/repli", ResourceKind.OTHER, Money("2500", "EUR")),
                ),
            ),
        ),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.total_selling_price_ht.amount == Decimal("2500")


def test_library_price_unit_mismatch_is_refused_at_estimate_level():
    from metreo_domain.errors import IncompatibleUnitsError
    from metreo_domain.units import Quantity as Q

    bad = EstimateLineInput(
        line_id="l1",
        code="1.1",
        designation="Décapage",
        quantity=Q.of(1450, "m2"),
        unit_price=Money("92.00", "EUR"),
        unit_price_unit_code="h",
    )
    with pytest.raises(IncompatibleUnitsError):
        compute_estimate((bad,), currency="EUR", markup=NEUTRAL)
