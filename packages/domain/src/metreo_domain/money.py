"""Money and rounding.

Rules enforced here (see ``docs/adr/0004-pricing-engine.md``):

* Amounts are :class:`decimal.Decimal`. Binary floats never touch money.
* Stored values are **unrounded**. Rounding is a presentation/contract step
  applied explicitly through a :class:`RoundingPolicy`.
* Arithmetic between two different currencies raises rather than guessing a
  conversion rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from typing import Final

from .errors import CurrencyMismatchError

#: Working precision for intermediate computations. Wide enough that chained
#: multiplications (quantity x consumption x price x coefficient) do not lose
#: cents, narrow enough to stay deterministic across platforms.
WORKING_PRECISION: Final[int] = 28

ROUNDING_MODES: Final[dict[str, str]] = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
}


def to_decimal(value: object) -> Decimal:
    """Coerce ``value`` to :class:`Decimal` without ever going through float.

    ``float`` inputs are accepted but converted via their repr, which keeps the
    decimal literal the caller wrote instead of the binary artefact.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        return Decimal(value.strip().replace(" ", "").replace(",", "."))
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def canonical_text(value: object) -> str:
    """Render an **unrounded** decimal as text, one value to one spelling.

    ``Decimal`` keeps the scale it was built with, and the storage backends do
    not agree on it: PostgreSQL returns ``NUMERIC(28, 10)`` padded to ten
    decimals — a quantity of 120 comes back as ``Decimal("120.0000000000")``
    and a zero as ``Decimal("0E-10")`` — while SQLite returns the text that was
    written. A plain ``str()`` therefore puts the database's formatting habits
    into snapshots, exports and digests: the same frozen estimate ends up with
    two different SHA-256 depending on where it was read, and ``0E-10`` leaks
    into a printed bill of quantities.

    So every decimal that is **not** quantised by a
    :class:`RoundingPolicy` goes through this function: trailing zeros are
    dropped and the exponent notation ``normalize()`` produces for round
    numbers (``1E+2``) is expanded back to ``100``.

    Quantised money keeps ``str(policy.quantize(...))`` instead: there the
    trailing zeros of ``1000.00`` are the point.
    """
    text = format(normalize_decimal(value), "f")
    return "0" if text in {"-0", "0"} else text


def normalize_decimal(value: object) -> Decimal:
    """The canonical :class:`Decimal` for a value: same number, one scale.

    Applied at the storage boundary (see ``metreo_api.db.Amount``) so that the
    backend's padding never reaches the arithmetic. Without it, PostgreSQL
    returns a 6 % rate as ``Decimal("0.0600000000")`` and every product
    downstream inherits that scale, which then shows up in explanation strings
    and in the frozen digest even though the values are equal.
    """
    decimal_value = to_decimal(value)
    if not decimal_value.is_finite():
        raise ValueError(f"valeur décimale non finie: {decimal_value!r}")
    normalized = decimal_value.normalize()
    _, _, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        # 1E+2 -> 100, rather than letting the exponent reach the output.
        normalized = normalized.quantize(Decimal(1))
    return normalized


@dataclass(frozen=True, slots=True)
class RoundingPolicy:
    """Explicit, auditable rounding configuration.

    ``scale`` is the number of decimals; ``mode`` is a key of
    :data:`ROUNDING_MODES`. ``unit_price_scale`` may differ from the total scale:
    several public clients in Belgium require unit prices at 2 decimals while
    accepting 4 for composite sub-details.
    """

    scale: int = 2
    mode: str = "half_up"
    unit_price_scale: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in ROUNDING_MODES:
            raise ValueError(f"unsupported rounding mode: {self.mode}")
        if self.scale < 0 or self.scale > 12:
            raise ValueError("rounding scale must be between 0 and 12")

    @property
    def _exponent(self) -> Decimal:
        return Decimal(1).scaleb(-self.scale)

    def quantize(self, amount: Decimal) -> Decimal:
        return amount.quantize(self._exponent, rounding=ROUNDING_MODES[self.mode])

    def quantize_unit_price(self, amount: Decimal) -> Decimal:
        scale = self.unit_price_scale if self.unit_price_scale is not None else self.scale
        exponent = Decimal(1).scaleb(-scale)
        return amount.quantize(exponent, rounding=ROUNDING_MODES[self.mode])


DEFAULT_ROUNDING: Final[RoundingPolicy] = RoundingPolicy(scale=2, mode="half_up")


@dataclass(frozen=True, slots=True)
class Money:
    """An unrounded monetary amount tagged with an ISO-4217 currency code."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        code = self.currency.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"invalid ISO-4217 currency code: {self.currency!r}")
        object.__setattr__(self, "currency", code)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal("0"), currency)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                "cannot combine amounts in different currencies",
                left=self.currency,
                right=other.currency,
            )

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            return Money(self.amount - other.amount, self.currency)

    def scaled_by(self, factor: object) -> Money:
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            return Money(self.amount * to_decimal(factor), self.currency)

    def rounded(self, policy: RoundingPolicy = DEFAULT_ROUNDING) -> Money:
        return Money(policy.quantize(self.amount), self.currency)

    def is_zero(self) -> bool:
        return self.amount == 0

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.amount} {self.currency}"


def money_sum(items: list[Money], currency: str) -> Money:
    """Sum a list of :class:`Money`, returning zero in ``currency`` when empty."""
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
