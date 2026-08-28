"""Le transport : couplage kilométrique et masse volumique, dans le domaine.

Bloquant F. Poser `distance_km` sans `rate_per_km` ne produisait aucune erreur :
le moteur calculait les rotations et ignorait le kilométrage, en silence. Un
transport de 20 km facturé comme un transport de 0 km.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_domain.errors import PricingConfigurationError
from metreo_domain.money import Money
from metreo_domain.pricing import ResourceKind, RotationComponent
from metreo_domain.units import Density, Quantity


def _rotation(**overrides: object) -> RotationComponent:
    base: dict[str, object] = {
        "label": "Camion 14 t",
        "payload": Quantity.of(14, "t"),
        "cost_per_rotation": Money("85", "EUR"),
        "kind": ResourceKind.TRANSPORT,
    }
    return RotationComponent(**{**base, **overrides})  # type: ignore[arg-type]


class TestKilometreCoupling:
    def test_a_distance_without_a_rate_is_refused(self) -> None:
        with pytest.raises(PricingConfigurationError) as excinfo:
            _rotation(distance_km=Decimal("18"))
        assert "rate_per_km" in excinfo.value.message

    def test_a_rate_without_a_distance_is_refused(self) -> None:
        with pytest.raises(PricingConfigurationError) as excinfo:
            _rotation(rate_per_km=Money("1.20", "EUR"))
        assert "distance_km" in excinfo.value.message

    def test_both_together_are_accepted_and_reach_the_amount(self) -> None:
        component = _rotation(distance_km=Decimal("18"), rate_per_km=Money("1.20", "EUR"))
        result = component.compute(Quantity.of(28, "t"), "EUR")
        # 28 t ÷ 14 t = 2 rotations ; (85 + 18 × 1,20) × 2 = 213,20
        assert result.amount.amount == Decimal("213.20")
        assert "km" in (result.formula or ""), result.formula

    def test_neither_is_accepted(self) -> None:
        assert _rotation().compute(Quantity.of(14, "t"), "EUR").amount.amount == Decimal("85")


class TestDensityOnRotations:
    """Le cas central du terrassement : bordereau en m³, camion en tonnes."""

    def test_a_cubic_metre_quantity_is_converted_with_a_sourced_density(self) -> None:
        component = _rotation(
            density=Density(
                value_kg_per_m3=Decimal("1800"),
                source="Rapport géotechnique GT-2026-018, p. 12 (essai 3)",
            )
        )
        # 100 m³ × 1 800 kg/m³ = 180 t ; 180 ÷ 14 = 12,86 → 13 rotations × 85
        result = component.compute(Quantity.of(100, "m3"), "EUR")
        assert result.amount.amount == Decimal("1105")
        assert result.density_source is not None
        assert "GT-2026-018" in result.density_source

    def test_without_a_density_the_conversion_is_refused(self) -> None:
        from metreo_domain.errors import AmbiguousConversionError

        with pytest.raises(AmbiguousConversionError):
            _rotation().compute(Quantity.of(100, "m3"), "EUR")
