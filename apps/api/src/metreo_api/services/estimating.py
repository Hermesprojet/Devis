"""Turning stored rows into an engine run, and freezing the result.

The rule that shapes this module: a frozen estimate version must reproduce its
own numbers from its own snapshot, with no lookup into tables that may since
have changed. So the snapshot stores the *inputs* (quantities, resolved prices,
markup, taxes, rounding) next to the *result*, and
:func:`recompute_from_snapshot` re-runs the engine on those inputs alone.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain import bounds
from metreo_domain.errors import DomainError, PricingConfigurationError
from metreo_domain.estimate import (
    EstimateLineInput,
    EstimateResult,
    LineKind,
    MissingPricePolicy,
    compute_estimate,
)
from metreo_domain.money import Money, RoundingPolicy, canonical_text, to_decimal
from metreo_domain.pricing import MarginMethod, MarkupPolicy, OverheadBase, TaxRate
from metreo_domain.units import Quantity

from ..models import (
    BoqItem,
    CompositePriceRow,
    Estimate,
    EstimateVersion,
    OrganizationSettings,
    PriceItem,
    TaxRateRow,
    utcnow,
)
from .composites import components_from_specs, specs_from_composite
from .locking import lock_owned

SNAPSHOT_SCHEMA = "metreo.estimate.snapshot/1"


def markup_from_settings(settings: OrganizationSettings) -> MarkupPolicy:
    return MarkupPolicy(
        site_overheads_rate=settings.site_overheads_rate,
        site_overheads_base=OverheadBase(settings.site_overheads_base),
        general_overheads_rate=settings.general_overheads_rate,
        general_overheads_base=OverheadBase(settings.general_overheads_base),
        contingency_rate=settings.contingency_rate,
        contingency_base=OverheadBase(settings.contingency_base),
        margin_rate=settings.margin_rate,
        margin_method=MarginMethod(settings.margin_method),
    )


def markup_to_dict(markup: MarkupPolicy) -> dict[str, Any]:
    return {
        "site_overheads_rate": canonical_text(markup.site_overheads_rate),
        "site_overheads_base": markup.site_overheads_base.value,
        "general_overheads_rate": canonical_text(markup.general_overheads_rate),
        "general_overheads_base": markup.general_overheads_base.value,
        "contingency_rate": canonical_text(markup.contingency_rate),
        "contingency_base": markup.contingency_base.value,
        "margin_rate": canonical_text(markup.margin_rate),
        "margin_method": markup.margin_method.value,
    }


def markup_from_dict(data: dict[str, Any]) -> MarkupPolicy:
    return MarkupPolicy(
        site_overheads_rate=to_decimal(data["site_overheads_rate"]),
        site_overheads_base=OverheadBase(data["site_overheads_base"]),
        general_overheads_rate=to_decimal(data["general_overheads_rate"]),
        general_overheads_base=OverheadBase(data["general_overheads_base"]),
        contingency_rate=to_decimal(data["contingency_rate"]),
        contingency_base=OverheadBase(data["contingency_base"]),
        margin_rate=to_decimal(data["margin_rate"]),
        margin_method=MarginMethod(data["margin_method"]),
    )


def rounding_from_settings(settings: OrganizationSettings) -> RoundingPolicy:
    return RoundingPolicy(
        scale=settings.rounding_scale,
        mode=settings.rounding_mode,
        unit_price_scale=settings.unit_price_scale,
    )


def rounding_to_dict(policy: RoundingPolicy) -> dict[str, Any]:
    return {
        "scale": policy.scale,
        "mode": policy.mode,
        "unit_price_scale": policy.unit_price_scale,
    }


def rounding_from_dict(data: dict[str, Any]) -> RoundingPolicy:
    return RoundingPolicy(
        scale=int(data["scale"]),
        mode=str(data["mode"]),
        unit_price_scale=data.get("unit_price_scale"),
    )


def active_taxes(
    session: Session, organization_id: str, *, at: date | None = None
) -> tuple[TaxRate, ...]:
    """Tax rates in force at ``at`` (today by default), most specific first."""
    reference = at or date.today()
    rows = session.scalars(
        select(TaxRateRow)
        .where(TaxRateRow.organization_id == organization_id, TaxRateRow.is_default.is_(True))
        .order_by(TaxRateRow.applies_from.asc())
    ).all()
    selected: list[TaxRate] = []
    for row in rows:
        if row.applies_from and row.applies_from > reference:
            continue
        if row.applies_to and row.applies_to < reference:
            continue
        selected.append(
            TaxRate(
                code=row.code,
                label=row.label,
                rate=row.rate,
                applies_from=row.applies_from.isoformat() if row.applies_from else None,
            )
        )
    return tuple(selected)


def taxes_to_list(taxes: tuple[TaxRate, ...]) -> list[dict[str, Any]]:
    return [
        {
            "code": t.code,
            "label": t.label,
            "rate": canonical_text(t.rate),
            "applies_from": t.applies_from,
        }
        for t in taxes
    ]


def taxes_from_list(data: list[dict[str, Any]]) -> tuple[TaxRate, ...]:
    return tuple(
        TaxRate(
            code=item["code"],
            label=item["label"],
            rate=to_decimal(item["rate"]),
            applies_from=item.get("applies_from"),
        )
        for item in data
    )


def build_line_specs(
    session: Session, *, estimate: Estimate, version: EstimateVersion
) -> list[dict[str, Any]]:
    """Resolve every bill-of-quantities row into a serialisable pricing input.

    Price resolution order for an item: its explicit composite (sub-detail),
    then its linked library price, then nothing — and *nothing* stays nothing:
    the line is reported as unpriced rather than valued at zero.
    """
    # `LIMIT n+1` plutôt qu'un `.all()` suivi d'un contrôle : charger vingt
    # mille lignes pour ensuite annoncer qu'il y en a trop revient à payer le
    # coût qu'on prétend éviter. Une ligne de plus que la limite suffit à
    # savoir que la limite est dépassée.
    items = list(
        session.scalars(
            select(BoqItem)
            .where(
                BoqItem.organization_id == estimate.organization_id,
                BoqItem.boq_id == estimate.boq_id,
            )
            .order_by(BoqItem.sort_index.asc(), BoqItem.position.asc())
            .limit(bounds.MAX_LINES_PER_ESTIMATE + 1)
        ).all()
    )
    if len(items) > bounds.MAX_LINES_PER_ESTIMATE:
        raise PricingConfigurationError(
            f"Ce bordereau dépasse {bounds.MAX_LINES_PER_ESTIMATE} lignes, "
            "maximum d'une estimation. Le découper en lots.",
            maximum=bounds.MAX_LINES_PER_ESTIMATE,
        )

    specs: list[dict[str, Any]] = []
    for item in items:
        spec: dict[str, Any] = {
            "line_id": item.id,
            "position": item.position,
            "code": item.code or item.position,
            "designation": item.designation,
            "unit_code": item.unit_code,
            "quantity": canonical_text(item.quantity),
            "kind": item.kind,
            "status": item.status,
            "pricing": None,
        }
        if item.kind == "section":
            specs.append(spec)
            continue

        if item.composite_price_id:
            composite = session.get(CompositePriceRow, item.composite_price_id)
            if composite is not None and composite.organization_id == estimate.organization_id:
                spec["pricing"] = {
                    "mode": "composite",
                    "composite_code": composite.code,
                    "composite_label": composite.label,
                    "components": specs_from_composite(composite),
                }
                specs.append(spec)
                continue

        if item.price_item_id:
            price_item = session.get(PriceItem, item.price_item_id)
            if (
                price_item is not None
                and price_item.organization_id == estimate.organization_id
                and price_item.price_book_version_id == version.price_book_version_id
            ):
                spec["pricing"] = {
                    "mode": "library_price",
                    "price_item_id": price_item.id,
                    "price_item_code": price_item.code,
                    "price_item_label": price_item.label,
                    "unit_price": canonical_text(price_item.unit_price),
                    "currency": price_item.currency,
                    "price_unit_code": price_item.unit_code,
                }
        specs.append(spec)
    return specs


class PricingInputError(Exception):
    """One or more lines cannot be priced as configured.

    Carries the offending lines so the UI can point at the row instead of
    showing a generic failure. Raised before any total is produced: a partial
    estimate with a silently dropped line would be worse than no estimate.
    """

    def __init__(self, problems: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(problems)} ligne(s) non calculable(s)")
        self.problems = problems


def inputs_from_specs(specs: list[dict[str, Any]], currency: str) -> tuple[EstimateLineInput, ...]:
    """Build engine inputs, collecting every line-level problem before failing."""
    inputs: list[EstimateLineInput] = []
    problems: list[dict[str, Any]] = []

    for spec in specs:
        pricing = spec.get("pricing")
        components = None
        unit_price = None
        price_unit_code = None
        try:
            quantity = Quantity.of(spec["quantity"], spec["unit_code"])
            if pricing and pricing["mode"] == "composite":
                components = components_from_specs(pricing["components"], currency)
            elif pricing and pricing["mode"] == "library_price":
                unit_price = Money(
                    to_decimal(pricing["unit_price"]), pricing.get("currency") or currency
                )
                price_unit_code = pricing.get("price_unit_code")
        except DomainError as exc:
            problems.append(
                {
                    "line_id": spec["line_id"],
                    "position": spec.get("position"),
                    "designation": spec["designation"],
                    **exc.to_dict(),
                }
            )
            continue

        line = EstimateLineInput(
            line_id=spec["line_id"],
            code=spec["code"],
            designation=spec["designation"],
            quantity=quantity,
            kind=LineKind(spec["kind"]),
            components=components,
            unit_price=unit_price,
            unit_price_unit_code=price_unit_code,
        )
        # Price the line on its own first: a refused conversion must be
        # attributed to the row that caused it, not to the whole estimate.
        try:
            compute_estimate((line,), currency=currency, markup=MarkupPolicy())
        except DomainError as exc:
            detail = exc.to_dict()
            if exc.code in ("incompatible_units", "ambiguous_conversion"):
                detail["hint"] = (
                    "L'unité du prix de bibliothèque ne correspond pas à celle du "
                    "poste. Utilisez un sous-détail explicite (avec masse volumique "
                    "sourcée si la conversion croise volume et masse)."
                )
            problems.append(
                {
                    "line_id": spec["line_id"],
                    "position": spec.get("position"),
                    "designation": spec["designation"],
                    **detail,
                }
            )
            continue
        inputs.append(line)

    if problems:
        raise PricingInputError(problems)
    return tuple(inputs)


def compute_version(
    session: Session, *, estimate: Estimate, version: EstimateVersion
) -> tuple[EstimateResult, dict[str, Any]]:
    """Compute a draft version from live data, or a frozen one from its snapshot."""
    if version.status == "frozen" and version.snapshot:
        return recompute_from_snapshot(version.snapshot), version.snapshot

    settings = session.get(OrganizationSettings, estimate.organization_id)
    if settings is None:  # pragma: no cover - settings are created with the org
        raise ValueError("organization settings missing")

    markup = markup_from_settings(settings)
    taxes = active_taxes(session, estimate.organization_id)
    rounding = rounding_from_settings(settings)
    specs = build_line_specs(session, estimate=estimate, version=version)
    inputs = inputs_from_specs(specs, estimate.currency)

    result = compute_estimate(
        inputs,
        currency=estimate.currency,
        markup=markup,
        taxes=taxes,
        missing_price_policy=MissingPricePolicy(settings.missing_price_policy),
    )
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "currency": estimate.currency,
        "price_book_version_id": version.price_book_version_id,
        "markup": markup_to_dict(markup),
        "taxes": taxes_to_list(taxes),
        "rounding": rounding_to_dict(rounding),
        "missing_price_policy": settings.missing_price_policy,
        "lines": specs,
        "result": result.to_dict(rounding),
    }
    return result, snapshot


def recompute_from_snapshot(snapshot: dict[str, Any]) -> EstimateResult:
    """Re-run the engine using only what the snapshot contains."""
    currency = snapshot["currency"]
    inputs = inputs_from_specs(snapshot["lines"], currency)
    return compute_estimate(
        inputs,
        currency=currency,
        markup=markup_from_dict(snapshot["markup"]),
        taxes=taxes_from_list(snapshot["taxes"]),
        missing_price_policy=MissingPricePolicy(snapshot["missing_price_policy"]),
    )


def _plain_decimal(value: Decimal) -> str:
    """One decimal value, one spelling.

    ``Decimal("10.00")`` and ``Decimal("10.0")`` are equal but do not share a
    representation, and the backends disagree on which one they hand back:
    PostgreSQL returns ``NUMERIC(28, 10)`` padded to ten decimals, SQLite
    returns the text that was written. Digesting the raw spelling would give
    the same frozen version two different hashes depending on the database it
    was read from. Trailing zeros are therefore dropped, and the exponent
    notation ``normalize()`` produces for round numbers (``1E+2``) is expanded
    back to ``100``.
    """
    if not value.is_finite():
        raise ValueError(f"Valeur décimale non finie dans un instantané : {value!r}")
    normalized = value.normalize()
    _, _, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal(1))
    text = format(normalized, "f")
    return "0" if text in {"-0", "0"} else text


def canonical_snapshot(value: Any) -> Any:
    """Rewrite a snapshot into its one canonical shape before hashing.

    Two snapshots that carry the same information must produce the same bytes,
    and anything whose meaning is not preserved by serialisation is refused
    rather than coerced. That is why there is no ``default=`` fallback here: a
    type this function does not know is a bug to fix, not a value to stringify
    and hope for.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        # Tagged so that the decimal 10 and the string "10" cannot collide.
        return {"$dec": _plain_decimal(value)}
    if isinstance(value, float):
        raise TypeError(
            "Un flottant binaire n'a rien à faire dans un instantané d'estimation : "
            f"{value!r}. Les montants sont des Decimal (voir metreo_domain.money)."
        )
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): canonical_snapshot(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonical_snapshot(item) for item in value]
    raise TypeError(f"Type non sérialisable dans un instantané : {type(value).__name__}")


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    """SHA-256 of the canonical form of ``snapshot``.

    Tamper-**evident** within the threat model of :mod:`metreo_api.services.audit`:
    it detects a snapshot edited in place. It is not an immutable ledger — a
    database administrator able to rewrite the row can recompute the digest to
    match.
    """
    material = json.dumps(
        canonical_snapshot(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class FreezeRefused(Exception):
    """Raised when the company rules forbid freezing the version as it stands."""

    def __init__(self, reason: str, details: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


def freeze_version(
    session: Session,
    *,
    estimate: Estimate,
    version: EstimateVersion,
    actor_user_id: str | None,
    label: str | None = None,
) -> tuple[EstimateVersion, EstimateResult]:
    """Make a version immutable.

    Refuses when a line has no price and the company set
    ``missing_price_policy=block``. On success the version stores its snapshot,
    the digest of that snapshot and the totals; later edits to the bill of
    quantities or to the price library do not touch it.
    """
    if version.status == "frozen":
        raise FreezeRefused("already_frozen", {"version_number": version.version_number})

    result, snapshot = compute_version(session, estimate=estimate, version=version)
    if result.blocking:
        raise FreezeRefused(
            "missing_prices",
            {
                "missing_price_line_ids": list(result.missing_price_line_ids),
                "message": (
                    "Des postes sont sans prix et la règle de l'entreprise "
                    "interdit le gel dans cet état."
                ),
            },
        )

    version.status = "frozen"
    version.snapshot = snapshot
    version.snapshot_sha256 = snapshot_digest(snapshot)
    version.markup = snapshot["markup"]
    version.taxes = snapshot["taxes"]
    version.rounding = snapshot["rounding"]
    version.missing_price_policy = snapshot["missing_price_policy"]
    version.total_selling_price_ht = result.total_selling_price_ht.amount
    version.total_ttc = result.total_ttc.amount
    version.frozen_at = utcnow()
    version.frozen_by = actor_user_id
    if label:
        version.label = label
    session.flush()
    return version, result


def next_version_number(session: Session, estimate_id: str, *, organization_id: str) -> int:
    """Next free version number for an estimate, allocated under a lock.

    The parent :class:`~metreo_api.models.Estimate` is locked first: without
    it two callers read the same maximum, both choose the same successor, and
    the second one violates ``uq_estimateversion_number`` — correct data, but
    an uncaught ``IntegrityError`` and a 500 for the caller.
    """
    lock_owned(session, Estimate, organization_id, estimate_id, label="Estimation")
    numbers = session.scalars(
        select(EstimateVersion.version_number).where(EstimateVersion.estimate_id == estimate_id)
    ).all()
    return (max(numbers) if numbers else 0) + 1


def lock_version(session: Session, *, organization_id: str, version_id: str) -> EstimateVersion:
    """Hold an estimate version so its status cannot change under our feet.

    Freezing reads ``status``, decides, then writes. Two callers reading
    ``draft`` at the same time would both freeze, producing two audit events
    for one irreversible act.
    """
    return lock_owned(session, EstimateVersion, organization_id, version_id, label="Version")


def totals_for_display(
    result: EstimateResult, rounding: RoundingPolicy, *, include_internal: bool
) -> dict[str, Any]:
    """Public totals, plus internal cost figures only when the caller may see them."""
    payload = result.to_dict(rounding)
    if include_internal:
        return payload
    payload.pop("total_direct_cost", None)
    payload.pop("total_cost_price", None)
    for line in payload["lines"]:
        price = line.get("price")
        if not price:
            continue
        price.pop("components", None)
        price.pop("cost_by_kind", None)
        price.pop("direct_cost", None)
        price.pop("cost_price", None)
        price.pop("markup_steps", None)
    return payload


def decimal_or_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0")
