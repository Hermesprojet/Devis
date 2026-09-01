"""Serialisable description of a sub-detail, and its mapping to the engine.

A *component spec* is a plain JSON-compatible dict. It is the single
representation used for live computation, for storage in a frozen estimate and
for re-running the engine from that frozen snapshot years later. Adding a
component type means adding it here and nowhere else.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from metreo_domain import bounds
from metreo_domain.errors import DomainError, PricingConfigurationError, UnknownUnitError
from metreo_domain.money import Money, RoundingPolicy, canonical_text, to_decimal
from metreo_domain.pricing import (
    Component,
    ConsumptionComponent,
    LumpSumComponent,
    MarkupPolicy,
    OutputRateComponent,
    ResourceKind,
    RotationComponent,
    compute_line_price,
)
from metreo_domain.units import Density, Quantity, get_unit

from ..models import CompositeComponentRow, CompositePriceRow

COMPONENT_TYPES = ("consumption", "output_rate", "rotation", "lump_sum")

#: Fields that must be present and non-null for each component type.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "consumption": ("consumption", "resource_unit_code", "unit_price"),
    "output_rate": ("output_rate", "hourly_rate"),
    "rotation": ("payload_value", "payload_unit_code", "cost_per_rotation"),
    "lump_sum": ("lump_sum_amount",),
}


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None or value == "" else to_decimal(value)


def spec_from_row(row: CompositeComponentRow) -> dict[str, Any]:
    """Serialise a stored component.

    Values become strings so JSON round-trips the exact decimal the user
    entered, through :func:`~metreo_domain.money.canonical_text` so that the
    spelling comes from the value and not from the database backend.
    """

    def s(value: Any) -> str | None:
        return None if value is None else canonical_text(value)

    spec: dict[str, Any] = {
        "component_type": row.component_type,
        "label": row.label,
        "resource_kind": row.resource_kind,
    }
    if row.component_type == "consumption":
        spec.update(
            consumption=s(row.consumption),
            resource_unit_code=row.resource_unit_code,
            unit_price=s(row.unit_price),
            loss_ratio=s(row.loss_ratio) or "0",
            convert_boq_quantity=bool(row.convert_boq_quantity),
            density_value=s(row.density_value),
            density_source=row.density_source,
        )
    elif row.component_type == "output_rate":
        spec.update(
            output_rate=s(row.output_rate),
            hourly_rate=s(row.hourly_rate),
            crew_size=s(row.crew_size) or "1",
        )
    elif row.component_type == "rotation":
        spec.update(
            payload_value=s(row.payload_value),
            payload_unit_code=row.payload_unit_code,
            cost_per_rotation=s(row.cost_per_rotation),
            round_up=bool(row.round_up),
            distance_km=s(row.distance_km),
            rate_per_km=s(row.rate_per_km),
            density_value=s(row.density_value),
            density_source=row.density_source,
        )
    elif row.component_type == "lump_sum":
        spec.update(lump_sum_amount=s(row.lump_sum_amount))
    return spec


def specs_from_composite(composite: CompositePriceRow) -> list[dict[str, Any]]:
    return [spec_from_row(row) for row in composite.components]


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Return the list of problems with a spec, empty when it is usable."""
    problems: list[str] = []
    component_type = spec.get("component_type")
    if component_type not in COMPONENT_TYPES:
        return [f"type de composant inconnu: {component_type!r}"]
    if not spec.get("label"):
        problems.append("le libellé du composant est obligatoire")
    for field in REQUIRED_FIELDS[component_type]:
        if spec.get(field) in (None, ""):
            problems.append(f"champ obligatoire manquant pour « {component_type} »: {field}")

    for unit_field in ("resource_unit_code", "payload_unit_code"):
        code = spec.get(unit_field)
        if code:
            try:
                # Canonicalisée sur place : « tonne » et « m³ » doivent
                # atteindre la base sous leur forme canonique, sans quoi deux
                # composants identiques s'y retrouvent sous deux orthographes
                # et aucune requête ne les rapproche.
                spec[unit_field] = get_unit(code).code
            except UnknownUnitError:
                problems.append(f"unité inconnue: {code}")

    if spec.get("density_value") and not spec.get("density_source"):
        problems.append("une masse volumique doit toujours être accompagnée de sa source")
    if spec.get("convert_boq_quantity") and not spec.get("density_value"):
        # Not always an error: a conversion inside one dimension needs no
        # density. Flagged only when the units cross volume <-> mass, which the
        # engine detects at computation time.
        pass
    return problems


def _density(spec: dict[str, Any]) -> Density | None:
    value = _decimal_or_none(spec.get("density_value"))
    if value is None:
        return None
    source = spec.get("density_source")
    if not source:
        raise DomainError("masse volumique sans source", spec_label=spec.get("label"))
    return Density(value_kg_per_m3=value, source=str(source))


