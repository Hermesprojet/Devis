"""Types immuables et fournisseur-indépendants du pipeline documentaire."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import TypeAlias, Union, cast

from .errors import (
    InvalidBoundingBoxError,
    InvalidCitationError,
    InvalidConfidenceError,
    InvalidContractValueError,
    InvalidIdentifierError,
    InvalidStructuredDataError,
    InvalidVersionError,
)


def _text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidIdentifierError("Une valeur non vide est obligatoire.", field=field)
    return normalized


def _positive(value: int, field: str) -> int:
    if isinstance(value, bool) or value < 1:
        raise InvalidContractValueError(
            "Un entier strictement positif est obligatoire.", field=field
        )
    return value


def _decimal(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidContractValueError("Un Decimal fini est obligatoire.", field=field)
    return value


@dataclass(frozen=True, slots=True)
class Confidence:
    """Confiance exacte ; les ``float`` sont refusés à la frontière."""

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise InvalidConfidenceError("La confiance doit être un Decimal fini.", field="value")
        if self.value < Decimal("0") or self.value > Decimal("1"):
            raise InvalidConfidenceError(
                "La confiance doit être comprise entre 0 et 1.", field="value"
            )


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Boîte normalisée, origine en haut à gauche."""

    x0: Decimal
    y0: Decimal
    x1: Decimal
    y1: Decimal

    def __post_init__(self) -> None:
        coordinates = (
            ("x0", self.x0),
            ("y0", self.y0),
            ("x1", self.x1),
            ("y1", self.y1),
        )
        for name, value in coordinates:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise InvalidBoundingBoxError(
                    "Chaque coordonnée doit être un Decimal fini.", field=name
                )
            if value < Decimal("0") or value > Decimal("1"):
                raise InvalidBoundingBoxError(
                    "Chaque coordonnée doit être comprise entre 0 et 1.", field=name
                )
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise InvalidBoundingBoxError(
                "La boîte doit vérifier x0 < x1 et y0 < y1.", field="bbox"
            )


@dataclass(frozen=True, slots=True)
class DocumentRevisionRef:
    organization_id: str
    document_id: str
    revision_id: str
    revision_number: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "organization_id", _text(self.organization_id, "organization_id"))
        object.__setattr__(self, "document_id", _text(self.document_id, "document_id"))
        object.__setattr__(self, "revision_id", _text(self.revision_id, "revision_id"))
        _positive(self.revision_number, "revision_number")


@dataclass(frozen=True, slots=True)
class ExecutionVersion:
    pipeline_version: str
    prompt_version: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "pipeline_version",
                _text(self.pipeline_version, "pipeline_version"),
            )
            if self.prompt_version is not None:
                object.__setattr__(
                    self, "prompt_version", _text(self.prompt_version, "prompt_version")
                )
            if self.model_version is not None:
                object.__setattr__(
                    self, "model_version", _text(self.model_version, "model_version")
                )
        except InvalidIdentifierError as exc:
            raise InvalidVersionError(exc.message, field=exc.field) from exc


@dataclass(frozen=True, slots=True)
class SourceCitation:
    revision: DocumentRevisionRef
    page: int
    char_start: int
    char_end: int
    bbox: BoundingBox
    extractor: str
    confidence: Confidence
    sheet: str | None = None
    layer: str | None = None
    object_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or self.page < 1:
            raise InvalidCitationError("La page doit être supérieure ou égale à 1.", field="page")
        if isinstance(self.char_start, bool) or self.char_start < 0:
            raise InvalidCitationError(
                "Le début de plage doit être supérieur ou égal à 0.", field="char_start"
            )
        if isinstance(self.char_end, bool) or self.char_end <= self.char_start:
            raise InvalidCitationError(
                "La fin de plage doit être strictement postérieure au début.",
                field="char_end",
            )
        object.__setattr__(self, "extractor", _text(self.extractor, "extractor"))
        for field_name in ("sheet", "layer", "object_id"):
            value = cast(str | None, getattr(self, field_name))
            if value is not None:
                object.__setattr__(self, field_name, _text(value, field_name))


