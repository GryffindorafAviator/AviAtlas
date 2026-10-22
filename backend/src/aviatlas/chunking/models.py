"""Data models for the Chunking module."""

from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from typing import List


class Chunk(BaseModel):
    """A unit of text prepared for embedding and retrieval.
    
    A Chunk may contain content from one or more source Blocks.
    It maintains references to those Blocks to enable precise citation.
    """

    id: UUID = Field(default_factory=uuid4)
    content: str
    block_ids: List[UUID] = Field(default_factory=list)
    
    # Metadata for filtering during retrieval
    document_id: UUID | None = None
    knowledge_base_id: UUID | None = None


