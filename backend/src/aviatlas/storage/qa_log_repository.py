"""Q&A logs: one row per answered question, for diagnosing retrieval and answers.

Sources are stored as references (document, chunk, page, ranks, scores), not as
excerpt text. Only the newest `keep` rows are retained.
"""
from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


@dataclass
class QALog:
    kind: str                      # ask | conversation
    question: str
    mode: str
    status: str                    # complete | no_evidence | stopped | failed
    search_query: str | None = None
    scope: dict = field(default_factory=dict)          # requested: document_ids / collection_id
    document_ids: list[str] = field(default_factory=list)
    skipped_document_ids: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    answer: str = ""
    segments: list[dict] = field(default_factory=list)
    invalid_citations: list[int] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    timings_ms: dict = field(default_factory=dict)     # retrieval, first_token, generation, total
    conversation_id: str | None = None
    message_id: str | None = None
    log_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def source_reference(citation: int, source) -> dict:
    return {"citation": citation, "document_id": str(source.document_id) if source.document_id else None,
            "document_name": source.document_name, "chunk_id": str(source.chunk_id) if source.chunk_id else None,
            "page_range": list(source.page_range) if source.page_range else None,
            "section_title": source.section_title, "rank": source.rank, "retrieval_method": source.retrieval_method,
            "scores": source.scores, "ranks": source.ranks}


class SQLiteQALogRepository:
    def __init__(self, documents: SQLiteDocumentRepository, keep: int = 2000):
        self.documents, self.keep = documents, keep
        with documents.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS qa_logs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS qa_logs_created ON qa_logs(created_at);
            ''')

    def add(self, log: QALog) -> None:
        payload = {k: v for k, v in asdict(log).items() if k not in ("log_id", "created_at")}
        with self.documents.connect() as connection:
            connection.execute('INSERT INTO qa_logs VALUES (?, ?, ?, ?)',
                               (str(log.log_id), log.created_at.isoformat(), log.status,
                                json.dumps(payload, ensure_ascii=False, default=str)))
            connection.execute('DELETE FROM qa_logs WHERE id NOT IN '
                               '(SELECT id FROM qa_logs ORDER BY created_at DESC LIMIT ?)', (self.keep,))

    @staticmethod
    def _log(row) -> QALog:
        return QALog(**json.loads(row[2]), log_id=UUID(row[0]), created_at=datetime.fromisoformat(row[1]))

    def list(self, limit: int, offset: int, status: str | None = None) -> tuple[list[QALog], int]:
        where, params = ("WHERE status=?", [status]) if status else ("", [])
        with self.documents.connect() as connection:
            total = connection.execute(f'SELECT COUNT(*) FROM qa_logs {where}', params).fetchone()[0]
            rows = connection.execute(f'SELECT id, created_at, payload FROM qa_logs {where} '
                                      'ORDER BY created_at DESC LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        return [self._log(r) for r in rows], total

    def get(self, log_id: UUID) -> QALog | None:
        with self.documents.connect() as connection:
            row = connection.execute('SELECT id, created_at, payload FROM qa_logs WHERE id=?', (str(log_id),)).fetchone()
        return self._log(row) if row else None

    def delete(self, log_id: UUID) -> bool:
        with self.documents.connect() as connection:
            return bool(connection.execute('DELETE FROM qa_logs WHERE id=?', (str(log_id),)).rowcount)

    def clear(self) -> int:
        with self.documents.connect() as connection:
            return connection.execute('DELETE FROM qa_logs').rowcount
