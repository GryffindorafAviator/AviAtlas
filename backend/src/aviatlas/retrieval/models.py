"""Data models for the Retrieval module."""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import List

# Introduce Parsing layer models for provenance information
from aviatlas.parsing.models import ProvenanceItem


class SearchResult(BaseModel):
    """The enriched result returned to the frontend after a search.

    It combines the retrieved text with the exact physical locations (bboxes)
    of the source blocks, enabling precise highlighting.
    """

    chunk_id: UUID
    content: str
    score: float  # Relevance score (e.g., cosine similarity or keyword match score)

    # CRITICAL: The exact physical locations extracted from the source Blocks
    provenance: List[ProvenanceItem] = Field(default_factory=list)