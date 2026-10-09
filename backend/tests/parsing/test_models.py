"""Tests for AviAtlas parsing domain models."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aviatlas.parsing.models import (
    Block,
    BlockType,
    BoundingBox,
    DocumentMetadata,
    ParsedDocument,
    ProvenanceItem,
    TextSpan,
)


def test_bounding_box_accepts_valid_coordinates():
    """A valid normalized bounding box should be created successfully."""
    bbox = BoundingBox(
        x0=0.1,
        y0=0.2,
        x1=0.8,
        y1=0.6,
    )

    assert bbox.x0 == 0.1
    assert bbox.y0 == 0.2
    assert bbox.x1 == 0.8
    assert bbox.y1 == 0.6


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x0", -0.1),
        ("y0", -0.1),
        ("x1", 1.1),
        ("y1", 1.1),
    ],
)
def test_bounding_box_rejects_coordinates_outside_normalized_range(
    field,
    value,
):
    """Coordinates outside [0.0, 1.0] should be rejected."""
    coordinates = {
        "x0": 0.1,
        "y0": 0.2,
        "x1": 0.8,
        "y1": 0.6,
    }
    coordinates[field] = value

    with pytest.raises(ValidationError):
        BoundingBox(**coordinates)


def test_bounding_box_rejects_reversed_x_coordinates():
    """The left edge must not be to the right of the right edge."""
    with pytest.raises(ValidationError):
        BoundingBox(
            x0=0.8,
            y0=0.2,
            x1=0.1,
            y1=0.6,
        )


def test_bounding_box_rejects_reversed_y_coordinates():
    """The top edge must not be below the bottom edge."""
    with pytest.raises(ValidationError):
        BoundingBox(
            x0=0.1,
            y0=0.8,
            x1=0.6,
            y1=0.2,
        )


def test_text_span_accepts_valid_range():
    """A valid half-open text span should be created successfully."""
    span = TextSpan(start=0, end=6)

    assert span.start == 0
    assert span.end == 6


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-1, 6),
        (0, -1),
        (3, 3),
        (6, 3),
    ],
)
def test_text_span_rejects_invalid_range(start, end):
    """Negative, empty, or reversed text spans should be rejected."""
    with pytest.raises(ValidationError):
        TextSpan(start=start, end=end)


def test_provenance_item_accepts_valid_source_location():
    """A valid provenance item should be created successfully."""
    provenance = ProvenanceItem(
        page_number=12,
        bbox=BoundingBox(
            x0=0.1,
            y0=0.2,
            x1=0.8,
            y1=0.6,
        ),
        text_span=TextSpan(start=0, end=120),
    )

    assert provenance.page_number == 12
    assert provenance.bbox.x0 == 0.1
    assert provenance.text_span == TextSpan(start=0, end=120)


def test_provenance_item_accepts_missing_text_span():
    """A provenance item may omit a text span when it is not meaningful."""
    provenance = ProvenanceItem(
        page_number=3,
        bbox=BoundingBox(
            x0=0.2,
            y0=0.3,
            x1=0.7,
            y1=0.8,
        ),
    )

    assert provenance.page_number == 3
    assert provenance.text_span is None


@pytest.mark.parametrize("page_number", [0, -1])
def test_provenance_item_rejects_invalid_page_number(page_number):
    """Page numbers must be 1-based positive integers."""
    with pytest.raises(ValidationError):
        ProvenanceItem(
            page_number=page_number,
            bbox=BoundingBox(
                x0=0.1,
                y0=0.2,
                x1=0.8,
                y1=0.6,
            ),
        )


def test_block_accepts_valid_content():
    """A valid document block should be created successfully."""
    block_id = uuid4()

    block = Block(
        id=block_id,
        type=BlockType.PARAGRAPH,
        content="Large language models have demonstrated strong capabilities.",
        order=0,
        provenance=[
            ProvenanceItem(
                page_number=1,
                bbox=BoundingBox(
                    x0=0.1,
                    y0=0.2,
                    x1=0.8,
                    y1=0.4,
                ),
                text_span=TextSpan(start=0, end=59),
            )
        ],
    )

    assert block.id == block_id
    assert block.type == BlockType.PARAGRAPH
    assert block.order == 0
    assert len(block.provenance) == 1


def test_block_accepts_empty_provenance():
    """A block may exist without physical provenance information."""
    block = Block(
        id=uuid4(),
        type=BlockType.HEADING,
        content="Introduction",
        order=0,
    )

    assert block.provenance == []


def test_block_rejects_negative_order():
    """Block order must be a non-negative sequence index."""
    with pytest.raises(ValidationError):
        Block(
            id=uuid4(),
            type=BlockType.PARAGRAPH,
            content="Example paragraph.",
            order=-1,
        )


def test_block_rejects_invalid_uuid():
    """Block IDs must be valid UUIDs."""
    with pytest.raises(ValidationError):
        Block(
            id="not-a-valid-uuid",
            type=BlockType.PARAGRAPH,
            content="Example paragraph.",
            order=0,
        )


def test_document_metadata_accepts_extracted_metadata():
    """Extracted document metadata should be stored successfully."""
    metadata = DocumentMetadata(
        title="Attention Is All You Need",
        authors=[
            "Ashish Vaswani",
            "Noam Shazeer",
        ],
        page_count=15,
        language="en",
    )

    assert metadata.title == "Attention Is All You Need"
    assert metadata.authors == [
        "Ashish Vaswani",
        "Noam Shazeer",
    ]
    assert metadata.page_count == 15
    assert metadata.language == "en"


def test_document_metadata_accepts_missing_optional_metadata():
    """Document metadata may be unavailable from the source."""
    metadata = DocumentMetadata()

    assert metadata.title is None
    assert metadata.authors == []
    assert metadata.page_count is None
    assert metadata.language is None


@pytest.mark.parametrize("page_count", [0, -1])
def test_document_metadata_rejects_invalid_page_count(page_count):
    """Page count must be positive when it is available."""
    with pytest.raises(ValidationError):
        DocumentMetadata(page_count=page_count)


def test_parsed_document_accepts_metadata_and_blocks():
    """A parsed document should contain metadata and extracted blocks."""
    block = Block(
        id=uuid4(),
        type=BlockType.PARAGRAPH,
        content="Example paragraph.",
        order=0,
        provenance=[
            ProvenanceItem(
                page_number=1,
                bbox=BoundingBox(
                    x0=0.1,
                    y0=0.2,
                    x1=0.8,
                    y1=0.4,
                ),
                text_span=TextSpan(start=0, end=18),
            )
        ],
    )

    document = ParsedDocument(
        metadata=DocumentMetadata(
            title="Example Document",
            authors=["Example Author"],
            page_count=1,
            language="en",
        ),
        blocks=[block],
    )

    assert document.metadata.title == "Example Document"
    assert len(document.blocks) == 1
    assert document.blocks[0] == block


def test_parsed_document_accepts_empty_blocks():
    """A parsed document may contain no extracted blocks."""
    document = ParsedDocument(
        metadata=DocumentMetadata(page_count=1),
    )

    assert document.blocks == []