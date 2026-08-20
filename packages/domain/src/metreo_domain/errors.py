"""Domain-level exceptions.

Every error carries a machine-readable ``code`` so the API layer can map it to a
stable error contract, and a human message that is safe to display.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain errors."""

    code = "domain_error"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, object] = dict(context)

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "context": self.context}


class UnknownUnitError(DomainError):
    code = "unknown_unit"


class IncompatibleUnitsError(DomainError):
    """The two units do not share a dimension and no bridge rule applies."""

    code = "incompatible_units"


class AmbiguousConversionError(DomainError):
    """A conversion is physically possible but needs a sourced parameter.

    The canonical case is volume <-> mass, which requires a density whose origin
    (document, laboratory report, supplier datasheet) must be recorded.
    """

    code = "ambiguous_conversion"


class CurrencyMismatchError(DomainError):
    code = "currency_mismatch"


class InvalidRateError(DomainError):
    """A rate used as a divisor is zero, negative or missing."""

    code = "invalid_rate"


class MissingPriceError(DomainError):
    code = "missing_price"


class PricingConfigurationError(DomainError):
    code = "pricing_configuration"


class OutOfBoundsError(DomainError):
    """Une valeur sort de la plage acceptée pour son rôle métier.

    Vit ici plutôt que dans ``bounds`` pour que ``money`` puisse la lever sans
    dépendre du module de bornes : un montant non fini est refusé dès sa
    conversion, bien avant qu'une borne ne soit consultée.
    """

    code = "out_of_bounds"


class NonFiniteAmountError(OutOfBoundsError):
    """``Infinity`` ou ``NaN`` là où un montant est attendu.

    Refusé à la conversion, et non plus loin : ``Decimal("NaN") > x`` ne rend
    pas ``False``, il lève ``InvalidOperation``. Une valeur non finie qui entre
    dans le moteur fait donc échouer la première comparaison de borne venue,
    sous la forme d'une exception non métier — et, si elle l'évite, elle
    atteint la base.
    """

    code = "non_finite_amount"
