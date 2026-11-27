"""Verification reports (System Design §11.4), one row per run, linked to an answer message."""
from __future__ import annotations
import json
from datetime import datetime
from uuid import UUID

from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


class SQLiteVerificationRepository:
    def __init__(self, documents: SQLiteDocumentRepository):
        self.documents = documents
        with documents.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS verifications (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS verifications_message ON verifications(message_id, created_at);
            ''')

    def add(self, verification_id: UUID, message_id: UUID, created_at: datetime, report: dict) -> None:
        with self.documents.connect() as connection:
            connection.execute('INSERT INTO verifications VALUES (?, ?, ?, ?)',
                               (str(verification_id), str(message_id), created_at.isoformat(),
                                json.dumps(report, ensure_ascii=False, default=str)))

    def latest(self, message_ids: list[UUID]) -> dict[UUID, dict]:
        """The newest report per message."""
        if not message_ids:
            return {}
        marks = ",".join("?" * len(message_ids))
        with self.documents.connect() as connection:
            rows = connection.execute(
                f'SELECT message_id, payload FROM verifications WHERE message_id IN ({marks}) ORDER BY created_at',
                [str(m) for m in message_ids]).fetchall()
        return {UUID(m): json.loads(p) for m, p in rows}
