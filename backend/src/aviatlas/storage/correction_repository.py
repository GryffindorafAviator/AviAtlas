"""User corrections to generated chunks (System Design §7.3), stored apart from them.

A correction is anchored to the generated chunk text (`anchor`, `occurrence`)
and keeps the anchors of its source blocks, so after reprocessing it can be
reapplied to the same text or offered for review next to chunks that share
those blocks.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


@dataclass
class Correction:
    correction_id: UUID
    document_id: UUID
    anchor: str
    occurrence: int
    original_text: str
    text: str | None          # replacement text, or None to keep the generated text
    excluded: bool
    block_anchors: list[str]
    # applied: attached to `chunk_id`; orphaned: its text was not found after reprocessing.
    status: str
    chunk_id: UUID | None
    context: dict = field(default_factory=dict)  # section_title, page_range when created
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteCorrectionRepository:
    def __init__(self, documents: SQLiteDocumentRepository):
        self.documents = documents
        with documents.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS chunk_corrections (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    anchor TEXT NOT NULL,
                    occurrence INTEGER NOT NULL,
                    original_text TEXT NOT NULL,
                    text TEXT,
                    excluded INTEGER NOT NULL,
                    block_anchors TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_id TEXT,
                    context TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (document_id, anchor, occurrence)
                );
            ''')

    @staticmethod
    def _row(row) -> Correction:
        return Correction(UUID(row[0]), UUID(row[1]), row[2], row[3], row[4], row[5], bool(row[6]),
                          json.loads(row[7]), row[8], UUID(row[9]) if row[9] else None, json.loads(row[10]),
                          datetime.fromisoformat(row[11]), datetime.fromisoformat(row[12]))

    _COLUMNS = ('id, document_id, anchor, occurrence, original_text, text, excluded, block_anchors, '
                'status, chunk_id, context, created_at, updated_at')

    def for_document(self, document_id: UUID) -> list[Correction]:
        with self.documents.connect() as connection:
            rows = connection.execute(f'SELECT {self._COLUMNS} FROM chunk_corrections WHERE document_id=? '
                                      'ORDER BY created_at, id', (str(document_id),)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, correction_id: UUID) -> Correction | None:
        with self.documents.connect() as connection:
            row = connection.execute(f'SELECT {self._COLUMNS} FROM chunk_corrections WHERE id=?',
                                     (str(correction_id),)).fetchone()
        return self._row(row) if row else None

    def upsert(self, correction: Correction) -> Correction:
        """Insert, or update the correction with the same anchor."""
        now = _now()
        with self.documents.connect() as connection:
            connection.execute(f'''
                INSERT INTO chunk_corrections ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (document_id, anchor, occurrence) DO UPDATE SET
                    text=excluded.text, excluded=excluded.excluded, status=excluded.status,
                    chunk_id=excluded.chunk_id, updated_at=excluded.updated_at''',
                (str(correction.correction_id), str(correction.document_id), correction.anchor,
                 correction.occurrence, correction.original_text, correction.text, int(correction.excluded),
                 json.dumps(correction.block_anchors), correction.status,
                 str(correction.chunk_id) if correction.chunk_id else None, json.dumps(correction.context),
                 now, now))
            row = connection.execute('SELECT id FROM chunk_corrections WHERE document_id=? AND anchor=? AND occurrence=?',
                                     (str(correction.document_id), correction.anchor, correction.occurrence)).fetchone()
        return self.get(UUID(row[0]))

    def reanchor(self, correction_id: UUID, anchor: str, occurrence: int, original_text: str,
                 block_anchors: list[str], chunk_id: UUID | None, status: str) -> None:
        with self.documents.connect() as connection:
            connection.execute(
                'UPDATE chunk_corrections SET anchor=?, occurrence=?, original_text=?, block_anchors=?, '
                'chunk_id=?, status=?, updated_at=? WHERE id=?',
                (anchor, occurrence, original_text, json.dumps(block_anchors),
                 str(chunk_id) if chunk_id else None, status, _now(), str(correction_id)))

    def set_attachments(self, attachments: dict[UUID, UUID | None]) -> None:
        """Record where each correction landed after reapplication (None: orphaned)."""
        now = _now()
        with self.documents.connect() as connection:
            for correction_id, chunk_id in attachments.items():
                connection.execute('UPDATE chunk_corrections SET chunk_id=?, status=?, updated_at=? WHERE id=?',
                                   (str(chunk_id) if chunk_id else None, 'applied' if chunk_id else 'orphaned',
                                    now, str(correction_id)))

    def delete(self, correction_id: UUID) -> bool:
        with self.documents.connect() as connection:
            return bool(connection.execute('DELETE FROM chunk_corrections WHERE id=?', (str(correction_id),)).rowcount)
