"""In-memory store for parsed Blocks."""

from uuid import UUID
from typing import Dict, List, Optional

from aviatlas.parsing.models import Block


class BlockStore:
    """Manages parsed Blocks in memory, indexed by document_id."""

    def __init__(self):
        # Key: document_id (UUID), Value: List of Blocks
        self._store: Dict[UUID, List[Block]] = {}

    def save(self, document_id: UUID, blocks: List[Block]):
        """Save blocks for a specific document."""
        self._store[document_id] = blocks

    def get(self, document_id: UUID) -> Optional[List[Block]]:
        """Retrieve blocks for a specific document."""
        return self._store.get(document_id)

    def delete(self, document_id: UUID):
        """Delete blocks for a document."""
        self._store.pop(document_id, None)


# Global singleton instance
block_store = BlockStore()