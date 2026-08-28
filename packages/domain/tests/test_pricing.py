"""Acceptance-level tests for the price engine.

Scenario 3 of the product brief ("excavation de terres, 120 m3") is covered end
to end here, including the numbers a reviewer would recompute by hand.
"""

from decimal import Decimal

import pytest

from metreo_domain.errors import InvalidRateError, PricingConfigurationError
from metreo_domain.money import DEFAULT_ROUNDING, Money
from metreo_domain.pricing import (
    CompositePrice,
    ConsumptionComponent,
    LumpSumComponent,
    MarginMethod,
    MarkupPolicy,
    OutputRateComponent,
    OverheadBase,
    ResourceKind,
    RotationComponent,
    TaxRate,
    compute_flat_line_price,
    compute_line_price,
)
from metreo_domain.units import Density, Quantity

DENSITY = Density(Decimal("1800"), "Rapport géotechnique fictif GT-2024-018 p. 12")
VAT21 = TaxRate("VAT-BE-21", "TVA 21 %", Decimal("0.21"), applies_from="1996-01-01")

NEUTRAL = MarkupPolicy()
COMPANY = MarkupPolicy(
    site_overheads_rate=Decimal("0.06"),
    general_overheads_rate=Decimal("0.09"),
    contingency_rate=Decimal("0.03"),
    margin_rate=Decimal("0.08"),
)


def excavation_components():
    return (
        OutputRateComponent(
            label="Pelle hydraulique 21 t (avec opérateur)",
            kind=ResourceKind.EQUIPMENT,
            output_rate=Decimal("30"),
            hourly_rate=Money("90.00", "EUR"),
        ),
        OutputRateComponent(
            label="Manœuvre",
            kind=ResourceKind.LABOR,
            output_rate=Decimal("30"),
            hourly_rate=Money("42.00", "EUR"),
        ),
        ConsumptionComponent(
            label="Traitement en centre agréé",
            kind=ResourceKind.DISPOSAL,
            consumption=Decimal("1"),
            resource_unit_code="t",
            unit_price=Money("12.50", "EUR"),
            convert_boq_quantity=True,
            density=DENSITY,
        ),
    )


