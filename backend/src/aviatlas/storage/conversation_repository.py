"""Conversations and messages in the document database.

Sources are stored per row with a foreign key to their document, so deleting a
document removes its excerpts from every conversation. Conversations whose scope
is that single document are deleted with it.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from aviatlas.retrieval.models import RetrievalResult
from aviatlas.services.models import ConversationRecord, MessageRecord
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteConversationRepository:
    def __init__(self, documents: SQLiteDocumentRepository):
        self.documents = documents
        with documents.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    scope TEXT NOT NULL,
                    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, seq)
                );
                CREATE TABLE IF NOT EXISTS message_sources (
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (message_id, position)
                );
                CREATE INDEX IF NOT EXISTS conversations_updated ON conversations(updated_at);
                CREATE INDEX IF NOT EXISTS message_sources_document ON message_sources(document_id);
            ''')

    @staticmethod
    def _conversation(row) -> ConversationRecord:
        scope = json.loads(row[2])
        return ConversationRecord(
            UUID(row[0]), row[1],
            [UUID(d) for d in scope["document_ids"]] if scope.get("document_ids") is not None else None,
            UUID(scope["collection_id"]) if scope.get("collection_id") else None,
            datetime.fromisoformat(row[3]), datetime.fromisoformat(row[4]), row[5])

    _SELECT = '''SELECT c.id, c.title, c.scope, c.created_at, c.updated_at,
                        (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                 FROM conversations c'''

    def create(self, document_ids: list[UUID] | None, collection_id: UUID | None, title: str | None) -> ConversationRecord:
        conversation_id, now = uuid4(), _now()
        scope = {"document_ids": [str(d) for d in document_ids] if document_ids is not None else None,
                 "collection_id": str(collection_id) if collection_id else None}
        single = str(document_ids[0]) if document_ids and len(document_ids) == 1 else None
        with self.documents.connect() as connection:
            connection.execute('INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)',
                               (str(conversation_id), title, json.dumps(scope), single, now, now))
        return self.get(conversation_id)

    def list(self, limit: int) -> list[ConversationRecord]:
        with self.documents.connect() as connection:
            rows = connection.execute(f'{self._SELECT} ORDER BY c.updated_at DESC, c.id LIMIT ?', (limit,)).fetchall()
        return [self._conversation(row) for row in rows]

    def get(self, conversation_id: UUID) -> ConversationRecord | None:
        with self.documents.connect() as connection:
            row = connection.execute(f'{self._SELECT} WHERE c.id = ?', (str(conversation_id),)).fetchone()
        return self._conversation(row) if row else None

    def rename(self, conversation_id: UUID, title: str) -> bool:
        with self.documents.connect() as connection:
            return bool(connection.execute('UPDATE conversations SET title=?, updated_at=? WHERE id=?',
                                           (title, _now(), str(conversation_id))).rowcount)

    def delete(self, conversation_id: UUID) -> bool:
        with self.documents.connect() as connection:
            return bool(connection.execute('DELETE FROM conversations WHERE id=?', (str(conversation_id),)).rowcount)

    def add_message(self, conversation_id: UUID, role: str, content: str, status: str,
                    payload: dict, *, title: str | None = None) -> MessageRecord:
        """Append a message; `title` is set only if the conversation has none yet."""
        message_id, now = uuid4(), _now()
        with self.documents.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            seq = connection.execute('SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE conversation_id=?',
                                     (str(conversation_id),)).fetchone()[0]
            connection.execute('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                               (str(message_id), str(conversation_id), seq, role, content, status,
                                json.dumps(payload, ensure_ascii=False), now))
            connection.execute('UPDATE conversations SET updated_at=?, title=COALESCE(title, ?) WHERE id=?',
                               (now, title, str(conversation_id)))
        return MessageRecord(message_id, conversation_id, seq, role, content, status, payload,
                             datetime.fromisoformat(now))

    def finish_message(self, message_id: UUID, content: str, status: str, payload: dict,
                       sources: list[RetrievalResult]) -> None:
        with self.documents.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('UPDATE messages SET content=?, status=?, payload=? WHERE id=?',
                               (content, status, json.dumps(payload, ensure_ascii=False), str(message_id)))
            # A source whose document was deleted mid-answer is dropped, like any deleted source.
            connection.execute('DELETE FROM message_sources WHERE message_id=?', (str(message_id),))
            for position, source in enumerate(sources, 1):
                connection.execute(
                    'INSERT INTO message_sources SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM documents WHERE id=?)',
                    (str(message_id), position, str(source.document_id), source.model_dump_json(),
                     str(source.document_id)))

    def messages(self, conversation_id: UUID) -> list[MessageRecord]:
        with self.documents.connect() as connection:
            rows = connection.execute(
                'SELECT id, conversation_id, seq, role, content, status, payload, created_at '
                'FROM messages WHERE conversation_id=? ORDER BY seq', (str(conversation_id),)).fetchall()
            sources: dict[str, list[tuple[int, RetrievalResult]]] = {}
            for message_id, position, payload in connection.execute(
                    'SELECT s.message_id, s.position, s.payload FROM message_sources s '
                    'JOIN messages m ON m.id = s.message_id WHERE m.conversation_id=? ORDER BY s.position',
                    (str(conversation_id),)):
                sources.setdefault(message_id, []).append((position, RetrievalResult.model_validate_json(payload)))
        return [MessageRecord(UUID(r[0]), UUID(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6]),
                              datetime.fromisoformat(r[7]), sources.get(r[0], [])) for r in rows]

    def fail_unfinished(self, error: str) -> int:
        """Mark answers left streaming by a previous process as failed."""
        with self.documents.connect() as connection:
            return connection.execute(
                "UPDATE messages SET status='failed', payload=json_set(payload, '$.error', ?) WHERE status='streaming'",
                (error,)).rowcount
