from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from metreo_contracts import (
    BoundingBox,
    Confidence,
    DocumentRevisionRef,
    ExecutionVersion,
    FrozenJsonObject,
    InvalidBoundingBoxError,
    InvalidCitationError,
    InvalidConfidenceError,
    InvalidStructuredDataError,
    SourceCitation,
    StructuredExtraction,
    to_primitive,
)


def revision(organization_id: str = "org-1") -> DocumentRevisionRef:
    return DocumentRevisionRef(
        organization_id=organization_id,
        document_id="doc-1",
        revision_id="rev-1",
        revision_number=1,
    )


def citation(organization_id: str = "org-1") -> SourceCitation:
    return SourceCitation(
        revision=revision(organization_id),
        page=2,
        char_start=10,
        char_end=20,
        bbox=BoundingBox(Decimal("0.1"), Decimal("0.2"), Decimal("0.8"), Decimal("0.9")),
        extractor="fixture@1",
        confidence=Confidence(Decimal("0.82")),
    )


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("1.01"), Decimal("NaN")])
def test_confidence_rejects_values_outside_the_closed_interval(value: Decimal) -> None:
    with pytest.raises(InvalidConfidenceError) as error:
        Confidence(value)
    assert error.value.code == "invalid_document_confidence"


def test_confidence_rejects_float_even_when_it_is_in_range() -> None:
    with pytest.raises(InvalidConfidenceError):
        Confidence(0.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "coordinates",
    [
        ("-0.1", "0", "0.5", "1"),
        ("0", "0", "1.1", "1"),
        ("0.5", "0", "0.5", "1"),
        ("0", "0.7", "1", "0.6"),
    ],
)
def test_bounding_box_rejects_out_of_range_or_reversed_coordinates(
    coordinates: tuple[str, str, str, str],
) -> None:
    with pytest.raises(InvalidBoundingBoxError) as error:
        BoundingBox(*(Decimal(value) for value in coordinates))
    assert error.value.code == "invalid_document_bounding_box"


def test_bounding_box_rejects_float_coordinates() -> None:
    with pytest.raises(InvalidBoundingBoxError):
        BoundingBox(0.1, Decimal("0"), Decimal("1"), Decimal("1"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("page", "char_start", "char_end"),
    [(0, 0, 1), (1, -1, 1), (1, 2, 2), (1, 3, 2)],
)
def test_citation_rejects_an_invalid_page_or_character_range(
    page: int, char_start: int, char_end: int
) -> None:
    with pytest.raises(InvalidCitationError) as error:
        SourceCitation(
            revision=revision(),
            page=page,
            char_start=char_start,
            char_end=char_end,
            bbox=BoundingBox(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")),
            extractor="fixture@1",
            confidence=Confidence(Decimal("1")),
        )
    assert error.value.code == "invalid_document_citation"


def test_structured_extraction_requires_a_citation() -> None:
    with pytest.raises(InvalidCitationError):
        StructuredExtraction(
            schema_name="clause",
            schema_version="1",
            data=FrozenJsonObject.from_mapping({"value": "C30/37"}),
            confidence=Confidence(Decimal("0.9")),
            citations=(),
            version=ExecutionVersion("pipeline-1", "prompt-1", "model-1"),
        )


def test_structured_extraction_rejects_citations_from_two_tenants() -> None:
    with pytest.raises(InvalidCitationError):
        StructuredExtraction(
            schema_name="clause",
            schema_version="1",
            data=FrozenJsonObject.from_mapping({"value": "C30/37"}),
            confidence=Confidence(Decimal("0.9")),
            citations=(citation("org-1"), citation("org-2")),
            version=ExecutionVersion("pipeline-1", "prompt-1", "model-1"),
        )


def test_structured_data_is_deeply_immutable_and_rejects_float() -> None:
    source: dict[str, object] = {
        "nested": {"quantity": Decimal("12.50")},
        "labels": ["a", "b"],
    }
    frozen = FrozenJsonObject.from_mapping(source)
    source["nested"] = {"quantity": Decimal("99")}
    assert to_primitive(frozen) == {
        "labels": ["a", "b"],
        "nested": {"quantity": "12.50"},
    }
    with pytest.raises(FrozenInstanceError):
        frozen.items = ()  # type: ignore[misc]
    with pytest.raises(InvalidStructuredDataError):
        FrozenJsonObject.from_mapping({"confidence": 0.8})


def test_serialization_never_turns_decimal_contract_values_into_float() -> None:
    extraction = StructuredExtraction(
        schema_name="quantity",
        schema_version="1",
        data=FrozenJsonObject.from_mapping({"quantity": Decimal("12.500")}),
        confidence=Confidence(Decimal("0.825")),
        citations=(citation(),),
        version=ExecutionVersion("pipeline-1", "prompt-4", "model-2"),
    )
    serialized = to_primitive(extraction)

    def assert_no_float(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for item in value.values():
                assert_no_float(item)
        elif isinstance(value, list):
            for item in value:
                assert_no_float(item)

    assert_no_float(serialized)
    assert isinstance(serialized, dict)
    assert serialized["confidence"] == {"value": "0.825"}
    assert serialized["data"] == {"quantity": "12.500"}


def test_references_and_citations_are_immutable() -> None:
    reference = revision()
    source = citation()
    with pytest.raises(FrozenInstanceError):
        reference.document_id = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        source.page = 99  # type: ignore[misc]
