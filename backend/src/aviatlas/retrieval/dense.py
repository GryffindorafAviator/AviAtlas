"""Scoped dense retrieval over any VectorIndex, with provenance from document records."""
from aviatlas.retrieval.models import RetrievalResult, RetrievalScope
from aviatlas.services.errors import DocumentNotReady
from aviatlas.services.ports import DocumentRepository, Embedder, VectorIndex


class DenseRetriever:
    method = "dense"

    def __init__(self, repository: DocumentRepository, embedder: Embedder, vectors: VectorIndex):
        self.repository = repository
        self.embedder = embedder
        self.vectors = vectors

    def search(self, query: str, scope: RetrievalScope, top_k: int = 3) -> list[RetrievalResult]:
        records = {}
        for document_id in scope.document_ids:
            record = self.repository.get(document_id)
            if record is None or record.status != "indexed" or not record.chunks:
                raise DocumentNotReady("Document is not ready. Please parse it first.")
            self.vectors.ensure_ready(document_id, [c.id for c in record.chunks],
                                      model_name=self.embedder.model_name)
            records[document_id] = record
        if not records:
            return []
        vector = self.embedder.encode([query])[0]
        hits = self.vectors.search(vector, top_k, records.keys())
        chunks = {c.id: c for record in records.values() for c in record.chunks}
        blocks = {b.id: b for record in records.values() for b in record.blocks}
        results = []
        for rank, hit in enumerate(hits, start=1):
            chunk = chunks[hit.chunk_id]
            provenance = [p for bid in chunk.block_ids if bid in blocks for p in blocks[bid].provenance]
            pages = [p.page_number for p in provenance]
            results.append(RetrievalResult(
                query=query, document_id=hit.document_id, chunk_id=chunk.id,
                content=chunk.resolved_text, score=hit.score, rank=rank,
                retrieval_method=self.method, provenance=provenance,
                section_title=chunk.section_title,
                page_range=(min(pages), max(pages)) if pages else None,
            ))
        return results
