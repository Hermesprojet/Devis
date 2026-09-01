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
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ImportBatch, ImportBatchRow, PriceItem
from . import classeur
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

#: Libellés français du type de ressource, traduits **avant** le contrat.
#: C'est une conversion de syntaxe propre au CSV, comme « 31/12/2026 » : un
#: fichier rédigé en français doit être compris, mais l'API, elle, attend la
#: valeur canonique — son `Literal` refuserait « matériau ». Traduire ici garde
#: les deux parcours d'accord sur ce qu'ils écrivent en base.
RESOURCE_KIND_ALIASES = {
    "materiau": "material",
    "matériau": "material",
    "materiaux": "material",
    "matériaux": "material",
    "main_doeuvre": "labor",
    "main-d'oeuvre": "labor",
    "main d'oeuvre": "labor",
    "mo": "labor",
    "engin": "equipment",
    "engins": "equipment",
    "materiel": "equipment",
    "matériel": "equipment",
    "transport": "transport",
    "evacuation": "disposal",
    "évacuation": "disposal",
    "traitement": "disposal",
    "sous_traitance": "subcontract",
    "sous-traitance": "subcontract",
    "divers": "other",
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


#: Formats de date qu'un fichier belge ou français utilise couramment. La
#: conversion de syntaxe appartient au CSV ; la validation métier — plage,
#: cohérence — reste au contrat partagé.
LOCAL_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


def coerce_local_date(value: str) -> str:
    """Traduit une date locale en ISO, ou rend la valeur telle quelle.

    Ne juge rien : une chaîne qu'aucun format ne reconnaît est transmise sans
    modification, et c'est le contrat qui la refusera avec un message. Deux
    validateurs pour une même règle finiraient par diverger.
    """
    text = (value or "").strip()
    if not text:
        return text
    for fmt in LOCAL_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


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


@dataclass
class Lecture:
    """Ce qu'un lecteur rend, quel que soit le format qu'il a lu.

    C'est la FRONTIÈRE de la convergence. En amont vivent les questions
    propres à un format — encodage et séparateur pour le CSV, archive et
    feuille pour le classeur. En aval, plus rien ne sait d'où vient la donnée :
    correspondance des colonnes, dates locales, alias de ressource, contrat
    partagé, doublons et staging s'appliquent une seule fois, pour les deux.

    Deux pipelines auraient divergé au premier alias ajouté, et l'utilisateur
    aurait obtenu deux résultats pour un même tableau selon qu'il l'enregistre
    en `.csv` ou en `.xlsx`.
    """

    headers: list[str]
    #: Une ligne = en-tête brut → texte. Du TEXTE, comme un CSV en rend : c'est
    #: au lecteur de classeur de rendre ses nombres et ses dates sous cette
    #: forme, pas à la normalisation de connaître deux jeux de types.
    rows: list[dict[str, str]]
    #: Le rang tel que l'utilisateur le voit dans son fichier. Le CSV compte les
    #: lignes, le classeur compte les rangs, et une ligne vide au milieu les
    #: désaccorderait si on recalculait ici.
    line_numbers: list[int]
    #: Ce que le format seul sait dire : encodage et séparateur, ou feuille.
    meta: dict[str, Any] = field(default_factory=dict)


def lire_le_csv(payload: bytes) -> Lecture:
    """Décodage, séparateur, en-têtes : tout ce qui est propre au CSV."""
    text, encoding = _decode(payload)
    if not text.strip():
        return Lecture(
            headers=[],
            rows=[],
            line_numbers=[],
            meta={"format": "csv", "encoding": encoding, "delimiter": None, "fatal": "empty_file"},
        )

    delimiter = _sniff_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h is not None]

    rows: list[dict[str, str]] = []
    line_numbers: list[int] = []
    for offset, raw_row in enumerate(reader):
        rows.append({k: (v if v is not None else "") for k, v in raw_row.items() if k is not None})
        line_numbers.append(offset + 2)  # 1-based, header is line 1

    return Lecture(
        headers=headers,
        rows=rows,
        line_numbers=line_numbers,
        meta={"format": "csv", "encoding": encoding, "delimiter": delimiter, "fatal": None},
    )


