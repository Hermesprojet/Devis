"""CSV/XLSX-style import of a price library, in two explicit steps.

``preview`` parses, validates and stores a staging batch. **No row reaches the
price library at this point.** ``commit`` then writes only the valid rows, using
the strategy the user picked for rows that already exist.

Acceptance scenario 2 of the product brief is implemented literally: a file with
five valid rows and two broken ones shows the two errors before anything is
written, and the confirmed import creates exactly five items.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain import bounds
from metreo_domain.bounds import OutOfBoundsError
from metreo_domain.errors import DomainError
from metreo_domain.money import to_decimal

from ..models import ImportBatch, ImportBatchRow, PriceItem
from .price_contract import validate_price_row

ImportStrategy = Literal["create", "replace", "ignore", "merge"]

#: Canonical column -> accepted header spellings (FR / NL / EN), lower-cased and
#: stripped of accents-insensitive noise by :func:`_normalise_header`.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("code", "reference", "référence", "ref", "artikelcode"),
    "label": (
        "label",
        "libelle",
        "libellé",
        "designation",
        "désignation",
        "omschrijving",
        "description",
    ),
    "family": ("family", "famille", "categorie", "catégorie", "familie", "category"),
    "resource_kind": ("resource_kind", "type", "nature", "type_ressource", "soort"),
    "unit_code": ("unit_code", "unit", "unite", "unité", "u", "eenheid"),
    "unit_price": ("unit_price", "prix", "prix_unitaire", "pu", "eenheidsprijs", "price"),
    "currency": ("currency", "devise", "munt"),
    "supplier_name": ("supplier_name", "fournisseur", "supplier", "leverancier"),
    "region_code": ("region_code", "region", "région", "zone", "regio"),
    "valid_from": ("valid_from", "valide_du", "date_debut", "date_début", "geldig_van"),
    "valid_to": ("valid_to", "valide_au", "date_fin", "geldig_tot"),
    "min_quantity": ("min_quantity", "quantite_min", "quantité_min", "qte_min", "min_qty"),
    "lead_time_days": ("lead_time_days", "delai", "délai", "delai_jours", "levertijd"),
    "source": ("source", "origine", "bron"),
    "conditions": ("conditions", "condition", "voorwaarden"),
    "indexation": ("indexation", "index", "revision", "révision"),
    "status": ("status", "statut", "etat", "état"),
    "confidence": ("confidence", "confiance", "niveau_confiance"),
    "notes": ("notes", "note", "remarque", "commentaire", "opmerking"),
}

REQUIRED_COLUMNS: tuple[str, ...] = ("code", "label", "unit_code", "unit_price")

VALID_RESOURCE_KINDS = {
    "material",
    "labor",
    "equipment",
    "transport",
    "disposal",
    "subcontract",
    "other",
}


def _normalise_header(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def build_column_mapping(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map file headers onto canonical columns.

    Returns the mapping and the list of headers that were not recognised. An
    unrecognised header is not an error — it is shown to the user so they can
    map it by hand, which is what the onboarding screen does.
    """
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for header in headers:
        normalised = _normalise_header(header)
        for canonical, aliases in COLUMN_ALIASES.items():
            if normalised == canonical or normalised in aliases:
                mapping[header] = canonical
                break
        else:
            unmapped.append(header)
    return mapping, unmapped


def _decode(payload: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace"), "utf-8/replace"


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ";,\t|"}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ";"


@dataclass
class RowError:
    column: str | None
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"column": self.column, "code": self.code, "message": self.message}


@dataclass
class ParsedRow:
    line_number: int
    raw: dict[str, str]
    normalized: dict[str, Any] | None
    errors: list[RowError] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of: str | None = None

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _parse_decimal(
    value: str,
    column: str,
    errors: list[RowError],
    *,
    bound: bounds.Bound | None = None,
) -> Decimal | None:
    """Convertit et **borne**, comme le fait la saisie manuelle.

    L'import contournait les bornes des schémas Pydantic : un prix de 1e20 ou
    un `Infinity` entraient sans un mot. `DomainError` est rattrapée en plus
    des erreurs arithmétiques parce que `to_decimal` refuse désormais les
    valeurs non finies — sans cela, un `Infinity` dans une cellule ferait
    échouer tout le fichier au lieu de la seule ligne fautive.
    """
    if value is None or not str(value).strip():
        return None
    try:
        parsed = to_decimal(str(value))
    except DomainError as exc:
        errors.append(RowError(column, exc.code, f"« {value} » : {exc.message}"))
        return None
    except (InvalidOperation, ArithmeticError, ValueError, TypeError):
        errors.append(
            RowError(column, "invalid_number", f"« {value} » n'est pas un nombre valide.")
        )
        return None
    if bound is not None:
        try:
            bound.check(parsed, label=column)
        except OutOfBoundsError as exc:
            errors.append(RowError(column, exc.code, exc.message))
            return None
    return parsed


