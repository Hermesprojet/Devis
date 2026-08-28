import inspect
from collections.abc import AsyncIterator

from metreo_contracts import (
    Classification,
    ClassifierPort,
    DocumentRevisionRef,
    Embedding,
    EmbeddingPort,
    ExtractedTable,
    ObjectStore,
    OcrDocument,
    OcrPort,
    SearchPort,
    SearchQuery,
    SearchResult,
    StoredObject,
    StructuredExtraction,
    StructuredLlmPort,
    StructuredLlmRequest,
    TableExtractionPort,
    TextSegment,
)


async def empty_stream() -> AsyncIterator[bytes]:
    if False:
        yield b""


class FakeObjectStore:
    async def put(
        self,
        *,
        organization_id: str,
        object_key: str,
        content_type: str,
        sha256: str,
        byte_size: int,
        chunks: AsyncIterator[bytes],
    ) -> StoredObject:
        async for _chunk in chunks:
            pass
        return StoredObject(organization_id, object_key, content_type, sha256, byte_size)

    def stream(self, *, organization_id: str, object_key: str) -> AsyncIterator[bytes]:
        return empty_stream()

    async def delete(self, *, organization_id: str, object_key: str) -> None:
        return None


class FakeOcr:
    async def extract(
        self,
        *,
        organization_id: str,
        revision: DocumentRevisionRef,
        source: AsyncIterator[bytes],
        content_type: str,
        pipeline_version: str,
    ) -> OcrDocument:
        raise NotImplementedError


class FakeTables:
    async def extract(
        self, *, organization_id: str, document: OcrDocument, pipeline_version: str
    ) -> tuple[ExtractedTable, ...]:
        return ()


class FakeClassifier:
    async def classify(
        self, *, organization_id: str, document: OcrDocument, pipeline_version: str
    ) -> tuple[Classification, ...]:
        return ()


class FakeStructuredLlm:
    async def extract(
        self, *, organization_id: str, request: StructuredLlmRequest
    ) -> StructuredExtraction:
        raise NotImplementedError


class FakeEmbedding:
    async def embed(
        self,
        *,
        organization_id: str,
        segments: tuple[TextSegment, ...],
        pipeline_version: str,
    ) -> tuple[Embedding, ...]:
        return ()


class FakeSearch:
    async def search(self, *, organization_id: str, query: SearchQuery) -> tuple[SearchResult, ...]:
        return ()


# Ces affectations sont vérifiées par mypy : contrairement à
# ``runtime_checkable``, elles contrôlent aussi les signatures complètes.
OBJECT_STORE: ObjectStore = FakeObjectStore()
OCR_PORT: OcrPort = FakeOcr()
TABLE_PORT: TableExtractionPort = FakeTables()
CLASSIFIER_PORT: ClassifierPort = FakeClassifier()
STRUCTURED_LLM_PORT: StructuredLlmPort = FakeStructuredLlm()
EMBEDDING_PORT: EmbeddingPort = FakeEmbedding()
SEARCH_PORT: SearchPort = FakeSearch()


def test_fakes_satisfy_the_seven_runtime_protocols() -> None:
    assert isinstance(FakeObjectStore(), ObjectStore)
    assert isinstance(FakeOcr(), OcrPort)
    assert isinstance(FakeTables(), TableExtractionPort)
    assert isinstance(FakeClassifier(), ClassifierPort)
    assert isinstance(FakeStructuredLlm(), StructuredLlmPort)
    assert isinstance(FakeEmbedding(), EmbeddingPort)
    assert isinstance(FakeSearch(), SearchPort)


def test_every_port_operation_requires_an_explicit_tenant() -> None:
    operations = (
        ObjectStore.put,
        ObjectStore.stream,
        ObjectStore.delete,
        OcrPort.extract,
        TableExtractionPort.extract,
        ClassifierPort.classify,
        StructuredLlmPort.extract,
        EmbeddingPort.embed,
        SearchPort.search,
    )
    for operation in operations:
        parameter = inspect.signature(operation).parameters["organization_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_search_tenant_is_declared_before_the_query() -> None:
    parameters = tuple(inspect.signature(SearchPort.search).parameters)
    assert parameters.index("organization_id") < parameters.index("query")


def test_structured_llm_has_no_tool_capability_in_its_contract() -> None:
    assert "tool" not in inspect.signature(StructuredLlmPort.extract).parameters
    assert "tool" not in StructuredLlmRequest.__dataclass_fields__
