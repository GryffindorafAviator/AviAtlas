"""Shared in-memory indexes used by ingestion and question answering."""
from uuid import UUID
from aviatlas.services.ports import SearchIndex

class IndexStore:
    def __init__(self):
        self._indexes: dict[UUID, SearchIndex] = {}

    def get(self, document_id: UUID) -> SearchIndex | None:
        return self._indexes.get(document_id)

    def save(self, document_id: UUID, index: SearchIndex) -> None:
        self._indexes[document_id] = index

    def delete(self, document_id: UUID) -> None:
        self._indexes.pop(document_id, None)
