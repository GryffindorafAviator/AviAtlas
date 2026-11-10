"""Durable document snapshots using SQLite and Pydantic JSON serialization."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4
from aviatlas.storage.document_registry import DocumentRecord

class SQLiteDocumentRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, filename: str, filepath: Path | str | None = None) -> DocumentRecord:
        record = DocumentRecord(id=uuid4(), filename=filename,
                                filepath=Path(filepath).resolve() if filepath else None,
                                metadata={"source_type": "pdf", "page_count": 0, "language": "unknown"})
        self.save(record)
        return record

    def save(self, record: DocumentRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO documents(id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (str(record.id), record.model_dump_json()),
            )

    def get(self, doc_id: UUID | str) -> DocumentRecord | None:
        try:
            key = str(UUID(str(doc_id)))
        except ValueError:
            return None
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM documents WHERE id=?", (key,)).fetchone()
        return DocumentRecord.model_validate_json(row[0]) if row else None

    def delete(self, doc_id: UUID) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM documents WHERE id=?", (str(doc_id),))

    def all_records(self) -> list[DocumentRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM documents ORDER BY rowid").fetchall()
        return [DocumentRecord.model_validate_json(row[0]) for row in rows]
