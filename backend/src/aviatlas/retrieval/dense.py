"""Scoped dense retrieval over any VectorIndex, with provenance from document records."""
from aviatlas.retrieval.models import RetrievalResult, RetrievalScope, ScoredHit
from aviatlas.retrieval.resolution import ScopedRecords
from aviatlas.services.ports import DocumentRepository, Embedder, VectorIndex


class DenseRetriever:
    method = "dense"

    def __init__(self, repository: DocumentRepository, embedder: Embedder, vectors: VectorIndex):
        self.repository = repository
        self.embedder = embedder
        self.vectors = vectors

    def ensure_ready(self, document_id, chunk_ids) -> None:
        self.vectors.ensure_ready(document_id, chunk_ids, model_name=self.embedder.model_name)

    def scoped(self, scope: RetrievalScope) -> ScopedRecords:
        return ScopedRecords(self.repository, scope, self.ensure_ready)

    def hits(self, query: str, records: ScopedRecords, top_k: int) -> list[ScoredHit]:
        if not records.records:
            return []
        return self.vectors.search(self.embedder.encode([query])[0], top_k, records.records.keys())

    def search(self, query: str, scope: RetrievalScope, top_k: int = 3) -> list[RetrievalResult]:
        records = self.scoped(scope)
        return [records.result(query, hit, rank=i, method=self.method)
                for i, hit in enumerate(self.hits(query, records, top_k), start=1)]
