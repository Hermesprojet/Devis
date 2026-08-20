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
