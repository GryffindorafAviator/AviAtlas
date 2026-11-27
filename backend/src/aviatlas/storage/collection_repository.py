"""Collection metadata and memberships in the document database."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from uuid import UUID, uuid4

from aviatlas.services.errors import InvalidInput, DocumentNotFound, CollectionNotFound
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


@dataclass
class CollectionRecord:
    collection_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    document_count: int = 0


class SQLiteCollectionRepository:
    def __init__(self, documents: SQLiteDocumentRepository):
        self.documents = documents
        with documents.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_collections (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    PRIMARY KEY (document_id, collection_id)
                );
                CREATE INDEX IF NOT EXISTS collection_members
                    ON document_collections(collection_id, document_id);
            ''')

    @staticmethod
    def _record(row):
        return CollectionRecord(UUID(row[0]), row[1], datetime.fromisoformat(row[2]),
                                datetime.fromisoformat(row[3]), row[4])

    def list(self):
        with self.documents.connect() as connection:
            rows = connection.execute('''
                SELECT c.id, c.name, c.created_at, c.updated_at, COUNT(dc.document_id)
                FROM collections c LEFT JOIN document_collections dc ON dc.collection_id=c.id
                GROUP BY c.id ORDER BY c.created_at, c.id
            ''').fetchall()
        return [self._record(row) for row in rows]

    def get(self, collection_id: UUID):
        return next((c for c in self.list() if c.collection_id == collection_id), None)

    def create(self, name: str):
        collection_id = uuid4()
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.documents.connect() as connection:
                connection.execute('INSERT INTO collections VALUES (?, ?, ?, ?)',
                                   (str(collection_id), name, now, now))
        except sqlite3.IntegrityError as exc:
            raise InvalidInput('A collection with this name already exists.') from exc
        return self.get(collection_id)

    def rename(self, collection_id: UUID, name: str):
        try:
            with self.documents.connect() as connection:
                cursor = connection.execute('UPDATE collections SET name=?, updated_at=? WHERE id=?',
                    (name, datetime.now(timezone.utc).isoformat(), str(collection_id)))
                if not cursor.rowcount:
                    raise CollectionNotFound('Collection not found.')
        except sqlite3.IntegrityError as exc:
            raise InvalidInput('A collection with this name already exists.') from exc
        return self.get(collection_id)

    def delete(self, collection_id: UUID):
        with self.documents.connect() as connection:
            if not connection.execute('DELETE FROM collections WHERE id=?', (str(collection_id),)).rowcount:
                raise CollectionNotFound('Collection not found.')

    def document_ids(self, collection_id: UUID):
        with self.documents.connect() as connection:
            rows = connection.execute('SELECT document_id FROM document_collections WHERE collection_id=?',
                                      (str(collection_id),)).fetchall()
        return {UUID(row[0]) for row in rows}

    def collection_ids(self, document_id: UUID):
        with self.documents.connect() as connection:
            rows = connection.execute('SELECT collection_id FROM document_collections WHERE document_id=? ORDER BY collection_id',
                                      (str(document_id),)).fetchall()
        return [UUID(row[0]) for row in rows]

    def replace_memberships(self, document_id: UUID, collection_ids: list[UUID]):
        # Validate and replace under one write transaction. Failed updates retain
        # all previous memberships; cascades also cover concurrent deletions.
        with self.documents.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM documents WHERE id=?', (str(document_id),)).fetchone():
                raise DocumentNotFound('Document not found.')
            for collection_id in set(collection_ids):
                if not connection.execute('SELECT 1 FROM collections WHERE id=?', (str(collection_id),)).fetchone():
                    raise CollectionNotFound('Collection not found.')
            connection.execute('DELETE FROM document_collections WHERE document_id=?', (str(document_id),))
            connection.executemany('INSERT INTO document_collections VALUES (?, ?)',
                                  [(str(document_id), str(cid)) for cid in set(collection_ids)])
        return self.collection_ids(document_id)
