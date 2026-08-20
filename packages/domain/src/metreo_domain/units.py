"""Unit registry and explicit conversions.

Design constraints coming from the product brief:

* Every quantity carries a unit; a bare number is never a quantity.
* Conversions inside a dimension are exact ratios of declared factors.
* Conversions **across** dimensions are refused unless a bridge parameter with a
  recorded source is supplied. Volume <-> mass is the case that matters on site
  (m3 of excavated soil vs tonnes billed by the treatment centre) and it is
  refused without a :class:`Density` carrying its origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from .errors import AmbiguousConversionError, IncompatibleUnitsError, UnknownUnitError
from .money import WORKING_PRECISION, to_decimal


class Dimension(str, Enum):
    """Physical (or commercial) dimension of a unit."""

    COUNT = "count"
    LENGTH = "length"
    AREA = "area"
    VOLUME = "volume"
    MASS = "mass"
    TIME = "time"
    LUMP_SUM = "lump_sum"

    @property
    def label_fr(self) -> str:
        return _DIMENSION_LABELS_FR[self]


_DIMENSION_LABELS_FR: Final[dict[Dimension, str]] = {
    Dimension.COUNT: "nombre",
    Dimension.LENGTH: "longueur",
    Dimension.AREA: "surface",
    Dimension.VOLUME: "volume",
    Dimension.MASS: "masse",
    Dimension.TIME: "durée",
    Dimension.LUMP_SUM: "forfait",
}


@dataclass(frozen=True, slots=True)
class Unit:
    """A unit of measure.

    ``factor_to_base`` expresses the unit in the dimension's base unit
    (m, m2, m3, kg, h, piece, forfait).
    """

    code: str
    dimension: Dimension
    factor_to_base: Decimal
    label_fr: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_to_base", to_decimal(self.factor_to_base))


_UNITS: Final[tuple[Unit, ...]] = (
    # count
    Unit(
        "pce",
        Dimension.COUNT,
        Decimal("1"),
        "pièce",
        ("p", "u", "pc", "st", "stuk", "unite", "unité"),
    ),
    # length
    Unit("m", Dimension.LENGTH, Decimal("1"), "mètre", ("ml", "mct", "m courant", "meter")),
    Unit("cm", Dimension.LENGTH, Decimal("0.01"), "centimètre"),
    Unit("mm", Dimension.LENGTH, Decimal("0.001"), "millimètre"),
    Unit("km", Dimension.LENGTH, Decimal("1000"), "kilomètre"),
    # area
    Unit("m2", Dimension.AREA, Decimal("1"), "mètre carré", ("m²", "m^2", "mc", "sqm")),
    Unit("cm2", Dimension.AREA, Decimal("0.0001"), "centimètre carré", ("cm²",)),
    Unit("ha", Dimension.AREA, Decimal("10000"), "hectare"),
    # volume
    Unit("m3", Dimension.VOLUME, Decimal("1"), "mètre cube", ("m³", "m^3", "cbm", "mcube")),
    Unit("l", Dimension.VOLUME, Decimal("0.001"), "litre", ("lt", "liter", "litre")),
    # mass
    Unit("kg", Dimension.MASS, Decimal("1"), "kilogramme"),
    Unit("t", Dimension.MASS, Decimal("1000"), "tonne", ("tonne", "ton", "tn")),
    # time
    Unit("h", Dimension.TIME, Decimal("1"), "heure", ("hr", "hour", "uur")),
    Unit("min", Dimension.TIME, Decimal(1) / Decimal(60), "minute"),
    Unit("d", Dimension.TIME, Decimal("8"), "jour (8 h)", ("j", "jour", "day")),
    # commercial
    Unit(
        "fft",
        Dimension.LUMP_SUM,
        Decimal("1"),
        "forfait",
        ("ff", "forfait", "ls", "lump sum", "gl"),
    ),
)


def _build_index() -> dict[str, Unit]:
    index: dict[str, Unit] = {}
    for unit in _UNITS:
        for key in (unit.code, *unit.aliases):
            normalised = _normalise(key)
            if normalised in index and index[normalised] is not unit:
                raise RuntimeError(f"duplicate unit alias {key!r}")
            index[normalised] = unit
    return index


def _normalise(raw: str) -> str:
    return raw.strip().lower().replace(".", "").replace(" ", "")


_INDEX: Final[dict[str, Unit]] = _build_index()


def get_unit(code: str) -> Unit:
    """Resolve a unit from its code or one of its aliases.

    Raises :class:`UnknownUnitError` rather than inventing a unit: an unreadable
    unit in an imported bill of quantities must surface as a line error.
    """
    try:
        return _INDEX[_normalise(code)]
    except KeyError as exc:
        raise UnknownUnitError(f"unité inconnue: {code!r}", unit=code) from exc


def known_units() -> tuple[Unit, ...]:
    return _UNITS


@dataclass(frozen=True, slots=True)
class Density:
    """A density with a mandatory, human-readable source.

    ``value_kg_per_m3`` is the only numeric field; ``source`` records where the
    figure comes from (geotechnical report, supplier datasheet, company
    library) and is required so an audited estimate can justify every tonne.
    """

    value_kg_per_m3: Decimal
    source: str
    material_label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_kg_per_m3", to_decimal(self.value_kg_per_m3))
        if self.value_kg_per_m3 <= 0:
            raise ValueError("density must be strictly positive")
        if not self.source or not self.source.strip():
            raise ValueError("density source is mandatory")


@dataclass(frozen=True, slots=True)
class Quantity:
    """A value bound to a unit."""

    value: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))

    @classmethod
    def of(cls, value: object, unit_code: str) -> Quantity:
        return cls(to_decimal(value), get_unit(unit_code))

    @property
    def dimension(self) -> Dimension:
        return self.unit.dimension

    def to_base(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            return self.value * self.unit.factor_to_base

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.value} {self.unit.code}"


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The converted quantity plus the trace of how it was obtained."""

    quantity: Quantity
    explanation: str
    density_used: Density | None = None


