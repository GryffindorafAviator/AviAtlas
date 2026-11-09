"""Data models for the Retrieval module.

The project intentionally separates the content layer from the retrieval layer:
- Block: parsed source content from a document
- Chunk: retrieval-ready chunk derived from blocks
- RetrievalResult: query-time search output

The field names below preserve backwards compatibility with the earlier API while
also supporting the newer, clearer chunk/result schema.
"""

from typing import Any, List
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from aviatlas.parsing.models import ProvenanceItem


class RetrievalChunk(BaseModel):
    """A retrieval-ready chunk derived from document blocks.

    Legacy compatibility: earlier code expects `content` and `block_ids`.
    Newer schema uses `text` and `source_block_ids` for clearer semantics.
    """

    id: UUID = Field(default_factory=uuid4)
    content: str = ""
    text: str | None = None
    block_ids: List[UUID] = Field(default_factory=list)
    source_block_ids: List[UUID] = Field(default_factory=list)
    document_id: UUID | None = None
    knowledge_base_id: UUID | None = None
    section_title: str | None = None
    page_range: tuple[int, int] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, values):
        if not isinstance(values, dict):
            return values

        if "text" in values and values["text"] is not None and "content" not in values:
            values["content"] = values["text"]
        if "content" in values and values["content"] is not None and "text" not in values:
            values["text"] = values["content"]

        if "source_block_ids" in values and values["source_block_ids"] and "block_ids" not in values:
            values["block_ids"] = values["source_block_ids"]
        if "block_ids" in values and values["block_ids"] and "source_block_ids" not in values:
            values["source_block_ids"] = values["block_ids"]

        if "content" in values and values["content"] is not None and not values.get("text"):
            values["text"] = values["content"]

        content = values.get("content") or values.get("text") or ""
        values["content"] = str(content)
        values["text"] = str(content)
        return values

    @property
    def resolved_text(self) -> str:
        return self.text or self.content or ""


class RetrievalResult(BaseModel):
    """A query-time retrieval result, carrying the hit and provenance.

    Newer schema uses `query`, `chunk_id`, `document_id`, `matched_text`,
    `section_title`, and `page_range`, while older code still expects `content`,
    `score`, and `provenance`.
    """

    query: str = ""
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    content: str = ""
    matched_text: str | None = None
    score: float = 0.0
    section_title: str | None = None
    page_range: tuple[int, int] | None = None
    source_excerpt: str | None = None
    provenance: List[ProvenanceItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, values):
        if not isinstance(values, dict):
            return values

        if "matched_text" in values and values["matched_text"] is not None and "content" not in values:
            values["content"] = values["matched_text"]
        if "content" in values and values["content"] is not None and "matched_text" not in values:
            values["matched_text"] = values["content"]

        if "source_excerpt" in values and values["source_excerpt"] is not None and "content" not in values:
            values["content"] = values["source_excerpt"]

        if values.get("content") is not None and values.get("matched_text") is None:
            values["matched_text"] = values["content"]

        if values.get("chunk_id") is None and values.get("chunk_id") is None:
            values["chunk_id"] = None

        return values