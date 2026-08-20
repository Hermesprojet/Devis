from decimal import Decimal

import pytest

from metreo_domain.errors import (
    AmbiguousConversionError,
    IncompatibleUnitsError,
    UnknownUnitError,
)
from metreo_domain.units import Density, Dimension, Quantity, convert, get_unit

SOURCED_DENSITY = Density(
    value_kg_per_m3=Decimal("1800"),
    source="Rapport géotechnique fictif GT-2024-018, p. 12",
    material_label="Terre de déblai non polluée",
)


@pytest.mark.parametrize(
    "alias,expected",
    [("m³", "m3"), ("M3", "m3"), ("ml", "m"), ("Tonne", "t"), ("stuk", "pce"), ("FF", "fft")],
)
def test_aliases_resolve_to_canonical_units(alias, expected):
    assert get_unit(alias).code == expected


def test_unknown_unit_raises_instead_of_guessing():
    with pytest.raises(UnknownUnitError):
        get_unit("m3/jour")


def test_same_dimension_conversion_is_exact():
    result = convert(Quantity.of("2.5", "km"), "m")
    assert result.quantity.value == Decimal("2500")


def test_volume_to_mass_is_refused_without_density():
    with pytest.raises(AmbiguousConversionError) as excinfo:
        convert(Quantity.of(120, "m3"), "t")
    assert excinfo.value.context["required"] == "density"


def test_volume_to_mass_with_sourced_density_records_its_origin():
    result = convert(Quantity.of(120, "m3"), "t", density=SOURCED_DENSITY)
    assert result.quantity.value == Decimal("216")
    assert result.density_used is SOURCED_DENSITY
    assert "GT-2024-018" in result.explanation


def test_mass_to_volume_is_the_exact_inverse():
    forward = convert(Quantity.of(120, "m3"), "t", density=SOURCED_DENSITY)
    back = convert(forward.quantity, "m3", density=SOURCED_DENSITY)
    assert back.quantity.value == Decimal("120")


def test_density_requires_a_source():
    with pytest.raises(ValueError):
        Density(value_kg_per_m3=Decimal("1800"), source="  ")


def test_density_must_be_positive():
    with pytest.raises(ValueError):
        Density(value_kg_per_m3=Decimal("0"), source="essai")


def test_unrelated_dimensions_are_never_bridged():
    with pytest.raises(IncompatibleUnitsError):
        convert(Quantity.of(10, "m2"), "h")


def test_dimension_labels_are_translated():
    assert Dimension.VOLUME.label_fr == "volume"