#: Valeurs acceptées pour les colonnes énumérées de `PriceItem`.
VALID_STATUS = frozenset({"active", "draft", "archived", "superseded"})
VALID_CONFIDENCE = frozenset({"declared", "quoted", "contracted", "estimated"})


def sql_length(column: str) -> int:
    """Longueur maximale **lue sur la colonne**, jamais recopiée à la main.

    La première version de ce contrôle portait des constantes écrites de
    mémoire : `family` était vérifié à 120 pour une colonne `String(60)`, et
    `region_code` à 20 pour un `String(10)`. Une ligne passait donc la
    prévisualisation puis échouait à l'écriture. Lire la longueur sur le modèle
    supprime la classe entière de ce défaut : une migration qui change la
    colonne change le contrôle.
    """
    sql_column = PriceItem.__table__.columns[column]
    length = getattr(sql_column.type, "length", None)
    if length is None:  # pragma: no cover - colonne Text, sans limite
        raise KeyError(f"La colonne {column} ne porte pas de longueur.")
    return int(length)


def _check_length(value: str | None, column: str, errors: list[RowError]) -> None:
    """Une valeur trop longue échoue autrement à l'écriture — erreur SQL sur
    PostgreSQL, troncature silencieuse sur SQLite. Les deux sont pires qu'un
    refus de ligne."""
    maximum = sql_length(column)
    if value and len(value) > maximum:
        errors.append(
            RowError(
                column,
                "too_long",
                f"« {value[:20]}… » fait {len(value)} caractères, maximum {maximum}.",
            )
        )


def _check_choice(
    value: str | None, column: str, allowed: frozenset[str], errors: list[RowError]
) -> None:
    """Une énumération non contrôlée écrit n'importe quelle chaîne en base."""
    if value and value not in allowed:
        errors.append(
            RowError(
                column,
                f"invalid_{column}",
                f"« {value} » n'est pas une valeur acceptée. Attendu : "
                + ", ".join(sorted(allowed))
                + ".",
            )
        )


def _parse_date(value: str, column: str, errors: list[RowError]) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    errors.append(
        RowError(column, "invalid_date", f"« {text} » n'est pas une date (AAAA-MM-JJ attendu).")
    )
    return None


def _serialise_for_staging(normalized: dict[str, Any]) -> dict[str, Any]:
    """Rend la ligne normalisée stockable en JSON.

    Les `Decimal` et les `date` ne survivent pas à un aller-retour JSON : ils
    sont écrits en texte, et le contrat les relira à la confirmation.
    """
    serialised: dict[str, Any] = {}
    for key, value in normalized.items():
        if isinstance(value, Decimal):
            serialised[key] = str(value)
        elif isinstance(value, date):
            serialised[key] = value.isoformat()
        else:
            serialised[key] = value
    return serialised


