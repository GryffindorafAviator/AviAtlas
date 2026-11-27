"""Document upload, parsing, chunking and indexing workflow."""
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from threading import Lock, RLock
from uuid import UUID

from aviatlas.chunking.anchors import occurrences
from aviatlas.retrieval.guard import index_guard
from aviatlas.parsing.formats import UnsupportedFormat, check_content, source_format
from aviatlas.storage.document_registry import DocumentRecord
from aviatlas.retrieval.tokenizer import tokenize
from aviatlas.services.errors import (
    InvalidInput, DocumentNotFound, DocumentNotReady, DocumentProcessingFailed, DocumentBusy, DuplicateDocument,
)
from aviatlas.services.models import ParseResult
from aviatlas.services.ports import DocumentRepository, Parser, Chunker, Embedder, KeywordIndex, VectorIndex

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


def _copy_and_hash(stream: BinaryIO, path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("wb") as buffer:
        while block := stream.read(_CHUNK):
            digest.update(block)
            buffer.write(block)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()

class DocumentService:
    def __init__(self, repository: DocumentRepository, vectors: VectorIndex,
                 upload_dir: Path, parser_factory: Callable[[], Parser],
                 chunker: Chunker, embedder: Embedder | None,
                 keywords: KeywordIndex | None = None, tokenizer=tokenize, corrections=None):
        self.repository = repository
        self.corrections = corrections
        self.vectors = vectors
        self.keywords = keywords
        self.tokenizer = tokenizer
        self.upload_dir = upload_dir
        self.parser_factory = parser_factory
        self.chunker = chunker
        self.embedder = embedder
        self._locks: dict[UUID, RLock] = {}
        self._locks_guard = Lock()
        self._upload_guard = Lock()

    def _lock_for(self, document_id: UUID):
        with self._locks_guard:
            return self._locks.setdefault(document_id, RLock())

    def get(self, document_id: UUID) -> DocumentRecord:
        record = self.repository.get(document_id)
        if record is None:
            raise DocumentNotFound("Document not found.")
        return record

    def list(self, limit: int = 20, offset: int = 0, document_ids: set[UUID] | None = None):
        records = sorted(self.repository.all_records(), key=lambda r: r.uploaded_at, reverse=True)
        if document_ids is not None:
            records = [r for r in records if r.id in document_ids]
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
                with index_guard.write():
                    self._delete_indexes(document_id)
                if record.filepath:
                    record.filepath.unlink(missing_ok=True)
                self.repository.delete(document_id)
            except Exception as exc:
                record.update_metadata(last_error="Deletion failed. Please retry deletion.")
                self.repository.save(record)
                raise DocumentProcessingFailed("Deletion failed. Please retry deletion.") from exc
        finally:
            lock.release()

    def repair_records(self) -> None:
        """Backfill content hashes for deduplication, drop titles that were only the
        generated storage name, and drop chunks of documents that have no index."""
        for record in self.repository.all_records():
            changed = False
            if record.title == str(record.id):
                record.title, changed = None, True
            if record.status in {"failed", "uploaded"} and record.chunks:
                # Chunks of a document without an index (left by older versions) must not look searchable.
                record.chunks, changed = [], True
            if "sha256" not in record.metadata and record.filepath and record.filepath.is_file():
                try:
                    record.metadata["sha256"], changed = _hash_file(record.filepath), True
                except OSError:
                    logger.exception("Could not hash document: %s", record.id)
            if changed:
                self.repository.save(record)

    def _delete_indexes(self, document_id: UUID) -> None:
        self.vectors.delete(document_id)
        if self.keywords is not None:
            self.keywords.delete(document_id)

    def _write_keywords(self, document_id: UUID, chunks) -> None:
        if self.keywords is not None:
            self.keywords.replace(document_id, [(c.id, self.tokenizer(c.resolved_text)) for c in chunks])

    def rebuild_keyword_indexes(self) -> int:
        """Build keyword entries that are missing or stale from stored chunks.

        Keyword indexes derive only from chunk text, so unlike vectors they can be
        rebuilt locally without reparsing or re-embedding."""
        if self.keywords is None:
            return 0
        rebuilt = 0
        for record in self.repository.all_records():
            if not record.searchable:
                continue
            try:
                self.keywords.ensure_ready(record.id, [c.id for c in record.indexed_chunks])
            except DocumentNotReady:
                with self._lock_for(record.id):
                    self._write_keywords(record.id, record.indexed_chunks)
                rebuilt += 1
        return rebuilt

    def recover_interrupted(self) -> None:
        # One server process only: any in-flight records belong to the previous run.
        for record in self.repository.all_records():
            if record.status in {"queued", "parsing", "indexing"} and record.indexed_chunks and self._indexes_ready(record):
                # An interrupted reprocess never touched the serving version.
                record.set_status("indexed")
                record.update_metadata(last_error="Reprocessing was interrupted; the previous version is still in use.")
                self.repository.save(record)
            elif record.status in {"queued", "parsing", "indexing"} or (
                    record.status == "uploaded" and record.filepath is None):
                self._delete_indexes(record.id)
                record.set_status("failed")
                record.chunks = []
                record.update_metadata(last_error="Processing was interrupted. Upload or parse again.")
                self.repository.save(record)
        removed = self.vectors.collect_garbage()
        if removed:
            logger.info("Removed %d vector collections left by interrupted rebuilds", removed)

    def resume_deletions(self) -> None:
        for record in self.repository.all_records():
            if record.status == "deleting":
                try:
                    self.delete(record.id)
                except DocumentProcessingFailed:
                    logger.exception("Could not resume deletion: %s", record.id)

    def upload(self, filename: str | None, stream: BinaryIO) -> DocumentRecord:
        try:
            fmt = source_format(filename)
        except UnsupportedFormat as exc:
            raise InvalidInput(str(exc)) from exc
        record = self.repository.register(filename)
        record.metadata["source_format"] = fmt
        file_path = self.upload_dir.resolve() / f"{record.id}{Path(filename).suffix.lower()}"
        try:
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            sha256 = _copy_and_hash(stream, file_path)
        except Exception as exc:
            record.set_status("failed")
            self.repository.save(record)
            logger.exception("Document upload failed: %s", record.id)
            raise DocumentProcessingFailed("Failed to save file. Check server logs and retry.") from exc
        try:
            check_content(file_path, fmt)
        except UnsupportedFormat as exc:
            file_path.unlink(missing_ok=True)
            self.repository.delete(record.id)
            raise InvalidInput(str(exc)) from exc
        # Check and claim the hash atomically so concurrent identical uploads
        # cannot both be kept.
        with self._upload_guard:
            existing = next((r for r in self.repository.all_records()
                             if r.id != record.id and r.metadata.get("sha256") == sha256), None)
            if existing is not None:
                file_path.unlink(missing_ok=True)
                self.repository.delete(record.id)
                raise DuplicateDocument(f"This file is already in the library as \"{existing.filename}\".")
            record.filepath = file_path
            record.metadata["sha256"] = sha256
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
        # A document that is already searchable keeps serving its current index and chunks
        # until the new version is complete (System Design §12.4).
        previous = bool(record.indexed_chunks) and self._indexes_ready(record)
        record.set_status("parsing")
        self.repository.save(record)
        writing = False
        try:
            if not previous:
                self._delete_indexes(document_id)
            parsed = self.parser_factory().parse(record.filepath)
            chunks = self.chunker.chunk(parsed.blocks, document_id=document_id, knowledge_base_id=None)
            if not chunks:
                raise ValueError("No searchable text was extracted from the document.")
            attachments = self._apply_corrections(document_id, chunks, parsed.blocks)
            record.set_status("indexing")
            self.repository.save(record)
            included = [c for c in chunks if not c.excluded]
            vectors = self.embedder.encode([c.resolved_text for c in included])
            writing = True
            with index_guard.write():  # searches wait for the switch instead of seeing half of it
                self.vectors.replace(document_id, [c.id for c in included], vectors, model_name=self.embedder.model_name)
                self._write_keywords(document_id, included)
                record.blocks = parsed.blocks
                record.chunks = chunks
                record.title = parsed.metadata.title
                metadata = parsed.metadata.model_dump()
                # Formats detected at upload win over parser defaults; a missing field keeps the stored value.
                metadata["source_format"] = record.metadata.get("source_format") or metadata["source_format"] or "pdf"
                record.update_metadata(**metadata, chunker=getattr(self.chunker, "version", None))
                record.parsed_at = datetime.now(timezone.utc)
                record.set_status("indexed")
                record.metadata.pop("last_error", None)
                self.repository.save(record)
            if self.corrections is not None:
                self.corrections.set_attachments(attachments)
        except Exception as exc:
            logger.exception("Document processing failed: %s", document_id)
            if previous and not writing:
                # Parsing, chunking, or embedding failed: nothing was written; keep the old version.
                record.set_status("indexed")
                record.update_metadata(last_error=f"Reprocessing failed ({type(exc).__name__}: {exc})"[:400]
                                       + "; the previous version is still in use.")
                self.repository.save(record)
            else:
                record.set_status("failed")
                record.chunks = []
                record.update_metadata(last_error=f"{type(exc).__name__}: {exc}"[:500])
                self.repository.save(record)
                try:
                    self._delete_indexes(document_id)
                except Exception:
                    logger.exception("Failed to clean up index: %s", document_id)
            raise DocumentProcessingFailed(
                "Document processing failed. Check server logs and retry parsing."
            ) from exc
        return ParseResult(document_id, record.status, len(parsed.blocks), len(chunks))

    def _indexes_ready(self, record: DocumentRecord) -> bool:
        ids = [c.id for c in record.indexed_chunks]
        try:
            self.vectors.ensure_ready(record.id, ids, model_name=self.embedder.model_name)
            if self.keywords is not None:
                self.keywords.ensure_ready(record.id, ids)
        except DocumentNotReady:
            return False
        return True

    def _apply_corrections(self, document_id: UUID, chunks, blocks) -> dict:
        """Reapply stored corrections to freshly generated chunks; return correction -> chunk id (None: orphaned)."""
        if self.corrections is None:
            return {}
        by_anchor = dict(zip(occurrences([c.content for c in chunks]), chunks))
        attachments = {}
        for correction in self.corrections.for_document(document_id):
            chunk = by_anchor.get((correction.anchor, correction.occurrence))
            if chunk is not None:
                chunk.edited_text, chunk.excluded, chunk.correction_id = correction.text, correction.excluded, correction.correction_id
            attachments[correction.correction_id] = chunk.id if chunk is not None else None
        return attachments

    def reindex(self, record: DocumentRecord, changed: set[UUID]) -> None:
        """Rewrite both indexes from the record's current chunks, re-embedding only `changed` chunks."""
        included = record.indexed_chunks
        reuse = self.vectors.vectors(record.id, [c.id for c in included if c.id not in changed])
        missing = [c for c in included if c.id not in reuse]
        fresh = dict(zip((c.id for c in missing), self.embedder.encode([c.resolved_text for c in missing]))) if missing else {}
        vectors = [reuse[c.id] if c.id in reuse else fresh[c.id] for c in included]
        self.vectors.replace(record.id, [c.id for c in included], vectors, model_name=self.embedder.model_name)
        self._write_keywords(record.id, included)
