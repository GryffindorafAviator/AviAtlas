"""In-memory keyword-based Retriever implementation."""

from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk, SearchResult
from aviatlas.retrieval.retrievers.base import Retriever


class InMemoryRetriever(Retriever):
    """A simple in-memory retriever using keyword matching.
    
    This is a placeholder for a real Vector DB retriever.
    Its primary purpose is to validate the Chunk -> Block -> BBox mapping logic.
    """

    def __init__(self, chunks: list[RetrievalChunk], blocks: list[Block]):
        self.chunks = chunks
        self._block_map = {block.id: block for block in blocks}

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        query_terms = set(query.lower().split())
        scored_chunks = []

        for chunk in self.chunks:
            content_terms = set(chunk.content.lower().split())
            intersection = query_terms.intersection(content_terms)
            if intersection:
                score = len(intersection) / len(query_terms.union(content_terms))
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = scored_chunks[:top_k]

        results = []
        for score, chunk in top_chunks:
            provenance_items = []
            
            for block_id in chunk.block_ids:
                original_block = self._block_map.get(block_id)
                if original_block:
                    provenance_items.extend(original_block.provenance)

            results.append(
                SearchResult(
                    chunk_id=chunk.id,
                    content=chunk.content,
                    score=score,
                    provenance=provenance_items,
                )
            )

        return results