def parse_csv(
    payload: bytes,
    *,
    column_mapping: dict[str, str] | None = None,
    default_currency: str = "EUR",
) -> tuple[list[ParsedRow], dict[str, Any]]:
    """Parse and validate the whole file, returning every row with its errors."""
    text, encoding = _decode(payload)
    if not text.strip():
        return [], {
            "encoding": encoding,
            "delimiter": None,
            "headers": [],
            "mapping": {},
            "unmapped_headers": [],
            "missing_required_columns": list(REQUIRED_COLUMNS),
            "fatal": "empty_file",
        }

    delimiter = _sniff_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    detected_mapping, unmapped = build_column_mapping(headers)
    mapping = {**detected_mapping, **(column_mapping or {})}
    mapped_targets = set(mapping.values())
    missing_required = [c for c in REQUIRED_COLUMNS if c not in mapped_targets]

    meta: dict[str, Any] = {
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "mapping": mapping,
        "unmapped_headers": unmapped,
        "missing_required_columns": missing_required,
        "fatal": "missing_required_columns" if missing_required else None,
    }
    if missing_required:
        return [], meta

    rows: list[ParsedRow] = []
    seen_codes: dict[str, int] = {}

    for offset, raw_row in enumerate(reader):
        line_number = offset + 2  # 1-based, header is line 1
        raw = {k: (v if v is not None else "") for k, v in raw_row.items() if k is not None}
        errors: list[RowError] = []
        values: dict[str, Any] = {}
        for header, canonical in mapping.items():
            values[canonical] = (raw.get(header) or "").strip()

        if not any(v for v in values.values()):
            continue  # blank line

        # Toute règle métier vit dans le contrat partagé. Ce qui reste ici est
        # propre au CSV : décodage, séparateur, correspondance des colonnes,
        # doublons DANS LE FICHIER — que le contrat ne peut pas voir, n'ayant
        # qu'une ligne sous les yeux.
        outcome = validate_price_row(values, default_currency=default_currency)
        errors = [RowError(e.column, e.code, e.message) for e in outcome.errors]

        code = values.get("code", "").strip()
        duplicate_in_file = False
        if code:
            if code in seen_codes:
                duplicate_in_file = True
                errors.append(
                    RowError(
                        "code",
                        "duplicate_in_file",
                        f"Code déjà présent ligne {seen_codes[code]} du même fichier.",
                    )
                )
            else:
                seen_codes[code] = line_number

        # La ligne de staging porte les valeurs NORMALISÉES, pas les cellules
        # brutes : « m³ » y devient « m3 » et « eur » devient « EUR ». Stocker
        # le brut obligerait la confirmation à renormaliser, et les deux
        # normalisations finiraient par diverger.
        normalized = _serialise_for_staging(outcome.normalized) if not errors else None

        rows.append(
            ParsedRow(
                line_number=line_number,
                raw=raw,
                normalized=normalized,
                errors=errors,
                is_duplicate=duplicate_in_file,
            )
        )

    return rows, meta


def create_preview(
    session: Session,
    *,
    organization_id: str,
    price_book_version_id: str,
    filename: str,
    payload: bytes,
    strategy: ImportStrategy = "create",
    column_mapping: dict[str, str] | None = None,
    default_currency: str = "EUR",
    created_by: str | None = None,
) -> tuple[ImportBatch, dict[str, Any]]:
    """Validate a file and persist it as a staging batch."""
    rows, meta = parse_csv(
        payload, column_mapping=column_mapping, default_currency=default_currency
    )

    existing_codes = set(
        session.scalars(
            select(PriceItem.code).where(
                PriceItem.organization_id == organization_id,
                PriceItem.price_book_version_id == price_book_version_id,
            )
        ).all()
    )
    for row in rows:
        if row.normalized and row.normalized["code"] in existing_codes:
            row.is_duplicate = True
            row.duplicate_of = row.normalized["code"]

    batch = ImportBatch(
        organization_id=organization_id,
        price_book_version_id=price_book_version_id,
        filename=filename,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        status="previewed",
        strategy=strategy,
        row_count=len(rows),
        valid_count=sum(1 for r in rows if r.is_valid),
        error_count=sum(1 for r in rows if not r.is_valid),
        duplicate_count=sum(1 for r in rows if r.is_duplicate and r.is_valid),
        column_mapping=meta["mapping"],
        created_by=created_by,
    )
    session.add(batch)
    session.flush()

    for row in rows:
        session.add(
            ImportBatchRow(
                batch_id=batch.id,
                line_number=row.line_number,
                raw=row.raw,
                normalized=row.normalized,
                is_valid=row.is_valid,
                is_duplicate=row.is_duplicate,
                errors=[e.to_dict() for e in row.errors],
            )
        )
    session.flush()
    return batch, meta


def validate_normalized(data: dict[str, Any]) -> list[RowError]:
    """Revalide une ligne de staging avant écriture, par le contrat unique.

    Prévisualiser et confirmer sont deux requêtes séparées par un temps
    arbitraire. Entre les deux, une contrainte a pu changer, une migration a pu
    raccourcir une colonne, ou la ligne de staging a pu être altérée. Une
    version antérieure ne revérifiait que les longueurs et le prix : l'unité,
    la devise, le type de ressource, la plage de dates et la quantité minimale
    passaient sans contrôle.
    """
    outcome = validate_price_row(data)
    return [RowError(e.column, e.code, e.message) for e in outcome.errors]


