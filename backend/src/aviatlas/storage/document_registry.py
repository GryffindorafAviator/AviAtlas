"""In-memory registry for tracking document metadata and status.

Note: In V1, this is an in-memory implementation. 
In future versions, this should be replaced by a database-backed store (e.g., PostgreSQL).
"""

from uuid import UUID, uuid4
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel


class DocumentRecord(BaseModel):
    """Represents a document's metadata and status."""
    id: UUID
    filename: str
    filepath: Path
    status: str  # e.g., "uploaded", "parsing", "parsed", "failed"


class DocumentRegistry:
    """Manages document records in memory."""

    def __init__(self):
        self._store: Dict[UUID, DocumentRecord] = {}

    def register(self, filename: str, filepath: Path) -> DocumentRecord:
        """Register a new document and return its record."""
        doc_id = uuid4()
        record = DocumentRecord(
            id=doc_id, 
            filename=filename, 
            filepath=filepath, 
            status="uploaded"
        )
        self._store[doc_id] = record
        return record

    def get(self, doc_id: UUID) -> Optional[DocumentRecord]:
        """Retrieve a document record by ID."""
        return self._store.get(doc_id)


# Global singleton instance to be shared across the API
registry = DocumentRegistry()