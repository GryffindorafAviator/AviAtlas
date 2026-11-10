"""Document upload, parsing, chunking and indexing workflow."""
import logging
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from threading import Lock, RLock
from uuid import UUID

from aviatlas.storage.document_registry import DocumentRecord
from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk
from aviatlas.services.errors import InvalidInput, DocumentNotFound, DocumentProcessingFailed, DocumentBusy
from aviatlas.services.models import ParseResult
from aviatlas.services.ports import DocumentRepository, Parser, Chunker, SearchIndex
from aviatlas.services.ports import IndexRepository

logger = logging.getLogger(__name__)

class DocumentService:
    def __init__(self, repository: DocumentRepository, indexes: IndexRepository,
                 upload_dir: Path, parser_factory: Callable[[], Parser],
                 chunker: Chunker,
                 index_factory: Callable[[list[RetrievalChunk], list[Block]], SearchIndex]):
        self.repository = repository
        self.indexes = indexes
        self.upload_dir = upload_dir
        self.parser_factory = parser_factory
        self.chunker = chunker
        self.index_factory = index_factory
        self._locks: dict[UUID, RLock] = {}
        self._locks_guard = Lock()

    def _lock_for(self, document_id: UUID):
        with self._locks_guard:
            return self._locks.setdefault(document_id, RLock())

    def get(self, document_id: UUID) -> DocumentRecord:
        record = self.repository.get(document_id)
        if record is None:
            raise DocumentNotFound("Document not found.")
        return record

    def list(self, limit: int = 20, offset: int = 0):
        records = sorted(self.repository.all_records(), key=lambda r: r.uploaded_at, reverse=True)
        return records[offset:offset + limit], len(records)

    def enqueue(self, document_id: UUID, retry_only: bool = False) -> DocumentRecord:
        lock = self._lock_for(document_id)
        if not lock.acquire(blocking=False):
            raise DocumentBusy("Document is being processed. Please wait.")
        try:
            record = self.get(document_id)
            if record.status in {"queued", "parsing", "indexing", "deleting"}:
                raise DocumentBusy("Document is already queued, processing, or deleting.")
            if retry_only and record.status != "failed":
                raise InvalidInput("Only failed documents can be retried.")
            if not record.filepath or not record.filepath.exists():
                raise DocumentNotFound("Uploaded file is missing from disk. Upload it again.")
            record.set_status("queued")
            record.metadata.pop("last_error", None)
            self.repository.save(record)
            return record
        finally:
            lock.release()

    def process_queued(self, document_id: UUID) -> None:
        with self._lock_for(document_id):
            record = self.repository.get(document_id)
            if record is None or record.status != "queued":
                return
            try:
                self._parse(document_id)
            except DocumentProcessingFailed:
                # Failure is persisted by _parse; background exceptions must not
                # replace an already-sent HTTP response.
                logger.exception("Background processing failed: %s", document_id)
            except Exception:
                record = self.repository.get(document_id)
                if record:
                    record.set_status("failed")
                    record.update_metadata(last_error="Processing failed. Please retry.")
                    self.repository.save(record)
                logger.exception("Background processing interrupted: %s", document_id)

    def delete(self, document_id: UUID) -> None:
        lock = self._lock_for(document_id)
        if not lock.acquire(blocking=False):
            raise DocumentBusy("Document is being processed. Delete it after processing finishes.")
        try:
            record = self.get(document_id)
            if record.status in {"parsing", "indexing"}:
                raise DocumentBusy("Document is being processed. Please wait.")
            record.set_status("deleting")
            self.repository.save(record)
            try:
                self.indexes.delete(document_id)
                if record.filepath:
                    record.filepath.unlink(missing_ok=True)
                self.repository.delete(document_id)
            except Exception as exc:
                record.update_metadata(last_error="Deletion failed. Please retry deletion.")
                self.repository.save(record)
                raise DocumentProcessingFailed("Deletion failed. Please retry deletion.") from exc
        finally:
            lock.release()

    def resume_deletions(self) -> None:
        for record in self.repository.all_records():
            if record.status == "deleting":
                try:
                    self.delete(record.id)
                except DocumentProcessingFailed:
                    logger.exception("Could not resume deletion: %s", record.id)

    def upload(self, filename: str | None, stream: BinaryIO) -> DocumentRecord:
        if not filename or not filename.lower().endswith(".pdf"):
            raise InvalidInput("Only PDF files are allowed.")
        record = self.repository.register(filename)
        file_path = self.upload_dir.resolve() / f"{record.id}{Path(filename).suffix}"
        try:
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(stream, buffer)
        except Exception as exc:
            record.set_status("failed")
            self.repository.save(record)
            logger.exception("Document upload failed: %s", record.id)
            raise DocumentProcessingFailed("Failed to save file. Check server logs and retry.") from exc
        record.filepath = file_path
        self.repository.save(record)
        return record

    def parse(self, document_id: UUID) -> ParseResult:
        lock = self._lock_for(document_id)
        if not lock.acquire(blocking=False):
            raise DocumentBusy("Document is being processed. Please wait.")
        try:
            if self.get(document_id).status in {"queued", "parsing", "indexing", "deleting"}:
                raise DocumentBusy("Document is already queued, processing, or deleting.")
            return self._parse(document_id)
        finally:
            lock.release()

    def _parse(self, document_id: UUID) -> ParseResult:
        record = self.repository.get(document_id)
        if record is None:
            raise DocumentNotFound("Document not found.")
        if not record.filepath or not record.filepath.exists():
            raise DocumentNotFound("Uploaded file is missing from disk.")
        record.set_status("parsing")
        self.repository.save(record)
        try:
            self.indexes.delete(document_id)
            parsed = self.parser_factory().parse(record.filepath)
            chunks = self.chunker.chunk(parsed.blocks, document_id=document_id, knowledge_base_id=None)
            if not chunks:
                raise ValueError("No searchable text was extracted from the PDF.")
            record.set_status("indexing")
            self.repository.save(record)
            index = self.index_factory(chunks, parsed.blocks)
            record.blocks = parsed.blocks
            record.chunks = chunks
            record.title = parsed.metadata.title
            record.update_metadata(**parsed.metadata.model_dump())
            record.parsed_at = datetime.now(timezone.utc)
            self.indexes.save(document_id, index)
            record.set_status("indexed")
            record.metadata.pop("last_error", None)
            self.repository.save(record)
        except Exception as exc:
            record.set_status("failed")
            record.update_metadata(last_error=f"{type(exc).__name__}: {exc}"[:500])
            self.repository.save(record)
            try:
                self.indexes.delete(document_id)
            except Exception:
                logger.exception("Failed to clean up index: %s", document_id)
            logger.exception("Document processing failed: %s", document_id)
            raise DocumentProcessingFailed(
                "Document processing failed. Check server logs and retry parsing."
            ) from exc
        return ParseResult(document_id, record.status, len(parsed.blocks), len(chunks))
