"""Resolve user-facing retrieval scopes into explicit, searchable document ids."""
from uuid import UUID

from aviatlas.retrieval.models import RetrievalScope
from aviatlas.services.errors import CollectionNotFound, DocumentNotFound, DocumentNotReady, InvalidInput
from aviatlas.services.models import ResolvedScope, ScopeRequest
from aviatlas.services.ports import DocumentRepository

MAX_DOCUMENTS = 100


class ScopeResolver:
    """Explicitly named documents must all be ready; collection and library
    scopes search the ready documents and report the rest as skipped."""

    def __init__(self, repository: DocumentRepository, collections=None):
        self.repository = repository
        self.collections = collections

    def resolve(self, request: ScopeRequest) -> ResolvedScope:
        if request.document_ids is not None and request.collection_id is not None:
            raise InvalidInput("Choose either documents or a collection, not both.")
        if request.document_ids is not None:
            ids = list(dict.fromkeys(request.document_ids))
            if not ids:
                raise InvalidInput("Select at least one document.")
            if len(ids) > MAX_DOCUMENTS:
                raise InvalidInput(f"Select at most {MAX_DOCUMENTS} documents.")
            for document_id in ids:
                record = self.repository.get(document_id)
                if record is None:
                    raise DocumentNotFound("Document not found.")
                if not record.searchable:
                    raise DocumentNotReady("Document is not ready. Please parse it first.")
            return ResolvedScope(ids)
        if request.collection_id is not None:
            if self.collections is None:
                raise CollectionNotFound("Collection storage is unavailable.")
            members = self.collections.document_ids(request.collection_id)
            records = [r for r in self.repository.all_records() if r.id in members]
        else:
            records = self.repository.all_records()
        records.sort(key=lambda r: r.uploaded_at)
        ready = [r.id for r in records if r.searchable]
        skipped = [r.id for r in records if not r.searchable]
        return ResolvedScope(ready, skipped)

    @staticmethod
    def retrieval_scope(resolved: ResolvedScope) -> RetrievalScope:
        return RetrievalScope.of(resolved.document_ids)


def single_document(document_id: UUID) -> ScopeRequest:
    return ScopeRequest(document_ids=[document_id])
