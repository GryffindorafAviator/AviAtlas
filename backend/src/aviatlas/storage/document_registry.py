"""In-memory registry for tracking document metadata and status.

This keeps the initial document lifecycle model explicit while maintaining the
existing API contracts used by the parser and retrieval pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict

from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk

DocumentStatus = Literal[
    "uploaded",
    "parsing",
    "parsed",
    "indexing",
    "indexed",
    "ready",
    "failed",
]


class DocumentRecord(BaseModel):
    """Represents a document's metadata, lifecycle state, and indexed content."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID
    filename: str
    filepath: Path | None = None
    title: Optional[str] = None
    status: DocumentStatus = "uploaded"
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parsed_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    blocks: List[Block] = Field(default_factory=list)
    chunks: List[RetrievalChunk] = Field(default_factory=list)

    def set_status(self, status: DocumentStatus) -> None:
        """Update the lifecycle state and keep timestamps in sync."""
        now = datetime.now(timezone.utc)
        self.status = status
        self.last_updated_at = now

        if status == "parsed":
            self.parsed_at = now
        elif status == "indexed":
            self.indexed_at = now

    def update_metadata(self, **kwargs: Any) -> None:
        """Merge metadata without losing the existing document context."""
        self.metadata.update(kwargs)
        self.last_updated_at = datetime.now(timezone.utc)


class DocumentRegistry:
    """Manages document records in memory."""

    def __init__(self):
        self._store: Dict[UUID, DocumentRecord] = {}

    def register(self, filename: str, filepath: Path | str | None = None) -> DocumentRecord:
        """Register a new document and return its record."""
        doc_id = uuid4()
        record = DocumentRecord(
            id=doc_id,
            filename=filename,
            filepath=Path(filepath) if filepath else None,
            status="uploaded",
            metadata={
                "source_type": "pdf",
                "page_count": 0,
                "language": "unknown",
            },
        )
        self._store[doc_id] = record
        return record

    def get(self, doc_id: UUID | str) -> Optional[DocumentRecord]:
        """Retrieve a document record by UUID or string identifier."""
        if isinstance(doc_id, str):
            try:
                doc_id = UUID(doc_id)
            except ValueError:
                return None
        return self._store.get(doc_id)

    def all_records(self) -> List[DocumentRecord]:
        """Return all known document records."""
        return list(self._store.values())

    def update_index(
        self,
        doc_id: UUID | str,
        *,
        blocks: Optional[List[Block]] = None,
        chunks: Optional[List[RetrievalChunk]] = None,
    ) -> DocumentRecord:
        """Attach parsed blocks and retrieval chunks to a document."""
        record = self.get(doc_id)
        if record is None:
            raise KeyError(f"Document not found: {doc_id}")

        if blocks is not None:
            record.blocks = blocks
            record.set_status("parsed")
        if chunks is not None:
            record.chunks = chunks
            record.set_status("indexed")
            record.metadata["page_count"] = max(len(record.blocks), record.metadata.get("page_count", 0))

        record.last_updated_at = datetime.now(timezone.utc)
        return record


# Global singleton instance to be shared across the API
registry = DocumentRegistry()