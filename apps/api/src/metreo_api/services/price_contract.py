"""Contrat unique d'une ligne de prix.

Trois chemins écrivent dans `price_items` : la saisie manuelle, la
prévisualisation d'un import et la confirmation du lot. Ils appliquaient trois
jeux de règles différents, et chacun laissait passer ce que les deux autres
refusaient — des longueurs recopiées de mémoire d'un côté, aucune vérification
d'unité de l'autre, une plage de dates jamais contrôlée au troisième.

Ce module porte la règle **une seule fois**. Les longueurs sont lues sur les
colonnes de :class:`~metreo_api.models.PriceItem` : une migration qui change le
modèle change le contrat, sans qu'aucune constante n'ait à suivre.

Le contrat rend soit une ligne normalisée, soit une liste d'erreurs
structurées. Jamais une exception non métier : une cellule fautive dans un
fichier de mille lignes doit produire une erreur de ligne, pas un échec global.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from metreo_domain import bounds
from metreo_domain.errors import DomainError
from metreo_domain.money import to_decimal
from metreo_domain.units import get_unit

from ..models import PriceItem

#: Valeurs acceptées pour les colonnes énumérées.
VALID_RESOURCE_KINDS = frozenset(
    {"material", "labor", "equipment", "transport", "disposal", "subcontract", "other"}
)
VALID_STATUS = frozenset({"active", "draft", "archived", "superseded"})
VALID_CONFIDENCE = frozenset({"declared", "quoted", "contracted", "estimated"})

#: Un délai de livraison au-delà de dix ans n'est pas un délai : c'est une
#: erreur de saisie ou une date confondue avec une durée.
MAX_LEAD_TIME_DAYS = 3650

#: Colonnes textuelles dont la longueur est contrainte par le stockage.
LENGTH_CHECKED = (
    "code",
    "label",
    "family",
    "supplier_name",
    "region_code",
    "source",
    "indexation",
)


def sql_length(column: str) -> int:
    """Longueur maximale **lue sur la colonne**, jamais recopiée.

    Une version antérieure portait des constantes écrites de mémoire :
    `family` vérifié à 120 pour un `String(60)`, `region_code` à 20 pour un
    `String(10)`. Une ligne passait la validation puis échouait à l'écriture.
    """
    sql_column = PriceItem.__table__.columns[column]
    length = getattr(sql_column.type, "length", None)
    if length is None:  # pragma: no cover - colonne Text, sans limite
        raise KeyError(f"La colonne {column} ne porte pas de longueur.")
    return int(length)


@dataclass(frozen=True, slots=True)
class FieldError:
    """Erreur rattachée à une colonne, lisible par un humain."""

    column: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"column": self.column, "code": self.code, "message": self.message}


@dataclass(slots=True)
class ValidationOutcome:
    """Le résultat du contrat : une ligne normalisée **ou** des erreurs."""

    normalized: dict[str, Any] = field(default_factory=dict)
    errors: list[FieldError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _check_required(value: Any, column: str, outcome: ValidationOutcome) -> str | None:
    text = None if value is None else str(value).strip()
    if not text:
        outcome.errors.append(FieldError(column, "required", f"« {column} » est obligatoire."))
        return None
    return text


def _check_length(value: str | None, column: str, outcome: ValidationOutcome) -> None:
    if value is None:
        return
    maximum = sql_length(column)
    if len(value) > maximum:
        outcome.errors.append(
            FieldError(
                column,
                "too_long",
                f"« {value[:20]}… » fait {len(value)} caractères, maximum {maximum}.",
            )
        )


def _check_choice(
    value: str | None, column: str, allowed: frozenset[str], outcome: ValidationOutcome
) -> None:
    if value and value not in allowed:
        outcome.errors.append(
            FieldError(
                column,
                f"invalid_{column}",
                f"« {value} » n'est pas une valeur acceptée. "
                "Attendu : " + ", ".join(sorted(allowed)) + ".",
            )
        )


def _check_decimal(
    value: Any, column: str, bound: bounds.Bound, outcome: ValidationOutcome
) -> Decimal | None:
    """Convertit et borne. Un `Infinity` ou un `NaN` est refusé à la conversion.

    Le laisser passer ne produit pas une valeur fausse mais une valeur
    incomparable : `Decimal("NaN") > x` lève `InvalidOperation` au lieu de
    rendre `False`, et le contrôle de borne devient une erreur non métier.
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = to_decimal(str(value))
    except DomainError as exc:
        outcome.errors.append(FieldError(column, exc.code, f"« {value} » : {exc.message}"))
        return None
    except Exception:
        outcome.errors.append(
            FieldError(column, "invalid_number", f"« {value} » n'est pas un nombre valide.")
        )
        return None
    try:
        bound.check(parsed, label=column)
    except DomainError as exc:
        outcome.errors.append(FieldError(column, exc.code, exc.message))
        return None
    return parsed


