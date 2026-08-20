"""Metreo domain layer.

Pure Python, no framework and no I/O. Everything here is deterministic and is
the single source of truth for money, units and price computation.
"""

from .errors import (
    AmbiguousConversionError,
    CurrencyMismatchError,
    DomainError,
    IncompatibleUnitsError,
    InvalidRateError,
    MissingPriceError,
    PricingConfigurationError,
    UnknownUnitError,
)
from .estimate import (
    EstimateLineInput,
    EstimateLineResult,
    EstimateResult,
    LineKind,
    MissingPricePolicy,
    compute_estimate,
)
from .money import DEFAULT_ROUNDING, Money, RoundingPolicy, money_sum, to_decimal
from .pricing import (
    ComponentResult,
    CompositePrice,
    ConsumptionComponent,
    LinePriceResult,
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
from .units import Density, Dimension, Quantity, Unit, convert, get_unit, known_units

__all__ = [
    "DEFAULT_ROUNDING",
    "AmbiguousConversionError",
    "ComponentResult",
    "CompositePrice",
    "ConsumptionComponent",
    "CurrencyMismatchError",
    "Density",
    "Dimension",
    "DomainError",
    "EstimateLineInput",
    "EstimateLineResult",
    "EstimateResult",
    "IncompatibleUnitsError",
    "InvalidRateError",
    "LineKind",
    "LinePriceResult",
    "LumpSumComponent",
    "MarginMethod",
    "MarkupPolicy",
    "MissingPriceError",
    "MissingPricePolicy",
    "Money",
    "OutputRateComponent",
    "OverheadBase",
    "PricingConfigurationError",
    "Quantity",
    "ResourceKind",
    "RotationComponent",
    "RoundingPolicy",
    "TaxRate",
    "Unit",
    "UnknownUnitError",
    "compute_estimate",
    "compute_flat_line_price",
    "compute_line_price",
    "convert",
    "get_unit",
    "known_units",
    "money_sum",
    "to_decimal",
]

__version__ = "0.1.0"