JsonPrimitive: TypeAlias = str | int | bool | Decimal | None
JsonValue: TypeAlias = Union[JsonPrimitive, tuple["JsonValue", ...], "FrozenJsonObject"]


def _freeze_json(value: object, field: str) -> JsonValue:
    if isinstance(value, FrozenJsonObject):
        return value
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvalidStructuredDataError("Un Decimal structuré doit être fini.", field=field)
        return value
    if isinstance(value, float):
        raise InvalidStructuredDataError("Les float sont interdits dans les contrats.", field=field)
    if isinstance(value, Mapping):
        return FrozenJsonObject.from_mapping(cast(Mapping[str, object], value), field=field)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item, field) for item in value)
    raise InvalidStructuredDataError("Type de donnée structurée non pris en charge.", field=field)


@dataclass(frozen=True, slots=True)
class FrozenJsonObject:
    """Objet JSON profondément immuable, sans ``float``."""

    items: tuple[tuple[str, JsonValue], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise InvalidStructuredDataError(
                "Les éléments JSON doivent être immuables.", field="data"
            )
        normalized: list[tuple[str, JsonValue]] = []
        seen: set[str] = set()
        for item in self.items:
            if not isinstance(item, tuple) or len(item) != 2:
                raise InvalidStructuredDataError(
                    "Un membre JSON doit être une paire.", field="data"
                )
            key, raw_value = item
            if not isinstance(key, str) or not key or key in seen:
                raise InvalidStructuredDataError(
                    "Les clés JSON doivent être uniques et non vides.", field="data"
                )
            seen.add(key)
            normalized.append((key, _freeze_json(raw_value, "data")))
        normalized.sort(key=lambda member: member[0])
        object.__setattr__(self, "items", tuple(normalized))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], *, field: str = "data") -> FrozenJsonObject:
        frozen: list[tuple[str, JsonValue]] = []
        seen: set[str] = set()
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise InvalidStructuredDataError(
                    "Les clés JSON doivent être des chaînes non vides.", field=field
                )
            if raw_key in seen:
                raise InvalidStructuredDataError("Une clé JSON est dupliquée.", field=field)
            seen.add(raw_key)
            frozen.append((raw_key, _freeze_json(raw_value, field)))
        frozen.sort(key=lambda item: item[0])
        return cls(tuple(frozen))


@dataclass(frozen=True, slots=True)
class TextSegment:
    segment_id: str
    text: str
    citation: SourceCitation

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_id", _text(self.segment_id, "segment_id"))
        if not self.text:
            raise InvalidContractValueError("Le texte du segment est obligatoire.", field="text")


@dataclass(frozen=True, slots=True)
class OcrPage:
    revision: DocumentRevisionRef
    page: int
    text: str
    segments: tuple[TextSegment, ...]
    language: str | None
    confidence: Confidence
    version: ExecutionVersion

    def __post_init__(self) -> None:
        _positive(self.page, "page")
        if not self.text:
            raise InvalidContractValueError(
                "Le texte OCR de la page est obligatoire.", field="text"
            )
        object.__setattr__(self, "segments", tuple(self.segments))
        if self.language is not None:
            object.__setattr__(self, "language", _text(self.language, "language"))
        for segment in self.segments:
            if segment.citation.revision != self.revision or segment.citation.page != self.page:
                raise InvalidCitationError(
                    "Chaque segment OCR doit citer sa propre page et révision.",
                    field="segments",
                )


@dataclass(frozen=True, slots=True)
class OcrDocument:
    revision: DocumentRevisionRef
    pages: tuple[OcrPage, ...]
    version: ExecutionVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "pages", tuple(self.pages))
        if not self.pages:
            raise InvalidContractValueError(
                "Un résultat OCR doit contenir une page.", field="pages"
            )
        numbers: set[int] = set()
        for page in self.pages:
            if page.revision != self.revision:
                raise InvalidCitationError(
                    "Toutes les pages OCR doivent viser la même révision.",
                    field="pages",
                )
            if page.page in numbers:
                raise InvalidContractValueError("Une page OCR est dupliquée.", field="pages")
            numbers.add(page.page)
            if page.version != self.version:
                raise InvalidVersionError(
                    "Chaque page OCR doit porter la version du document.", field="pages"
                )