def component_from_spec(spec: dict[str, Any], currency: str) -> Component:
    """Construit un composant du moteur depuis une spécification.

    **Ne suppose plus que `validate_spec` a été appelée.** Un instantané gelé,
    une ligne historique ou un appel direct peuvent apporter une spécification
    incomplète : sans ce contrôle, `to_decimal(None)` levait un `TypeError` —
    une erreur de programmation, donc un HTTP 500, là où la donnée est
    simplement invalide et mérite une erreur métier lisible.
    """
    problems = validate_spec(spec)
    if problems:
        raise PricingConfigurationError(
            "Spécification de composant inexploitable : " + " ; ".join(problems),
            component=spec.get("label"),
            component_type=spec.get("component_type"),
            problems=problems,
        )

    component_type = spec["component_type"]
    label = spec["label"]
    kind = ResourceKind(spec.get("resource_kind") or "other")

    if component_type == "consumption":
        return ConsumptionComponent(
            label=label,
            kind=kind,
            consumption=to_decimal(spec["consumption"]),
            resource_unit_code=spec["resource_unit_code"],
            unit_price=Money(to_decimal(spec["unit_price"]), currency),
            loss_ratio=to_decimal(spec.get("loss_ratio") or "0"),
            convert_boq_quantity=bool(spec.get("convert_boq_quantity")),
            density=_density(spec),
        )
    if component_type == "output_rate":
        return OutputRateComponent(
            label=label,
            kind=kind,
            output_rate=to_decimal(spec["output_rate"]),
            hourly_rate=Money(to_decimal(spec["hourly_rate"]), currency),
            crew_size=to_decimal(spec.get("crew_size") or "1"),
        )
    if component_type == "rotation":
        rate_per_km = _decimal_or_none(spec.get("rate_per_km"))
        return RotationComponent(
            label=label,
            kind=kind if kind is not ResourceKind.OTHER else ResourceKind.TRANSPORT,
            payload=Quantity.of(spec["payload_value"], spec["payload_unit_code"]),
            cost_per_rotation=Money(to_decimal(spec["cost_per_rotation"]), currency),
            round_up=bool(spec.get("round_up", True)),
            density=_density(spec),
            distance_km=_decimal_or_none(spec.get("distance_km")),
            rate_per_km=Money(rate_per_km, currency) if rate_per_km is not None else None,
        )
    if component_type == "lump_sum":
        return LumpSumComponent(
            label=label,
            kind=kind,
            amount_value=Money(to_decimal(spec["lump_sum_amount"]), currency),
        )
    raise DomainError(f"type de composant non pris en charge: {component_type}")


def components_from_specs(specs: list[dict[str, Any]], currency: str) -> tuple[Component, ...]:
    """Construit les composants d'un sous-détail, après contrôle de cardinalité.

    Contrôlé ici et pas seulement dans le schéma HTTP : la route n'est pas le
    seul chemin vers cette fonction, et un instantané gelé peut porter plus de
    composants que la limite en vigueur aujourd'hui.
    """
    if len(specs) > bounds.MAX_COMPONENTS_PER_LINE:
        raise PricingConfigurationError(
            f"{len(specs)} composants pour un sous-détail, maximum "
            f"{bounds.MAX_COMPONENTS_PER_LINE}.",
            count=len(specs),
            maximum=bounds.MAX_COMPONENTS_PER_LINE,
        )
    if not specs:
        raise PricingConfigurationError(
            "Un sous-détail sans composant ne calcule rien : il n'a pas de sens."
        )
    return tuple(component_from_spec(spec, currency) for spec in specs)


# --------------------------------------------------------------------------
# Prévisualisation : le MOTEUR calcule, personne ne recopie son arithmétique
# --------------------------------------------------------------------------


def apercu(
    specs: list[dict[str, Any]],
    *,
    unit_code: str,
    currency: str,
    arrondi: RoundingPolicy | None = None,
) -> dict[str, Any]:
    """Le déboursé sec d'UNE unité du sous-détail, et sa ventilation.

    **Le déboursé sec, et pas un prix de vente.** `MarkupPolicy()` par défaut
    est neutre : frais de chantier, frais généraux, aléas et marge à zéro. Un
    sous-détail décrit ce que la ressource coûte ; lui appliquer la politique
    commerciale de l'entreprise mêlerait deux choses qui se décident
    séparément, et le chiffre affiché dans la bibliothèque ne serait plus
    celui que le devis reprend.

    **Aucun calcul n'est refait ailleurs.** Cette fonction appelle
    `compute_line_price`, le même point d'entrée que l'estimation. C'est
    l'unique raison d'être de cette prévisualisation côté serveur : l'écran ne
    doit pas réimplémenter l'arithmétique en TypeScript, sous peine de deux
    vérités qui divergent au premier arrondi.
    """
    arrondi = arrondi or RoundingPolicy()
    composants = components_from_specs(specs, currency)
    resultat = compute_line_price(
        quantity=Quantity.of(1, unit_code),
        components=composants,
        currency=currency,
        markup=MarkupPolicy(),
    )
    par_nature = resultat.cost_by_kind()
    return {
        "unit_code": unit_code,
        "currency": currency,
        # Les deux formes, comme partout ailleurs dans ce dépôt : le décimal
        # EXACT pour qui recalcule, et la forme arrondie selon la politique de
        # l'organisation pour qui lit. L'écran affiche la seconde et ne
        # l'invente pas — un arrondi décidé en TypeScript diverge du devis au
        # premier centime.
        "unit_cost": canonical_text(resultat.direct_cost.amount),
        "unit_cost_display": str(arrondi.quantize(resultat.direct_cost.amount)),
        "by_kind": [
            {
                "resource_kind": nature,
                "label": ResourceKind(nature).label_fr,
                "amount": canonical_text(montant.amount),
                "amount_display": str(arrondi.quantize(montant.amount)),
            }
            for nature, montant in sorted(par_nature.items())
        ],
        # `to_dict` du domaine plutôt qu'une projection maison : il porte déjà
        # la quantité de ressource, le prix unitaire, la formule et la source
        # de densité. En réécrire une partie ici ferait diverger l'aperçu de ce
        # que l'écran d'estimation affiche pour la MÊME ligne.
        "components": [composant.to_dict(arrondi) for composant in resultat.components],
    }
