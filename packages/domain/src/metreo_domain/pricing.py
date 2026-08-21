"""Deterministic price computation (étude de prix).

Nothing in this module is probabilistic and nothing calls out to a model. Given
the same inputs it returns the same numbers, and it returns them together with a
human-readable decomposition so a reviewer can re-do the arithmetic by hand.

Vocabulary (FR construction estimating):

``déboursé sec``
    Sum of the direct resources of a line (materials, labour, plant, transport,
    disposal, subcontracting).
``prix de revient``
    Déboursé sec + site overheads (frais de chantier) + general overheads
    (frais généraux), in the configured order.
``prix de vente HT``
    Prix de revient + contingency (aléas) + margin (marge), using the configured
    margin method. Taxes are never folded into this figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any, Protocol

from .bounds import MAX_COMPONENTS_PER_LINE, RATE, check_total
from .errors import InvalidRateError, PricingConfigurationError
from .money import WORKING_PRECISION, Money, RoundingPolicy, canonical_text, money_sum, to_decimal
from .units import Density, Quantity, convert, get_unit


class ResourceKind(str, Enum):
    """Family of a direct resource, used for grouping and for permissions."""

    MATERIAL = "material"
    LABOR = "labor"
    EQUIPMENT = "equipment"
    TRANSPORT = "transport"
    DISPOSAL = "disposal"
    SUBCONTRACT = "subcontract"
    OTHER = "other"

    @property
    def label_fr(self) -> str:
        return _KIND_LABELS_FR[self]


_KIND_LABELS_FR: dict[ResourceKind, str] = {
    ResourceKind.MATERIAL: "Matériaux",
    ResourceKind.LABOR: "Main-d'œuvre",
    ResourceKind.EQUIPMENT: "Engins",
    ResourceKind.TRANSPORT: "Transport",
    ResourceKind.DISPOSAL: "Évacuation / traitement",
    ResourceKind.SUBCONTRACT: "Sous-traitance",
    ResourceKind.OTHER: "Divers",
}


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """One line of a sub-detail, with the arithmetic that produced it."""

    label: str
    kind: ResourceKind
    resource_quantity: Quantity
    unit_price: Money
    amount: Money
    formula: str
    density_source: str | None = None

    def to_dict(self, policy: RoundingPolicy) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind.value,
            "kind_label": self.kind.label_fr,
            "resource_quantity": canonical_text(self.resource_quantity.value),
            "resource_unit": self.resource_quantity.unit.code,
            "unit_price": str(policy.quantize_unit_price(self.unit_price.amount)),
            "amount": str(policy.quantize(self.amount.amount)),
            "amount_raw": canonical_text(self.amount.amount),
            "formula": self.formula,
            "density_source": self.density_source,
        }


class Component(Protocol):
    """A contributor to the déboursé sec of one bill-of-quantities line.

    Members are declared read-only so the frozen dataclasses below satisfy the
    protocol: a component is a value, never something a caller mutates between
    two computations.
    """

    @property
    def label(self) -> str: ...

    @property
    def kind(self) -> ResourceKind: ...

    def compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult: ...


def _ratio(numerator: Decimal, denominator: Decimal, *, what: str) -> Decimal:
    if denominator == 0:
        raise InvalidRateError(f"{what} nul: division impossible", denominator=str(denominator))
    if denominator < 0:
        raise InvalidRateError(f"{what} négatif: valeur invalide", denominator=str(denominator))
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        return numerator / denominator


@dataclass(frozen=True, slots=True)
class ConsumptionComponent:
    """Resource consumed proportionally to the bill-of-quantities quantity.

    ``consumption`` is expressed in *resource units per bill-of-quantities unit*
    (e.g. 0.35 t of sub-base per m2 of road). ``loss_ratio`` is the coefficient
    de perte, expressed as a fraction (0.05 = 5 %).

    When the resource unit and the bill-of-quantities unit belong to different
    dimensions — the classic m3 excavated / tonne landfilled — a sourced
    :class:`~metreo_domain.units.Density` is required and is echoed in the
    result so the estimate can justify it.
    """

    label: str
    kind: ResourceKind
    consumption: Decimal
    resource_unit_code: str
    unit_price: Money
    loss_ratio: Decimal = Decimal("0")
    convert_boq_quantity: bool = False
    density: Density | None = None

    def compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult:
        consumption = to_decimal(self.consumption)
        loss = to_decimal(self.loss_ratio)
        if loss < -1:
            raise PricingConfigurationError("coefficient de perte invalide", loss_ratio=str(loss))
        resource_unit = get_unit(self.resource_unit_code)
        density_source: str | None = None

        if self.convert_boq_quantity:
            conversion = convert(boq_quantity, self.resource_unit_code, density=self.density)
            base_value = conversion.quantity.value
            base_trace = f"({conversion.explanation})"
            if conversion.density_used is not None:
                density_source = conversion.density_used.source
        else:
            base_value = boq_quantity.value
            base_trace = f"{boq_quantity.value} {boq_quantity.unit.code}"

        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            resource_value = base_value * consumption * (Decimal(1) + loss)
        resource_quantity = Quantity(resource_value, resource_unit)
        amount = self.unit_price.scaled_by(resource_value)

        formula = (
            f"{base_trace} × {consumption} × (1 + {loss})"
            f" = {resource_value} {resource_unit.code}"
            f" × {self.unit_price.amount} {currency}/{resource_unit.code}"
            f" = {amount.amount} {currency}"
        )
        return ComponentResult(
            label=self.label,
            kind=self.kind,
            resource_quantity=resource_quantity,
            unit_price=self.unit_price,
            amount=amount,
            formula=formula,
            density_source=density_source,
        )


@dataclass(frozen=True, slots=True)
class OutputRateComponent:
    """Crew or plant priced from a productivity rate (rendement).

    ``output_rate`` is the number of bill-of-quantities units produced per hour.
    Hours = quantity / rate, cost = hours x hourly rate. A zero or negative rate
    raises :class:`~metreo_domain.errors.InvalidRateError` instead of producing
    an infinite or negative duration.
    """

    label: str
    kind: ResourceKind
    output_rate: Decimal
    hourly_rate: Money
    crew_size: Decimal = Decimal("1")
    time_unit_code: str = "h"

    def compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult:
        rate = to_decimal(self.output_rate)
        crew = to_decimal(self.crew_size)
        if crew <= 0:
            raise PricingConfigurationError("effectif d'équipe invalide", crew_size=str(crew))
        hours = _ratio(boq_quantity.value, rate, what=f"rendement de « {self.label} »")
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            billed = hours * crew
        time_unit = get_unit(self.time_unit_code)
        amount = self.hourly_rate.scaled_by(billed)
        formula = (
            f"{boq_quantity.value} {boq_quantity.unit.code} ÷ {rate}"
            f" {boq_quantity.unit.code}/{time_unit.code} = {hours} {time_unit.code}"
            f" × {crew} = {billed} {time_unit.code}"
            f" × {self.hourly_rate.amount} {currency}/{time_unit.code}"
            f" = {amount.amount} {currency}"
        )
        return ComponentResult(
            label=self.label,
            kind=self.kind,
            resource_quantity=Quantity(billed, time_unit),
            unit_price=self.hourly_rate,
            amount=amount,
            formula=formula,
        )


@dataclass(frozen=True, slots=True)
class RotationComponent:
    """Haulage priced per truck rotation.

    Rotations = quantity / payload, rounded **up** by default because half a
    truck does not leave the site. Set ``round_up=False`` for framework
    contracts billed pro rata. When the payload is expressed in a different
    dimension than the line (m3 dug vs t hauled) a sourced density is required.
    """

    label: str
    payload: Quantity
    cost_per_rotation: Money
    kind: ResourceKind = ResourceKind.TRANSPORT
    round_up: bool = True
    density: Density | None = None
    distance_km: Decimal | None = None
    rate_per_km: Money | None = None

    def __post_init__(self) -> None:
        """La distance et le tarif kilométrique sont indissociables.

        Poser l'un sans l'autre ne produisait aucune erreur : le moteur
        calculait les rotations et ignorait le kilométrage, en silence. Un
        transport de 20 km facturé comme un transport de 0 km, sans que rien
        ne le signale — ni dans le montant, ni dans la formule.

        Le contrôle vit ici, et non seulement dans le schéma HTTP : un
        instantané, un script ou un appel direct au moteur contourneraient
        Pydantic.
        """
        has_distance = self.distance_km is not None
        has_rate = self.rate_per_km is not None
        if has_distance != has_rate:
            missing = "rate_per_km" if has_distance else "distance_km"
            raise PricingConfigurationError(
                f"Transport « {self.label} » : « {missing} » manque. La distance "
                "et le tarif kilométrique vont ensemble ; l'un sans l'autre ne "
                "produit aucun coût et serait ignoré sans un mot.",
                component=self.label,
                missing=missing,
            )
        if self.distance_km is not None and self.distance_km < 0:
            raise PricingConfigurationError(
                f"Transport « {self.label} » : distance négative.",
                component=self.label,
            )

    def compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult:
        density_source: str | None = None
        if boq_quantity.unit.dimension is self.payload.unit.dimension:
            aligned = convert(boq_quantity, self.payload.unit.code)
        else:
            aligned = convert(boq_quantity, self.payload.unit.code, density=self.density)
            if aligned.density_used is not None:
                density_source = aligned.density_used.source

        raw_rotations = _ratio(aligned.quantity.value, self.payload.value, what="charge utile")
        if self.round_up:
            rotations = raw_rotations.to_integral_value(rounding="ROUND_CEILING")
            rounding_note = f" → {rotations} rotation(s) (arrondi supérieur)"
        else:
            rotations = raw_rotations
            rounding_note = ""

        per_rotation = self.cost_per_rotation
        distance_note = ""
        if self.distance_km is not None and self.rate_per_km is not None:
            distance = to_decimal(self.distance_km)
            if distance < 0:
                raise PricingConfigurationError("distance négative", distance_km=str(distance))
            per_rotation = per_rotation + self.rate_per_km.scaled_by(distance)
            distance_note = f" (dont {distance} km × {self.rate_per_km.amount} {currency}/km)"

        amount = per_rotation.scaled_by(rotations)
        formula = (
            f"{aligned.quantity.value} {self.payload.unit.code}"
            f" ÷ {self.payload.value} {self.payload.unit.code}/rotation"
            f" = {raw_rotations}{rounding_note}"
            f" × {per_rotation.amount} {currency}/rotation{distance_note}"
            f" = {amount.amount} {currency}"
        )
        if density_source:
            formula = f"{aligned.explanation}; {formula}"
        return ComponentResult(
            label=self.label,
            kind=self.kind,
            resource_quantity=Quantity(rotations, get_unit("pce")),
            unit_price=per_rotation,
            amount=amount,
            formula=formula,
            density_source=density_source,
        )


@dataclass(frozen=True, slots=True)
class LumpSumComponent:
    """A fixed amount added to the line whatever the quantity (installation,
    mobilisation, fixed subcontract price)."""

    label: str
    kind: ResourceKind
    amount_value: Money

    def compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult:
        del boq_quantity
        return ComponentResult(
            label=self.label,
            kind=self.kind,
            resource_quantity=Quantity(Decimal(1), get_unit("fft")),
            unit_price=self.amount_value,
            amount=self.amount_value,
            formula=f"forfait = {self.amount_value.amount} {currency}",
        )


class OverheadBase(str, Enum):
    """Which running amount a percentage step applies to."""

    DIRECT_COST = "direct_cost"
    DIRECT_PLUS_SITE = "direct_plus_site"
    RUNNING_TOTAL = "running_total"


class MarginMethod(str, Enum):
    """How the commercial margin is applied.

    ``ON_COST``
        Markup: selling = base x (1 + rate). 10 % on a cost of 100 gives 110.
    ``ON_PRICE``
        Margin on turnover: selling = base / (1 - rate). 10 % on a cost of 100
        gives 111.11, i.e. the margin really represents 10 % of the sale.
    """

    ON_COST = "on_cost"
    ON_PRICE = "on_price"


@dataclass(frozen=True, slots=True)
class MarkupPolicy:
    """Company-level chain from déboursé sec to prix de vente HT.

    Rates are fractions (0.08 = 8 %). Every rate has an explicit base so the
    order of application is auditable rather than implied by the code.
    """

    site_overheads_rate: Decimal = Decimal("0")
    site_overheads_base: OverheadBase = OverheadBase.DIRECT_COST
    general_overheads_rate: Decimal = Decimal("0")
    general_overheads_base: OverheadBase = OverheadBase.DIRECT_PLUS_SITE
    contingency_rate: Decimal = Decimal("0")
    contingency_base: OverheadBase = OverheadBase.RUNNING_TOTAL
    margin_rate: Decimal = Decimal("0")
    margin_method: MarginMethod = MarginMethod.ON_COST

    def __post_init__(self) -> None:
        # Les bornes vivent ici, et pas seulement dans les schémas HTTP : un
        # instantané gelé, un script ou un appel direct au moteur fournissent
        # des taux sans jamais passer par Pydantic. Un taux négatif produirait
        # un prix de vente inférieur au coût sans que rien ne le signale.
        for name in (
            "site_overheads_rate",
            "general_overheads_rate",
            "contingency_rate",
            "margin_rate",
        ):
            value = to_decimal(getattr(self, name))
            RATE.check(value, label=name)
            object.__setattr__(self, name, value)

        # Les énumérations sont converties, pas supposées : une base ou une
        # méthode inconnue venue d'un instantané doit être refusée ici plutôt
        # que de traverser le calcul et d'y produire un comportement par défaut
        # que personne n'a choisi.
        for name, enum_type in (
            ("site_overheads_base", OverheadBase),
            ("general_overheads_base", OverheadBase),
            ("contingency_base", OverheadBase),
            ("margin_method", MarginMethod),
        ):
            raw = getattr(self, name)
            try:
                object.__setattr__(self, name, enum_type(raw))
            except ValueError as exc:
                raise PricingConfigurationError(
                    f"{name} : « {raw} » n'est pas une valeur connue. "
                    "Attendu : " + ", ".join(sorted(e.value for e in enum_type)) + ".",
                    field=name,
                    value=str(raw),
                ) from exc

        if self.margin_method is MarginMethod.ON_PRICE and self.margin_rate >= 1:
            raise PricingConfigurationError(
                "une marge sur prix de vente doit être strictement inférieure à 100 %",
                margin_rate=str(self.margin_rate),
            )


@dataclass(frozen=True, slots=True)
class MarkupStepResult:
    key: str
    label: str
    base_amount: Money
    rate: Decimal
    amount: Money
    running_total: Money
    formula: str

    def to_dict(self, policy: RoundingPolicy) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "base_amount": str(policy.quantize(self.base_amount.amount)),
            "rate": canonical_text(self.rate),
            "amount": str(policy.quantize(self.amount.amount)),
            "running_total": str(policy.quantize(self.running_total.amount)),
            "formula": self.formula,
        }


@dataclass(frozen=True, slots=True)
class TaxRate:
    """A tax rate with the date it applies from, kept outside the HT price."""

    code: str
    label: str
    rate: Decimal
    applies_from: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", to_decimal(self.rate))


@dataclass(frozen=True, slots=True)
class LinePriceResult:
    """Everything needed to display and audit one priced line."""

    quantity: Quantity
    currency: str
    components: tuple[ComponentResult, ...]
    direct_cost: Money
    markup_steps: tuple[MarkupStepResult, ...]
    cost_price: Money
    selling_price_ht: Money
    unit_price_ht: Money
    tax_amounts: tuple[tuple[TaxRate, Money], ...] = ()

    @property
    def total_ttc(self) -> Money:
        return money_sum(
            [self.selling_price_ht, *[amount for _, amount in self.tax_amounts]],
            self.currency,
        )

    def cost_by_kind(self) -> dict[str, Money]:
        totals: dict[str, Money] = {}
        for component in self.components:
            key = component.kind.value
            totals[key] = totals.get(key, Money.zero(self.currency)) + component.amount
        return totals

    def to_dict(self, policy: RoundingPolicy) -> dict[str, Any]:
        return {
            "quantity": str(self.quantity.value),
            "unit": self.quantity.unit.code,
            "currency": self.currency,
            "components": [c.to_dict(policy) for c in self.components],
            "cost_by_kind": {
                kind: str(policy.quantize(amount.amount))
                for kind, amount in self.cost_by_kind().items()
            },
            "direct_cost": str(policy.quantize(self.direct_cost.amount)),
            "markup_steps": [s.to_dict(policy) for s in self.markup_steps],
            "cost_price": str(policy.quantize(self.cost_price.amount)),
            "selling_price_ht": str(policy.quantize(self.selling_price_ht.amount)),
            "selling_price_ht_raw": str(self.selling_price_ht.amount),
            "unit_price_ht": str(policy.quantize_unit_price(self.unit_price_ht.amount)),
            "taxes": [
                {
                    "code": tax.code,
                    "label": tax.label,
                    "rate": str(tax.rate),
                    "amount": str(policy.quantize(amount.amount)),
                }
                for tax, amount in self.tax_amounts
            ],
            "total_ttc": str(policy.quantize(self.total_ttc.amount)),
        }


@dataclass(frozen=True, slots=True)
class CompositePrice:
    """A sub-detail (sous-détail de prix): the components of one unit price."""

    code: str
    label: str
    unit_code: str
    components: tuple[Component, ...] = field(default_factory=tuple)

    def compute(
        self,
        quantity: Quantity,
        *,
        currency: str,
        markup: MarkupPolicy,
        taxes: tuple[TaxRate, ...] = (),
    ) -> LinePriceResult:
        return compute_line_price(
            quantity=quantity,
            components=self.components,
            currency=currency,
            markup=markup,
            taxes=taxes,
        )


def compute_line_price(
    *,
    quantity: Quantity,
    components: tuple[Component, ...],
    currency: str,
    markup: MarkupPolicy,
    taxes: tuple[TaxRate, ...] = (),
) -> LinePriceResult:
    """Price one line and return the full decomposition.

    The chain is always: direct cost -> site overheads -> general overheads ->
    cost price -> contingency -> margin -> selling price HT -> taxes. Steps with
    a zero rate are still emitted so the reader sees that they were considered.
    """
    # Vérifié AVANT toute matérialisation : un sous-détail de dix mille
    # composants ne doit pas être calculé puis refusé, il doit être refusé.
    if len(components) > MAX_COMPONENTS_PER_LINE:
        raise PricingConfigurationError(
            f"{len(components)} composants pour une seule ligne, maximum "
            f"{MAX_COMPONENTS_PER_LINE}. Au-delà, ce n'est plus un sous-détail "
            "mais un bordereau : il faut le découper.",
            count=len(components),
            maximum=MAX_COMPONENTS_PER_LINE,
        )

    results = tuple(c.compute(quantity, currency) for c in components)
    direct_cost = money_sum([r.amount for r in results], currency)

    steps: list[MarkupStepResult] = []
    running = direct_cost

    def apply(key: str, label: str, rate: Decimal, base_kind: OverheadBase) -> None:
        nonlocal running
        if base_kind is OverheadBase.DIRECT_COST:
            base = direct_cost
        elif base_kind is OverheadBase.DIRECT_PLUS_SITE:
            base = direct_cost + (steps[0].amount if steps else Money.zero(currency))
        else:
            base = running
        amount = base.scaled_by(rate)
        running = running + amount
        steps.append(
            MarkupStepResult(
                key=key,
                label=label,
                base_amount=base,
                rate=rate,
                amount=amount,
                running_total=running,
                formula=(
                    f"{base.amount} {currency} × {rate}"
                    f" = {amount.amount} {currency}"
                    f" → cumul {running.amount} {currency}"
                ),
            )
        )

    apply(
        "site_overheads",
        "Frais de chantier",
        markup.site_overheads_rate,
        markup.site_overheads_base,
    )
    apply(
        "general_overheads",
        "Frais généraux",
        markup.general_overheads_rate,
        markup.general_overheads_base,
    )
    cost_price = running

    apply("contingency", "Aléas", markup.contingency_rate, markup.contingency_base)

    margin_base = running
    if markup.margin_method is MarginMethod.ON_COST:
        margin_amount = margin_base.scaled_by(markup.margin_rate)
        margin_formula = (
            f"{margin_base.amount} {currency} × {markup.margin_rate}"
            f" = {margin_amount.amount} {currency} (marge sur coût)"
        )
    else:
        with localcontext() as ctx:
            ctx.prec = WORKING_PRECISION
            divisor = Decimal(1) - markup.margin_rate
            selling = margin_base.amount / divisor
        margin_amount = Money(selling - margin_base.amount, currency)
        margin_formula = (
            f"{margin_base.amount} {currency} ÷ (1 − {markup.margin_rate})"
            f" = {selling} {currency} → marge {margin_amount.amount} {currency}"
            " (marge sur prix de vente)"
        )
    running = running + margin_amount
    steps.append(
        MarkupStepResult(
            key="margin",
            label="Marge",
            base_amount=margin_base,
            rate=markup.margin_rate,
            amount=margin_amount,
            running_total=running,
            formula=margin_formula,
        )
    )

    selling_price_ht = running
    unit_price_ht = Money(
        _ratio(
            selling_price_ht.amount,
            quantity.value,
            what="quantité de la ligne",
        )
        if quantity.value != 0
        else Decimal(0),
        currency,
    )
    tax_amounts = tuple((tax, selling_price_ht.scaled_by(tax.rate)) for tax in taxes)

    # Les bornes d'entrée ne suffisent pas : une quantité et un prix unitaire
    # chacun dans sa plage produisent un total qui n'y est pas. C'est ici, sur
    # le montant CALCULÉ, que le stockage est réellement protégé.
    check_total(direct_cost.amount, label="déboursé sec de la ligne")
    check_total(cost_price.amount, label="prix de revient de la ligne")
    check_total(selling_price_ht.amount, label="prix de vente HT de la ligne")
    # Les taxes et le TTC sont stockés et exportés comme le HT : les contrôler
    # ici, et non seulement lorsque la ligne est agrégée par `compute_estimate`.
    # Une ligne calculée seule — sous-détail, aperçu — doit être refusée par le
    # même chemin.
    total_tax = Decimal(0)
    for tax, amount in tax_amounts:
        check_total(amount.amount, label=f"total de {tax.code} sur la ligne")
        total_tax += amount.amount
    check_total(selling_price_ht.amount + total_tax, label="total TTC de la ligne")

    return LinePriceResult(
        quantity=quantity,
        currency=currency,
        components=results,
        direct_cost=direct_cost,
        markup_steps=tuple(steps),
        cost_price=cost_price,
        selling_price_ht=selling_price_ht,
        unit_price_ht=unit_price_ht,
        tax_amounts=tax_amounts,
    )


def compute_flat_line_price(
    *,
    quantity: Quantity,
    unit_price: Money,
    currency: str,
    markup: MarkupPolicy,
    taxes: tuple[TaxRate, ...] = (),
    label: str = "Prix unitaire de bibliothèque",
    kind: ResourceKind = ResourceKind.OTHER,
    price_unit_code: str | None = None,
    density: Density | None = None,
) -> LinePriceResult:
    """Price a line from a single library unit price rather than a sub-detail.

    ``price_unit_code`` is the unit the library price is expressed in. When it
    differs from the line's unit the quantity is converted first, and the
    conversion appears in the formula. A conversion that cannot be justified —
    a price per hour applied to a line in m2, or m3 to tonnes with no sourced
    density — raises instead of producing a plausible-looking wrong number.
    """
    resource_unit = price_unit_code or quantity.unit.code
    components: tuple[Component, ...] = (
        ConsumptionComponent(
            label=label,
            kind=kind,
            consumption=Decimal(1),
            resource_unit_code=resource_unit,
            unit_price=unit_price,
            convert_boq_quantity=get_unit(resource_unit).code != quantity.unit.code,
            density=density,
        ),
    )
    return compute_line_price(
        quantity=quantity,
        components=components,
        currency=currency,
        markup=markup,
        taxes=taxes,
    )
