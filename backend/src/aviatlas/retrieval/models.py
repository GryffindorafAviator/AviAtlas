"""Domain models for AviAtlas retrieval layer."""

from uuid import UUID

from pydantic import BaseModel, Field

# 引入 Parsing 层的模型，用于构建溯源信息
from aviatlas.parsing.models import ProvenanceItem


class RetrievalChunk(BaseModel):
    """A unit of text prepared for embedding and retrieval.
    
    A Chunk may contain content from one or more source Blocks.
    It maintains references to those Blocks to enable precise citation.
    """

    id: UUID = Field(default_factory=UUID)
    content: str
    block_ids: list[UUID] = Field(default_factory=list)
    
    # Metadata for filtering during retrieval
    document_id: UUID | None = None
    knowledge_base_id: UUID | None = None


class SearchResult(BaseModel):
    """The enriched result returned to the frontend after a search.
    
    It combines the retrieved text with the exact physical locations (bboxes) 
    of the source blocks, enabling precise highlighting.
    """
    chunk_id: UUID
    content: str
    score: float  # Relevance score (e.g., cosine similarity or keyword match score)
    
    # CRITICAL: The exact physical locations extracted from the source Blocks
    provenance: list[ProvenanceItem] = Field(default_factory=list)