def test_output_rate_component_hours_and_cost():
    result = OutputRateComponent(
        "Pelle", ResourceKind.EQUIPMENT, Decimal("30"), Money("90.00", "EUR")
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.resource_quantity.value == Decimal("4")
    assert result.amount.amount == Decimal("360.00")
    assert "120 m3 ÷ 30" in result.formula


def test_output_rate_multiplies_by_crew_size():
    result = OutputRateComponent(
        "Équipe de pose",
        ResourceKind.LABOR,
        Decimal("30"),
        Money("42.00", "EUR"),
        crew_size=Decimal("2"),
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.resource_quantity.value == Decimal("8")
    assert result.amount.amount == Decimal("336.00")


def test_zero_output_rate_is_a_typed_error_not_a_division_crash():
    component = OutputRateComponent(
        "Pelle", ResourceKind.EQUIPMENT, Decimal("0"), Money("90.00", "EUR")
    )
    with pytest.raises(InvalidRateError):
        component.compute(Quantity.of(120, "m3"), "EUR")


def test_negative_output_rate_is_rejected():
    component = OutputRateComponent(
        "Pelle", ResourceKind.EQUIPMENT, Decimal("-5"), Money("90.00", "EUR")
    )
    with pytest.raises(InvalidRateError):
        component.compute(Quantity.of(120, "m3"), "EUR")


def test_material_loss_coefficient_is_applied_and_visible():
    result = ConsumptionComponent(
        label="Sable stabilisé",
        kind=ResourceKind.MATERIAL,
        consumption=Decimal("0.1"),
        resource_unit_code="m3",
        unit_price=Money("38.00", "EUR"),
        loss_ratio=Decimal("0.05"),
    ).compute(Quantity.of(200, "m2"), "EUR")
    assert result.resource_quantity.value == Decimal("21.000")
    assert result.amount.amount == Decimal("798.000")
    assert "(1 + 0.05)" in result.formula


def test_disposal_conversion_carries_the_density_source():
    result = ConsumptionComponent(
        label="Traitement",
        kind=ResourceKind.DISPOSAL,
        consumption=Decimal("1"),
        resource_unit_code="t",
        unit_price=Money("12.50", "EUR"),
        convert_boq_quantity=True,
        density=DENSITY,
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.resource_quantity.value == Decimal("216")
    assert result.amount.amount == Decimal("2700.00")
    assert result.density_source == DENSITY.source


def test_rotations_are_rounded_up_because_half_a_truck_does_not_leave():
    result = RotationComponent(
        label="Camion 8x4",
        payload=Quantity.of(14, "t"),
        cost_per_rotation=Money("38.00", "EUR"),
        density=DENSITY,
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.resource_quantity.value == Decimal("16")
    assert result.amount.amount == Decimal("608.00")


def test_rotations_can_be_billed_pro_rata_when_configured():
    result = RotationComponent(
        label="Camion 8x4",
        payload=Quantity.of(14, "t"),
        cost_per_rotation=Money("38.00", "EUR"),
        density=DENSITY,
        round_up=False,
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.resource_quantity.value < Decimal("16")


def test_distance_component_is_added_per_rotation_and_shown():
    result = RotationComponent(
        label="Camion 8x4",
        payload=Quantity.of(14, "t"),
        cost_per_rotation=Money("38.00", "EUR"),
        density=DENSITY,
        distance_km=Decimal("18"),
        rate_per_km=Money("1.10", "EUR"),
    ).compute(Quantity.of(120, "m3"), "EUR")
    assert result.unit_price.amount == Decimal("57.80")
    assert result.amount.amount == Decimal("924.80")
    assert "18 km" in result.formula


def test_deboursé_sec_is_the_sum_of_direct_resources():
    result = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=NEUTRAL,
    )
    # 360.00 (pelle) + 168.00 (manœuvre) + 2700.00 (traitement)
    assert result.direct_cost.amount == Decimal("3228.00")
    assert result.selling_price_ht.amount == Decimal("3228.00")


def test_markup_chain_matches_a_hand_computation():
    result = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=COMPANY,
    )
    direct = Decimal("3228.00")
    site = direct * Decimal("0.06")
    general = (direct + site) * Decimal("0.09")
    cost_price = direct + site + general
    contingency = cost_price * Decimal("0.03")
    before_margin = cost_price + contingency
    expected = before_margin * Decimal("1.08")

    assert result.cost_price.amount == cost_price
    assert result.selling_price_ht.amount == expected
    assert DEFAULT_ROUNDING.quantize(result.selling_price_ht.amount) == Decimal("4148.84")


def test_every_markup_step_is_reported_even_at_zero_rate():
    result = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert [step.key for step in result.markup_steps] == [
        "site_overheads",
        "general_overheads",
        "contingency",
        "margin",
    ]


def test_margin_on_price_differs_from_margin_on_cost():
    on_cost = compute_line_price(
        quantity=Quantity.of(1, "fft"),
        components=(LumpSumComponent("Base", ResourceKind.OTHER, Money("100.00", "EUR")),),
        currency="EUR",
        markup=MarkupPolicy(margin_rate=Decimal("0.10")),
    )
    on_price = compute_line_price(
        quantity=Quantity.of(1, "fft"),
        components=(LumpSumComponent("Base", ResourceKind.OTHER, Money("100.00", "EUR")),),
        currency="EUR",
        markup=MarkupPolicy(margin_rate=Decimal("0.10"), margin_method=MarginMethod.ON_PRICE),
    )
    assert on_cost.selling_price_ht.amount == Decimal("110.000")
    assert DEFAULT_ROUNDING.quantize(on_price.selling_price_ht.amount) == Decimal("111.11")


def test_margin_on_price_of_100_percent_is_configuration_error():
    with pytest.raises(PricingConfigurationError):
        MarkupPolicy(margin_rate=Decimal("1"), margin_method=MarginMethod.ON_PRICE)


def test_overhead_base_selection_changes_the_result():
    components = (LumpSumComponent("Base", ResourceKind.OTHER, Money("1000", "EUR")),)
    cascaded = compute_line_price(
        quantity=Quantity.of(1, "fft"),
        components=components,
        currency="EUR",
        markup=MarkupPolicy(
            site_overheads_rate=Decimal("0.10"),
            general_overheads_rate=Decimal("0.10"),
            general_overheads_base=OverheadBase.DIRECT_PLUS_SITE,
        ),
    )
    flat = compute_line_price(
        quantity=Quantity.of(1, "fft"),
        components=components,
        currency="EUR",
        markup=MarkupPolicy(
            site_overheads_rate=Decimal("0.10"),
            general_overheads_rate=Decimal("0.10"),
            general_overheads_base=OverheadBase.DIRECT_COST,
        ),
    )
    assert cascaded.cost_price.amount == Decimal("1210.00")
    assert flat.cost_price.amount == Decimal("1200.00")


def test_taxes_are_computed_outside_the_ht_price():
    result = compute_line_price(
        quantity=Quantity.of(1, "fft"),
        components=(LumpSumComponent("Base", ResourceKind.OTHER, Money("100.00", "EUR")),),
        currency="EUR",
        markup=NEUTRAL,
        taxes=(VAT21,),
    )
    assert result.selling_price_ht.amount == Decimal("100.00")
    assert result.tax_amounts[0][1].amount == Decimal("21.0000")
    assert DEFAULT_ROUNDING.quantize(result.total_ttc.amount) == Decimal("121.00")


def test_unit_price_is_derived_from_the_selling_price():
    result = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.unit_price_ht.amount == Decimal("26.90")


def test_zero_quantity_does_not_divide_by_zero():
    result = compute_line_price(
        quantity=Quantity.of(0, "m3"),
        components=(LumpSumComponent("Mobilisation", ResourceKind.OTHER, Money("500", "EUR")),),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.unit_price_ht.amount == Decimal("0")
    assert result.selling_price_ht.amount == Decimal("500")


def test_flat_price_line_behaves_like_a_one_component_sub_detail():
    result = compute_flat_line_price(
        quantity=Quantity.of("12.5", "m2"),
        unit_price=Money("48.20", "EUR"),
        currency="EUR",
        markup=NEUTRAL,
    )
    assert result.direct_cost.amount == Decimal("602.500")


def test_composite_price_is_reusable_across_quantities():
    composite = CompositePrice(
        code="TER-EXC-01",
        label="Excavation en terrain meuble",
        unit_code="m3",
        components=excavation_components(),
    )
    small = composite.compute(Quantity.of(60, "m3"), currency="EUR", markup=NEUTRAL)
    large = composite.compute(Quantity.of(120, "m3"), currency="EUR", markup=NEUTRAL)
    assert large.direct_cost.amount == small.direct_cost.amount * 2


def test_computation_is_reproducible_bit_for_bit():
    first = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=COMPANY,
        taxes=(VAT21,),
    )
    second = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=COMPANY,
        taxes=(VAT21,),
    )
    assert first.to_dict(DEFAULT_ROUNDING) == second.to_dict(DEFAULT_ROUNDING)


def test_cost_by_kind_groups_the_sub_detail():
    result = compute_line_price(
        quantity=Quantity.of(120, "m3"),
        components=excavation_components(),
        currency="EUR",
        markup=NEUTRAL,
    )
    by_kind = result.cost_by_kind()
    assert set(by_kind) == {"equipment", "labor", "disposal"}
    assert by_kind["disposal"].amount == Decimal("2700.00")


def test_flat_price_in_a_different_unit_is_converted_and_traced():
    result = compute_flat_line_price(
        quantity=Quantity.of(2, "km"),
        unit_price=Money("3.50", "EUR"),
        currency="EUR",
        markup=NEUTRAL,
        price_unit_code="m",
    )
    assert result.direct_cost.amount == Decimal("7000.000")
    assert "2 km" in result.components[0].formula


def test_flat_price_across_dimensions_is_refused_without_density():
    from metreo_domain.errors import AmbiguousConversionError

    with pytest.raises(AmbiguousConversionError):
        compute_flat_line_price(
            quantity=Quantity.of(120, "m3"),
            unit_price=Money("12.50", "EUR"),
            currency="EUR",
            markup=NEUTRAL,
            price_unit_code="t",
        )


def test_flat_price_across_dimensions_works_with_a_sourced_density():
    result = compute_flat_line_price(
        quantity=Quantity.of(120, "m3"),
        unit_price=Money("12.50", "EUR"),
        currency="EUR",
        markup=NEUTRAL,
        price_unit_code="t",
        density=DENSITY,
    )
    assert result.direct_cost.amount == Decimal("2700.00")
    assert result.components[0].density_source == DENSITY.source


def test_flat_price_between_incompatible_dimensions_is_refused():
    from metreo_domain.errors import IncompatibleUnitsError

    with pytest.raises(IncompatibleUnitsError):
        compute_flat_line_price(
            quantity=Quantity.of(1450, "m2"),
            unit_price=Money("92.00", "EUR"),
            currency="EUR",
            markup=NEUTRAL,
            price_unit_code="h",
        )