def _check_unit(value: Any, outcome: ValidationOutcome) -> str | None:
    """Canonicalise l'unité et refuse l'inconnue.

    Une unité non reconnue écrite en base rend la ligne incalculable plus tard,
    au moment où on ne saura plus quoi en faire.
    """
    raw = _check_required(value, "unit_code", outcome)
    if raw is None:
        return None
    try:
        return get_unit(raw).code
    except DomainError as exc:
        outcome.errors.append(FieldError("unit_code", exc.code, exc.message))
        return None


def _check_currency(value: Any, default: str, outcome: ValidationOutcome) -> str:
    currency = (str(value).strip() if value else default).upper()
    if len(currency) != 3 or not currency.isalpha():
        outcome.errors.append(
            FieldError(
                "currency",
                "invalid_currency",
                f"Devise « {currency} » invalide : trois lettres attendues (EUR, USD…).",
            )
        )
    return currency


def _check_lead_time(value: Any, outcome: ValidationOutcome) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except Exception:
        outcome.errors.append(
            FieldError("lead_time_days", "invalid_number", f"Délai « {value} » invalide.")
        )
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        # Tronquer 1,5 jour en 1 produit une donnée fausse que personne ne
        # reverra : mieux vaut refuser et laisser corriger.
        outcome.errors.append(
            FieldError(
                "lead_time_days",
                "not_an_integer",
                f"Délai « {value} » : un nombre entier de jours est attendu.",
            )
        )
        return None
    if parsed < 0:
        outcome.errors.append(
            FieldError("lead_time_days", "negative", "Un délai ne peut pas être négatif.")
        )
        return None
    if parsed > MAX_LEAD_TIME_DAYS:
        outcome.errors.append(
            FieldError(
                "lead_time_days",
                "too_long",
                f"Délai de {parsed} jours : maximum {MAX_LEAD_TIME_DAYS} "
                "(une date a peut-être été saisie à la place d'une durée).",
            )
        )
        return None
    return int(parsed)


def _check_date_range(
    valid_from: date | None, valid_to: date | None, outcome: ValidationOutcome
) -> None:
    if valid_from and valid_to and valid_to < valid_from:
        outcome.errors.append(
            FieldError(
                "valid_to",
                "invalid_range",
                "La date de fin de validité précède la date de début.",
            )
        )


def validate_price_row(data: dict[str, Any], *, default_currency: str = "EUR") -> ValidationOutcome:
    """Valide et normalise une ligne de prix, quelle que soit son origine.

    Les trois appelants — saisie manuelle, prévisualisation, confirmation —
    passent par ici. Toute règle ajoutée s'applique donc aux trois d'un coup,
    ce qui était précisément ce qui manquait.
    """
    outcome = ValidationOutcome()

    code = _check_required(data.get("code"), "code", outcome)
    label = _check_required(data.get("label"), "label", outcome)
    for column in LENGTH_CHECKED:
        raw = code if column == "code" else label if column == "label" else data.get(column)
        _check_length(str(raw).strip() if raw else None, column, outcome)

    unit_code = _check_unit(data.get("unit_code"), outcome)
    currency = _check_currency(data.get("currency"), default_currency, outcome)

    resource_kind = (str(data.get("resource_kind") or "material")).strip().lower()
    _check_choice(resource_kind, "resource_kind", VALID_RESOURCE_KINDS, outcome)
    status = (str(data.get("status")).strip() if data.get("status") else None) or "active"
    _check_choice(status, "status", VALID_STATUS, outcome)
    confidence = (
        str(data.get("confidence")).strip() if data.get("confidence") else None
    ) or "declared"
    _check_choice(confidence, "confidence", VALID_CONFIDENCE, outcome)

    unit_price = _check_decimal(data.get("unit_price"), "unit_price", bounds.UNIT_PRICE, outcome)
    if unit_price is None and not any(e.column == "unit_price" for e in outcome.errors):
        outcome.errors.append(
            FieldError("unit_price", "required", "Le prix unitaire est obligatoire.")
        )
    min_quantity = _check_decimal(
        data.get("min_quantity"), "min_quantity", bounds.QUANTITY, outcome
    )
    lead_time_days = _check_lead_time(data.get("lead_time_days"), outcome)
    _check_date_range(data.get("valid_from"), data.get("valid_to"), outcome)

    if not outcome.is_valid:
        return outcome

    outcome.normalized = {
        "code": code,
        "label": label,
        "family": data.get("family"),
        "resource_kind": resource_kind,
        "unit_code": unit_code,
        "unit_price": unit_price,
        "currency": currency,
        "supplier_name": data.get("supplier_name"),
        "region_code": data.get("region_code"),
        "valid_from": data.get("valid_from"),
        "valid_to": data.get("valid_to"),
        "min_quantity": min_quantity,
        "lead_time_days": lead_time_days,
        "source": data.get("source"),
        "conditions": data.get("conditions"),
        "indexation": data.get("indexation"),
        "status": status,
        "confidence": confidence,
        "notes": data.get("notes"),
    }
    return outcome