def commit_batch(
    session: Session,
    batch: ImportBatch,
    *,
    strategy: ImportStrategy | None = None,
    is_demo_data: bool = False,
) -> dict[str, Any]:
    """Write the valid rows of a previewed batch into the price library.

    Strategies for a code that already exists in the target version:
    ``create`` skips it and reports a conflict, ``replace`` overwrites every
    field, ``ignore`` keeps the existing row silently, ``merge`` fills only the
    fields the file provides and leaves the others untouched.
    """
    if batch.status != "previewed":
        raise ValueError(f"batch is {batch.status}, expected 'previewed'")

    effective_strategy = strategy or batch.strategy
    created = updated = skipped = conflicted = 0
    details: list[dict[str, Any]] = []

    existing = {
        item.code: item
        for item in session.scalars(
            select(PriceItem).where(
                PriceItem.organization_id == batch.organization_id,
                PriceItem.price_book_version_id == batch.price_book_version_id,
            )
        ).all()
    }

    rejected_at_commit: list[dict[str, Any]] = []

    for row in batch.rows:
        if not row.is_valid or not row.normalized:
            continue
        data = dict(row.normalized)

        outcome = validate_price_row(data)
        late_errors = [RowError(e.column, e.code, e.message) for e in outcome.errors]
        if late_errors:
            rejected_at_commit.append(
                {
                    "line_number": row.line_number,
                    "code": data.get("code"),
                    "outcome": "rejected_at_commit",
                    "errors": [e.to_dict() for e in late_errors],
                }
            )
            details.append(rejected_at_commit[-1])
            continue

        # Écrire ce que le CONTRAT a normalisé, pas le dictionnaire d'origine.
        # Sans cela, « m³ », « eur » et les espaces de bordure atteignaient les
        # colonnes tels quels, malgré une validation qui les avait corrigés.
        data = outcome.normalized
        code = data["code"]
        current = existing.get(code)

        if current is None:
            item = PriceItem(
                organization_id=batch.organization_id,
                price_book_version_id=batch.price_book_version_id,
                is_demo_data=is_demo_data,
                **_to_columns(data),
            )
            session.add(item)
            existing[code] = item
            created += 1
            continue

        if effective_strategy == "create":
            conflicted += 1
            details.append({"line_number": row.line_number, "code": code, "outcome": "conflict"})
            continue
        if effective_strategy == "ignore":
            skipped += 1
            continue
        columns = _to_columns(data)
        if effective_strategy == "merge":
            columns = {k: v for k, v in columns.items() if v not in (None, "")}
        for key, value in columns.items():
            setattr(current, key, value)
        updated += 1

    batch.status = "committed"
    batch.strategy = effective_strategy
    from ..models import utcnow

    batch.committed_at = utcnow()
    session.flush()

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "conflicted": conflicted,
        # Une ligne jugée valide à la prévisualisation et refusée à l'écriture
        # doit être visible : la taire ferait croire à un import complet.
        "rejected_at_commit": len(rejected_at_commit),
        "strategy": effective_strategy,
        "details": details,
    }


def _to_columns(data: dict[str, Any]) -> dict[str, Any]:
    """Projette une ligne **déjà normalisée par le contrat** vers les colonnes.

    Aucune conversion défensive ici, et surtout aucun `date.fromisoformat` :
    une chaîne illisible lèverait une `ValueError` à l'écriture, donc un 500,
    alors qu'elle doit produire une erreur de champ dans le contrat. Cette
    fonction ne fait plus que renommer et recopier.
    """
    return {key: data[key] for key in _COLUMN_FIELDS}


#: Champs portés par une ligne de prix normalisée, dans l'ordre des colonnes.
_COLUMN_FIELDS = (
    "code",
    "label",
    "family",
    "resource_kind",
    "unit_code",
    "unit_price",
    "currency",
    "supplier_name",
    "region_code",
    "valid_from",
    "valid_to",
    "min_quantity",
    "lead_time_days",
    "source",
    "conditions",
    "indexation",
    "status",
    "confidence",
    "notes",
)


def batch_report(batch: ImportBatch, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "sha256": batch.sha256,
        "status": batch.status,
        "strategy": batch.strategy,
        "row_count": batch.row_count,
        "valid_count": batch.valid_count,
        "error_count": batch.error_count,
        "duplicate_count": batch.duplicate_count,
        "column_mapping": batch.column_mapping,
        "meta": meta or {},
        "rows": [
            {
                "line_number": row.line_number,
                "is_valid": row.is_valid,
                "is_duplicate": row.is_duplicate,
                "errors": row.errors,
                "normalized": row.normalized,
                "raw": row.raw,
            }
            for row in batch.rows
        ],
    }