def normaliser(
    lecture: Lecture,
    *,
    column_mapping: dict[str, str] | None = None,
    default_currency: str = "EUR",
) -> tuple[list[ParsedRow], dict[str, Any]]:
    """La normalisation, une seule fois, pour tous les formats."""
    headers = lecture.headers
    detected_mapping, unmapped = build_column_mapping(headers)
    mapping = {**detected_mapping, **(column_mapping or {})}
    mapped_targets = set(mapping.values())
    missing_required = [c for c in REQUIRED_COLUMNS if c not in mapped_targets]

    meta: dict[str, Any] = {
        "encoding": None,
        "delimiter": None,
        **lecture.meta,
        "headers": headers,
        "mapping": mapping,
        "unmapped_headers": unmapped,
        "missing_required_columns": missing_required,
    }
    if lecture.meta.get("fatal"):
        meta["missing_required_columns"] = list(REQUIRED_COLUMNS)
        return [], meta
    meta["fatal"] = "missing_required_columns" if missing_required else None
    if missing_required:
        return [], meta

    rows: list[ParsedRow] = []
    seen_codes: dict[str, int] = {}

    for raw, line_number in zip(lecture.rows, lecture.line_numbers, strict=True):
        errors: list[RowError] = []
        values: dict[str, Any] = {}
        for header, canonical in mapping.items():
            values[canonical] = (raw.get(header) or "").strip()

        if not any(v for v in values.values()):
            continue  # blank line

        # Toute règle métier vit dans le contrat partagé. Ce qui reste ici est
        # propre à l'IMPORT, quel qu'en soit le format : dates écrites à la
        # locale, alias de type de ressource, et doublons DANS LE FICHIER — que
        # le contrat ne peut pas voir, n'ayant qu'une ligne sous les yeux.
        for column in ("valid_from", "valid_to"):
            if values.get(column):
                values[column] = coerce_local_date(values[column])
        if values.get("resource_kind"):
            key = values["resource_kind"].strip().lower()
            values["resource_kind"] = RESOURCE_KIND_ALIASES.get(key, key)

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


#: Les en-têtes du modèle proposé au téléchargement, en FRANÇAIS.
#:
#: Le nom canonique de chaque colonne est anglais — c'est la clé interne — mais
#: le modèle s'adresse à une entreprise belge francophone qui va le remplir à
#: la main. Lui servir « unit_price » quand l'écran dit « prix unitaire »
#: l'obligerait à traduire pour retrouver sa colonne.
#:
#: Chaque nom est un alias RECONNU : `test_le_modele_telecharge_est_un_classeur`
#: relit le modèle servi et vérifie qu'aucune colonne n'est laissée de côté. Et
#: `test_les_deux_modeles_annoncent_les_memes_colonnes` le tient identique au
#: modèle CSV — deux modèles divergents pour un même import feraient douter
#: lequel dit vrai.
COLONNES_DU_MODELE: tuple[str, ...] = (
    "code",
    "libelle",
    "famille",
    "type",
    "unite",
    "prix_unitaire",
    "devise",
    "fournisseur",
    "region",
    "valide_du",
    "valide_au",
    "quantite_min",
    "delai",
    "source",
    "conditions",
    "indexation",
    "statut",
    "confiance",
    "notes",
)


def colonnes_du_modele() -> list[str]:
    return list(COLONNES_DU_MODELE)


def modele_xlsx() -> bytes:
    """Le classeur vide proposé à qui n'a pas encore de barème au bon format."""
    return classeur.ecrire_un_modele(colonnes_du_modele())


def lire_le_classeur(payload: bytes, *, feuille: str | None = None) -> Lecture:
    """Le classeur, ramené à la forme que rend le lecteur CSV.

    Tout ce qui pouvait refuser le fichier — signature, bornes de l'archive,
    macros, formules, liens externes — a déjà été prononcé par `classeur.lire`.
    Ce qui arrive ici est un tableau de texte, et rien d'autre.
    """
    lue, meta = classeur.lire(payload, feuille=feuille)
    return Lecture(
        headers=lue.headers,
        rows=lue.lignes,
        line_numbers=lue.rangs,
        meta={**meta, "fatal": None},
    )


def lire(payload: bytes, *, feuille: str | None = None) -> Lecture:
    """Choisit le lecteur d'après le CONTENU du fichier, jamais d'après son nom.

    Un `.csv` renommé `.xlsx` est courant, et l'inverse aussi. Choisir sur le
    nom enverrait un texte au lecteur d'archive, qui échouerait par une erreur
    illisible au lieu d'un refus clair — ou pire, enverrait une archive au
    lecteur CSV, qui rendrait des lignes de charabia sans rien refuser.
    """
    if classeur.detecter_le_format(payload) == "csv":
        return lire_le_csv(payload)
    return lire_le_classeur(payload, feuille=feuille)


def parse_csv(
    payload: bytes,
    *,
    column_mapping: dict[str, str] | None = None,
    default_currency: str = "EUR",
    feuille: str | None = None,
) -> tuple[list[ParsedRow], dict[str, Any]]:
    """Lit un fichier de prix — CSV ou classeur — et rend chaque ligne validée.

    Le nom reste celui d'avant : il est cité par les appelants et par les tests,
    et le changer aurait été un renommage de plus dans un changement qui en
    porte déjà assez. Ce qu'il fait, lui, a changé : il choisit le lecteur.
    """
    return normaliser(
        lire(payload, feuille=feuille),
        column_mapping=column_mapping,
        default_currency=default_currency,
    )


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
    feuille: str | None = None,
) -> tuple[ImportBatch, dict[str, Any]]:
    """Validate a file and persist it as a staging batch."""
    rows, meta = parse_csv(
        payload,
        column_mapping=column_mapping,
        default_currency=default_currency,
        feuille=feuille,
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