def convert(
    quantity: Quantity,
    target_unit_code: str,
    *,
    density: Density | None = None,
) -> ConversionResult:
    """Convert ``quantity`` to ``target_unit_code``.

    Within a dimension the conversion is a ratio of declared factors. Between
    volume and mass a :class:`Density` is required; without it the call raises
    :class:`AmbiguousConversionError` so the UI can ask the user for a sourced
    figure instead of silently assuming one.
    """
    target = get_unit(target_unit_code)
    source = quantity.unit

    if source.dimension is target.dimension:
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            value = quantity.to_base() / target.factor_to_base
        return ConversionResult(
            quantity=Quantity(value, target),
            explanation=(
                f"{quantity.value} {source.code} × {source.factor_to_base}"
                f" ÷ {target.factor_to_base} = {value} {target.code}"
            ),
        )

    bridge = {source.dimension, target.dimension}
    if bridge == {Dimension.VOLUME, Dimension.MASS}:
        if density is None:
            raise AmbiguousConversionError(
                "conversion volume ↔ masse impossible sans masse volumique sourcée",
                from_unit=source.code,
                to_unit=target.code,
                required="density",
            )
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            if source.dimension is Dimension.VOLUME:
                kilograms = quantity.to_base() * density.value_kg_per_m3
                value = kilograms / target.factor_to_base
                formula = (
                    f"{quantity.value} {source.code} → {quantity.to_base()} m³"
                    f" × {density.value_kg_per_m3} kg/m³ = {kilograms} kg"
                    f" → {value} {target.code}"
                )
            else:
                cubic_metres = quantity.to_base() / density.value_kg_per_m3
                value = cubic_metres / target.factor_to_base
                formula = (
                    f"{quantity.value} {source.code} → {quantity.to_base()} kg"
                    f" ÷ {density.value_kg_per_m3} kg/m³ = {cubic_metres} m³"
                    f" → {value} {target.code}"
                )
        return ConversionResult(
            quantity=Quantity(value, target),
            explanation=f"{formula} (masse volumique: {density.source})",
            density_used=density,
        )

    raise IncompatibleUnitsError(
        "conversion impossible entre ces dimensions",
        from_unit=source.code,
        from_dimension=source.dimension.value,
        to_unit=target.code,
        to_dimension=target.dimension.value,
    )
