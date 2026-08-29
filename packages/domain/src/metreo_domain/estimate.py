"""Aggregation of priced lines into an estimate total.

Markups are applied per line (the usual practice for a BPU / bordereau de prix
unitaires, where the client sees a unit price per item) and then summed. A line
without a price is never silently valued at zero: it is reported as a blocker or
as a warning depending on the company rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from .bounds import MAX_LINES_PER_ESTIMATE, check_total
from .errors import PricingConfigurationError
from .money import DEFAULT_ROUNDING, Money, RoundingPolicy, canonical_text, money_sum
from .pricing import (
    Component,
    LinePriceResult,
    MarkupPolicy,
    TaxRate,
    compute_flat_line_price,
    compute_line_price,
)
from .units import Density, Quantity


class MissingPricePolicy(str, Enum):
    """What the company does with an unpriced item at approval time."""

    BLOCK = "block"
    WARN = "warn"


class LineKind(str, Enum):
    """Row type of a bill of quantities."""

    SECTION = "section"
    ITEM = "item"
    OPTION = "option"
    VARIANT = "variant"
    PROVISIONAL = "provisional"

    @property
    def counts_in_base_total(self) -> bool:
        """Options and variants are quoted but excluded from the base total."""
        return self in (LineKind.ITEM, LineKind.PROVISIONAL)


@dataclass(frozen=True, slots=True)
class EstimateLineInput:
    """One row submitted to the engine.

    Exactly one of ``components`` or ``unit_price`` provides the valuation. When
    both are ``None`` the line is unpriced.
    """

    line_id: str
    code: str
    designation: str
    quantity: Quantity
    kind: LineKind = LineKind.ITEM
    components: tuple[Component, ...] | None = None
    unit_price: Money | None = None
    #: Unit the library price is expressed in, when it differs from the line's
    #: unit (price per tonne on a line measured in m3, for instance).
    unit_price_unit_code: str | None = None
    #: Sourced density, required when the two units cross volume and mass.
    unit_price_density: Density | None = None
    markup_override: MarkupPolicy | None = None


@dataclass(frozen=True, slots=True)
class EstimateLineResult:
    line_id: str
    code: str
    designation: str
    kind: LineKind
    quantity: Quantity
    price: LinePriceResult | None
    missing_price: bool
    included_in_total: bool

    def to_dict(self, policy: RoundingPolicy) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "code": self.code,
            "designation": self.designation,
            "kind": self.kind.value,
            "quantity": canonical_text(self.quantity.value),
            "unit": self.quantity.unit.code,
            "missing_price": self.missing_price,
            "included_in_total": self.included_in_total,
            "price": self.price.to_dict(policy) if self.price else None,
        }


@dataclass(frozen=True, slots=True)
class EstimateResult:
    currency: str
    lines: tuple[EstimateLineResult, ...]
    total_direct_cost: Money
    total_cost_price: Money
    total_selling_price_ht: Money
    tax_totals: tuple[tuple[TaxRate, Money], ...]
    missing_price_line_ids: tuple[str, ...]
    options_total_ht: Money
    blocking: bool

    @property
    def total_ttc(self) -> Money:
        return money_sum(
            [self.total_selling_price_ht, *[a for _, a in self.tax_totals]],
            self.currency,
        )

    def displayed_taxable_bases(self, policy: RoundingPolicy) -> dict[str, Decimal]:
        """Base taxable **imprimée** de chaque taux, par code.

        La base d'un taux est la somme des totaux de ligne *tels qu'imprimés*
        des postes qui portent ce taux et qui entrent dans le total. Les mêmes
        règles d'inclusion que le Total HT : une option est chiffrée, elle
        n'est pas taxée dans le total du marché de base.
        """
        bases: dict[str, Decimal] = {tax.code: Decimal(0) for tax, _ in self.tax_totals}
        for line in self.lines:
            if not line.included_in_total or line.price is None:
                continue
            imprime = policy.quantize(line.price.selling_price_ht.amount)
            for tax, _ in line.price.tax_amounts:
                if tax.code in bases:
                    bases[tax.code] += imprime
        return bases

    def to_dict(self, policy: RoundingPolicy = DEFAULT_ROUNDING) -> dict[str, Any]:
        """Le document remis au client s'additionne exactement.

        Convention retenue — celle de la facturation : les nombres imprimés
        côte à côte doivent s'accorder, parce que c'est de tête que le lecteur
        les vérifie.

            Total HT       = somme des totaux de ligne imprimés et inclus
            TVA d'un taux  = arrondi(taux x base taxable IMPRIMÉE de ce taux)
            Total TTC      = Total HT imprimé + TVA imprimées

        Chaque montant restait auparavant le bon arrondi de sa propre valeur
        exacte, et leur mise côte à côte se contredisait : sur le devis de
        démonstration, la somme des lignes valait 99 097,07 pour un Total HT
        imprimé de 99 097,08, et HT + TVA donnaient 119 907,47 pour un TTC
        imprimé de 119 907,46. L'écart croît avec le nombre de postes — mesuré
        jusqu'à 1,29 EUR sur un bordereau de deux cent cinquante lignes.

        Le total imprimé s'écarte donc du total exact, et c'est le prix assumé
        de la cohérence. **Les valeurs brutes ne bougent pas** :
        `total_selling_price_ht_raw` reste la valeur non arrondie, le calcul
        interne et l'empreinte des instantanés s'appuient dessus, et un devis
        gelé reste comparable à lui-même.

        Une chose ne suit pas et ne peut pas suivre : la somme des TVA
        imprimées poste par poste n'égale pas la TVA du pied. La TVA porte sur
        la base d'un taux, pas sur chaque ligne prise isolément — c'est le
        traitement fiscal, et c'est ce que la ligne « TVA 21 % » énonce.
        """
        bases = self.displayed_taxable_bases(policy)
        total_ht = Decimal(0)
        for line in self.lines:
            if line.included_in_total and line.price is not None:
                total_ht += policy.quantize(line.price.selling_price_ht.amount)

        taxes = [
            {
                "code": tax.code,
                "label": tax.label,
                "rate": canonical_text(tax.rate),
                "taxable_base": str(bases[tax.code]),
                "amount": str(policy.quantize(bases[tax.code] * tax.rate)),
            }
            for tax, _ in self.tax_totals
        ]
        total_ttc = total_ht + sum(
            (Decimal(tax["amount"]) for tax in taxes),
            Decimal(0),
        )

        return {
            "currency": self.currency,
            "lines": [line.to_dict(policy) for line in self.lines],
            "total_direct_cost": str(policy.quantize(self.total_direct_cost.amount)),
            "total_cost_price": str(policy.quantize(self.total_cost_price.amount)),
            "total_selling_price_ht": str(total_ht),
            "total_selling_price_ht_raw": canonical_text(self.total_selling_price_ht.amount),
            "options_total_ht": str(policy.quantize(self.options_total_ht.amount)),
            "taxes": taxes,
            "total_ttc": str(total_ttc),
            "total_ttc_raw": canonical_text(self.total_ttc.amount),
            "missing_price_line_ids": list(self.missing_price_line_ids),
            "blocking": self.blocking,
        }


def compute_estimate(
    lines: tuple[EstimateLineInput, ...],
    *,
    currency: str,
    markup: MarkupPolicy,
    taxes: tuple[TaxRate, ...] = (),
    missing_price_policy: MissingPricePolicy = MissingPricePolicy.BLOCK,
) -> EstimateResult:
    """Compute every line then aggregate.

    Section rows carry no price. Options and variants are priced but kept out of
    the base total; their sum is exposed separately as ``options_total_ht``.
    """
    # Avant la boucle : la limite doit couper avant le coût, pas après.
    if len(lines) > MAX_LINES_PER_ESTIMATE:
        raise PricingConfigurationError(
            f"{len(lines)} lignes dans une estimation, maximum {MAX_LINES_PER_ESTIMATE}.",
            count=len(lines),
            maximum=MAX_LINES_PER_ESTIMATE,
        )

    results: list[EstimateLineResult] = []
    missing: list[str] = []

    direct = Money.zero(currency)
    cost = Money.zero(currency)
    selling = Money.zero(currency)
    options = Money.zero(currency)
    tax_accumulator: dict[str, Money] = {tax.code: Money.zero(currency) for tax in taxes}

    for line in lines:
        if line.kind is LineKind.SECTION:
            results.append(
                EstimateLineResult(
                    line_id=line.line_id,
                    code=line.code,
                    designation=line.designation,
                    kind=line.kind,
                    quantity=line.quantity,
                    price=None,
                    missing_price=False,
                    included_in_total=False,
                )
            )
            continue

        effective_markup = line.markup_override or markup
        price: LinePriceResult | None = None
        if line.components:
            price = compute_line_price(
                quantity=line.quantity,
                components=line.components,
                currency=currency,
                markup=effective_markup,
                taxes=taxes,
            )
        elif line.unit_price is not None:
            price = compute_flat_line_price(
                quantity=line.quantity,
                unit_price=line.unit_price,
                currency=currency,
                markup=effective_markup,
                taxes=taxes,
                price_unit_code=line.unit_price_unit_code,
                density=line.unit_price_density,
            )

        is_missing = price is None
        if is_missing:
            missing.append(line.line_id)

        included = price is not None and line.kind.counts_in_base_total
        if price is not None:
            if included:
                direct = direct + price.direct_cost
                cost = cost + price.cost_price
                selling = selling + price.selling_price_ht
                for tax, amount in price.tax_amounts:
                    tax_accumulator[tax.code] = tax_accumulator[tax.code] + amount
            else:
                options = options + price.selling_price_ht

        results.append(
            EstimateLineResult(
                line_id=line.line_id,
                code=line.code,
                designation=line.designation,
                kind=line.kind,
                quantity=line.quantity,
                price=price,
                missing_price=is_missing,
                included_in_total=included,
            )
        )

    # Chaque ligne a déjà été contrôlée par `compute_line_price`. Leur somme,
    # elle, ne l'a pas été : trois lignes acceptables produisent un total qui
    # ne l'est pas. Les taxes sont incluses parce que le TTC est stocké et
    # exporté au même titre que le HT.
    check_total(direct.amount, label="déboursé sec de l'estimation")
    check_total(cost.amount, label="prix de revient de l'estimation")
    check_total(selling.amount, label="prix de vente HT de l'estimation")
    check_total(options.amount, label="total des options")
    for tax in taxes:
        check_total(tax_accumulator[tax.code].amount, label=f"total de {tax.code}")
    check_total(
        selling.amount + sum(tax_accumulator[t.code].amount for t in taxes),
        label="total TTC de l'estimation",
    )

    return EstimateResult(
        currency=currency,
        lines=tuple(results),
        total_direct_cost=direct,
        total_cost_price=cost,
        total_selling_price_ht=selling,
        tax_totals=tuple((tax, tax_accumulator[tax.code]) for tax in taxes),
        missing_price_line_ids=tuple(missing),
        options_total_ht=options,
        blocking=bool(missing) and missing_price_policy is MissingPricePolicy.BLOCK,
    )


def sensitivity(
    base: EstimateResult,
    *,
    recompute: Callable[[Decimal], EstimateResult],
    variations: dict[str, Decimal],
) -> dict[str, dict[str, str]]:
    """Run ``recompute`` for each named variation and report the delta.

    ``recompute`` takes the variation factor and returns an
    :class:`EstimateResult`. Kept generic so the caller decides what the factor
    means (productivity, price index, haul distance, density, contingency).
    """
    report: dict[str, dict[str, str]] = {}
    for name, factor in variations.items():
        result = recompute(factor)
        delta = result.total_selling_price_ht - base.total_selling_price_ht
        report[name] = {
            "factor": canonical_text(factor),
            "total_selling_price_ht": str(
                DEFAULT_ROUNDING.quantize(result.total_selling_price_ht.amount)
            ),
            "delta": str(DEFAULT_ROUNDING.quantize(delta.amount)),
        }
    return report
