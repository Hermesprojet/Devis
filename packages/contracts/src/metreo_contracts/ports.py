"""Ports asynchrones du pipeline documentaire, sans adaptateur concret."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .documents import (
    Classification,
    DocumentRevisionRef,
    Embedding,
    ExtractedTable,
    FrozenJsonObject,
    OcrDocument,
    SearchResult,
    StructuredExtraction,
    TextSegment,
)
from .errors import InvalidContractValueError, InvalidIdentifierError


def _text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidIdentifierError(
            "Une valeur non vide est obligatoire.", field=field
        )
    return normalized


def _tenant(value: str) -> str:
    return _text(value, "organization_id")


@dataclass(frozen=True, slots=True)
class StoredObject:
    organization_id: str
    object_key: str
    content_type: str
    sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "organization_id", _tenant(self.organization_id))
        object.__setattr__(self, "object_key", _text(self.object_key, "object_key"))
        object.__setattr__(
            self, "content_type", _text(self.content_type, "content_type")
        )
        object.__setattr__(self, "sha256", _text(self.sha256, "sha256"))
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise InvalidContractValueError(
                "Le SHA-256 doit contenir 64 hexadécimaux.", field="sha256"
            )
        if isinstance(self.byte_size, bool) or self.byte_size < 0:
            raise InvalidContractValueError(
                "La taille doit être positive ou nulle.", field="byte_size"
            )


@dataclass(frozen=True, slots=True)
class StructuredLlmRequest:
    schema_name: str
    schema_version: str
    prompt_version: str
    input_data: FrozenJsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_name", _text(self.schema_name, "schema_name"))
        object.__setattr__(
            self, "schema_version", _text(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self, "prompt_version", _text(self.prompt_version, "prompt_version")
        )


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    limit: int = 10
    filters: FrozenJsonObject | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "text"))
        if isinstance(self.limit, bool) or self.limit < 1 or self.limit > 100:
            raise InvalidContractValueError(
                "La limite doit être comprise entre 1 et 100.", field="limit"
            )


@runtime_checkable
class ObjectStore(Protocol):
    async def put(
        self,
        *,
        organization_id: str,
        object_key: str,
        content_type: str,
        sha256: str,
        byte_size: int,
        chunks: AsyncIterator[bytes],
    ) -> StoredObject: ...

    def stream(
        self, *, organization_id: str, object_key: str
    ) -> AsyncIterator[bytes]: ...

    async def delete(self, *, organization_id: str, object_key: str) -> None: ...


@runtime_checkable
class OcrPort(Protocol):
    async def extract(
        self,
        *,
        organization_id: str,
        revision: DocumentRevisionRef,
        source: AsyncIterator[bytes],
        content_type: str,
        pipeline_version: str,
    ) -> OcrDocument: ...


@runtime_checkable
class TableExtractionPort(Protocol):
    async def extract(
        self, *, organization_id: str, document: OcrDocument, pipeline_version: str
    ) -> tuple[ExtractedTable, ...]: ...


@runtime_checkable
class ClassifierPort(Protocol):
    async def classify(
        self, *, organization_id: str, document: OcrDocument, pipeline_version: str
    ) -> tuple[Classification, ...]: ...


@runtime_checkable
class StructuredLlmPort(Protocol):
    async def extract(
        self, *, organization_id: str, request: StructuredLlmRequest
    ) -> StructuredExtraction: ...


@runtime_checkable
class EmbeddingPort(Protocol):
    async def embed(
        self,
        *,
        organization_id: str,
        segments: tuple[TextSegment, ...],
        pipeline_version: str,
    ) -> tuple[Embedding, ...]: ...


@runtime_checkable
class SearchPort(Protocol):
    async def search(
        self, *, organization_id: str, query: SearchQuery
    ) -> tuple[SearchResult, ...]: ...
