"""Base interface for Chunkers."""

import uuid
from abc import ABC, abstractmethod

from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk


class Chunker(ABC):
    """Interface for converting Blocks into RetrievalChunks."""

    @abstractmethod
    def chunk(
        self, 
        blocks: list[Block], 
        document_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
    ) -> list[RetrievalChunk]:
        raise NotImplementedError