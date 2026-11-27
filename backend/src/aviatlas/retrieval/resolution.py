"""Shared steps for every retriever: check scope readiness, resolve hits to results.

Chunk text and provenance always come from authoritative document records,
never from an index.
"""
from collections.abc import Callable
from uuid import UUID

from aviatlas.retrieval.models import RetrievalResult, RetrievalScope, ScoredHit
from aviatlas.services.errors import DocumentNotReady
from aviatlas.services.ports import DocumentRepository


class ScopedRecords:
    def __init__(self, repository: DocumentRepository, scope: RetrievalScope,
                 ensure_ready: Callable[[UUID, list[UUID]], None]):
        self.records = {}
        for document_id in scope.document_ids:
            record = repository.get(document_id)
            if record is None or not record.searchable:
                raise DocumentNotReady("Document is not ready. Please parse it first.")
            ensure_ready(document_id, [c.id for c in record.indexed_chunks])
            self.records[document_id] = record
        self.chunks = {c.id: c for r in self.records.values() for c in r.indexed_chunks}
        self.blocks = {b.id: b for r in self.records.values() for b in r.blocks}

    def result(self, query: str, hit: ScoredHit, *, rank: int, method: str,
               scores: dict[str, float] | None = None, ranks: dict[str, int] | None = None,
               matched_terms=()) -> RetrievalResult:
        chunk = self.chunks[hit.chunk_id]
        provenance = [p for bid in chunk.block_ids if bid in self.blocks for p in self.blocks[bid].provenance]
        pages = [p.page_number for p in provenance]
        return RetrievalResult(
            query=query, document_id=hit.document_id, document_name=self.records[hit.document_id].filename,
            chunk_id=chunk.id, content=chunk.resolved_text, score=hit.score, rank=rank,
            retrieval_method=method, scores=scores or {method: hit.score}, ranks=ranks or {method: rank},
            matched_terms=list(matched_terms or hit.matched_terms), provenance=provenance,
            section_title=chunk.section_title, page_range=(min(pages), max(pages)) if pages else None,
            block_ids=list(chunk.block_ids),
        )
