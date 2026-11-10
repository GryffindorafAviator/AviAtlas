"""Document-scoped semantic retrieval with a shared multilingual encoder."""
from functools import lru_cache
import os
from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk, RetrievalResult

def embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL") or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

@lru_cache(maxsize=2)
def _load_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)

def get_encoder(model_name: str | None = None):
    return _load_encoder(model_name or embedding_model_name())

class SemanticRetriever:
    def __init__(self, chunks: list[RetrievalChunk], blocks: list[Block], encoder=None):
        self.embedding_model = embedding_model_name()
        self.chunks = chunks
        self.blocks = {block.id: block for block in blocks}
        self.encoder = encoder if encoder is not None else get_encoder()
        self.embeddings = self.encoder.encode(
            [chunk.resolved_text for chunk in chunks], normalize_embeddings=True,
        ) if chunks else None

    def search(self, query: str, top_k: int = 3) -> list[RetrievalResult]:
        if self.embeddings is None:
            return []
        import numpy as np
        vector = self.encoder.encode([query], normalize_embeddings=True)[0]
        scores = np.asarray(self.embeddings) @ np.asarray(vector)
        results = []
        for index in np.argsort(-scores)[:top_k]:
            chunk = self.chunks[int(index)]
            provenance = [p for bid in chunk.block_ids if bid in self.blocks
                          for p in self.blocks[bid].provenance]
            pages = [p.page_number for p in provenance]
            results.append(RetrievalResult(
                query=query, document_id=chunk.document_id, chunk_id=chunk.id,
                content=chunk.resolved_text, score=float(scores[index]),
                provenance=provenance,
                page_range=(min(pages), max(pages)) if pages else None,
            ))
        return results