@dataclass(frozen=True, slots=True)
class TableCell:
    row: int
    column: int
    text: str
    citation: SourceCitation
    confidence: Confidence
    row_span: int = 1
    column_span: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.row, bool) or self.row < 0:
            raise InvalidContractValueError("La ligne doit être positive ou nulle.", field="row")
        if isinstance(self.column, bool) or self.column < 0:
            raise InvalidContractValueError(
                "La colonne doit être positive ou nulle.", field="column"
            )
        _positive(self.row_span, "row_span")
        _positive(self.column_span, "column_span")


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    table_id: str
    revision: DocumentRevisionRef
    cells: tuple[TableCell, ...]
    citations: tuple[SourceCitation, ...]
    confidence: Confidence
    version: ExecutionVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "table_id", _text(self.table_id, "table_id"))
        object.__setattr__(self, "cells", tuple(self.cells))
        object.__setattr__(self, "citations", tuple(self.citations))
        if not self.cells or not self.citations:
            raise InvalidCitationError(
                "Un tableau doit avoir des cellules et des citations.",
                field="citations",
            )
        if any(citation.revision != self.revision for citation in self.citations):
            raise InvalidCitationError("Une citation vise une autre révision.", field="citations")


@dataclass(frozen=True, slots=True)
class Classification:
    category: str
    confidence: Confidence
    citations: tuple[SourceCitation, ...]
    version: ExecutionVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _text(self.category, "category"))
        object.__setattr__(self, "citations", tuple(self.citations))
        if not self.citations:
            raise InvalidCitationError(
                "Une classification doit avoir au moins une citation.",
                field="citations",
            )
        organization_id = self.citations[0].revision.organization_id
        if any(citation.revision.organization_id != organization_id for citation in self.citations):
            raise InvalidCitationError(
                "Les citations d'une classification doivent appartenir au même tenant.",
                field="citations",
            )


@dataclass(frozen=True, slots=True)
class StructuredExtraction:
    schema_name: str
    schema_version: str
    data: FrozenJsonObject
    confidence: Confidence
    citations: tuple[SourceCitation, ...]
    version: ExecutionVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_name", _text(self.schema_name, "schema_name"))
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        object.__setattr__(self, "citations", tuple(self.citations))
        if not self.citations:
            raise InvalidCitationError(
                "Une extraction structurée doit avoir au moins une citation.",
                field="citations",
            )
        organization_id = self.citations[0].revision.organization_id
        if any(citation.revision.organization_id != organization_id for citation in self.citations):
            raise InvalidCitationError(
                "Les citations d'une extraction doivent appartenir au même tenant.",
                field="citations",
            )


@dataclass(frozen=True, slots=True)
class Embedding:
    segment_id: str
    vector: tuple[Decimal, ...]
    version: ExecutionVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_id", _text(self.segment_id, "segment_id"))
        object.__setattr__(self, "vector", tuple(self.vector))
        if not self.vector:
            raise InvalidContractValueError("Un embedding ne peut pas être vide.", field="vector")
        for value in self.vector:
            _decimal(value, "vector")


@dataclass(frozen=True, slots=True)
class SearchResult:
    segment: TextSegment
    score: Confidence
    version: ExecutionVersion


def _json_value_to_primitive(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _json_value_to_primitive(item) for key, item in value.items}
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, tuple):
        return [_json_value_to_primitive(item) for item in value]
    return value


def to_primitive(value: object) -> object:
    """Sérialiser un contrat en primitives sans convertir les Decimal en float."""

    if isinstance(value, FrozenJsonObject):
        return _json_value_to_primitive(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [to_primitive(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise InvalidStructuredDataError("Objet non sérialisable par le contrat.", field="value")
