"""Organize existing documents without copying files or rebuilding indexes."""
from __future__ import annotations
from uuid import UUID
from aviatlas.services.errors import CollectionNotFound, InvalidInput
from aviatlas.storage.collection_repository import SQLiteCollectionRepository


class CollectionService:
    def __init__(self, repository: SQLiteCollectionRepository):
        self.repository = repository

    @staticmethod
    def _name(name: str):
        name = name.strip()
        if not name or len(name) > 100:
            raise InvalidInput('Collection names must contain 1 to 100 characters.')
        return name

    def list(self):
        return self.repository.list()

    def get(self, collection_id: UUID):
        record = self.repository.get(collection_id)
        if record is None:
            raise CollectionNotFound('Collection not found.')
        return record

    def create(self, name: str):
        return self.repository.create(self._name(name))

    def rename(self, collection_id: UUID, name: str):
        return self.repository.rename(collection_id, self._name(name))

    def delete(self, collection_id: UUID):
        self.repository.delete(collection_id)

    def document_ids(self, collection_id: UUID):
        self.get(collection_id)
        return self.repository.document_ids(collection_id)

    def collection_ids(self, document_id: UUID):
        return self.repository.collection_ids(document_id)

    def replace_memberships(self, document_id: UUID, collection_ids: list[UUID]):
        return self.repository.replace_memberships(document_id, collection_ids)
