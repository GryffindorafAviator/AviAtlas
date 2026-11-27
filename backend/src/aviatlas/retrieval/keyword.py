"""Scoped BM25 retrieval over any KeywordIndex."""
from aviatlas.retrieval.models import RetrievalResult, RetrievalScope, ScoredHit
from aviatlas.retrieval.resolution import ScopedRecords
from aviatlas.retrieval.tokenizer import tokenize
from aviatlas.services.ports import DocumentRepository, KeywordIndex


class KeywordRetriever:
    method = "bm25"

    def __init__(self, repository: DocumentRepository, keywords: KeywordIndex, tokenizer=tokenize):
        self.repository = repository
        self.keywords = keywords
        self.tokenizer = tokenizer

    def ensure_ready(self, document_id, chunk_ids) -> None:
        self.keywords.ensure_ready(document_id, chunk_ids)

    def scoped(self, scope: RetrievalScope) -> ScopedRecords:
        return ScopedRecords(self.repository, scope, self.ensure_ready)

    def hits(self, terms: list[str], records: ScopedRecords, top_k: int) -> list[ScoredHit]:
        if not records.records or not terms:
            return []
        return self.keywords.search(terms, top_k, records.records.keys())

    def search(self, query: str, scope: RetrievalScope, top_k: int = 3) -> list[RetrievalResult]:
        records = self.scoped(scope)
        return [records.result(query, hit, rank=i, method=self.method)
                for i, hit in enumerate(self.hits(self.tokenizer(query), records, top_k), start=1)]
