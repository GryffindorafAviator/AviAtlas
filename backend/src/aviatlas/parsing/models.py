"""Domain models for AviAtlas document parsing."""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class BlockType(str, Enum):
    """Semantic content types in AviAtlas's internal document model."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    PICTURE = "picture"
    CAPTION = "caption"
    EQUATION = "equation"
    CODE = "code"


class BoundingBox(BaseModel):
    """Normalized bounding box relative to a document page.

    Coordinates use a top-left origin and are normalized to [0.0, 1.0].
    The x-axis increases from left to right, and the y-axis increases
    from top to bottom.
    """

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "BoundingBox":
        """Validate that the bounding box has a valid rectangular shape."""
        if self.x0 > self.x1:
            raise ValueError("x0 must be less than or equal to x1")

        if self.y0 > self.y1:
            raise ValueError("y0 must be less than or equal to y1")

        return self


class TextSpan(BaseModel):
    """Half-open character range within a block's content."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_span(self) -> "TextSpan":
        """Validate that the text span contains at least one character."""
        if self.start >= self.end:
            raise ValueError("start must be less than end")

        return self


class ProvenanceItem(BaseModel):
    """Physical source location for part or all of a document block.

    Page numbers are 1-based. The bounding box identifies the physical
    region on the page, while the optional text span identifies which
    characters of the block's content correspond to that region.
    """

    page_number: int = Field(ge=1)
    bbox: BoundingBox
    text_span: TextSpan | None = None