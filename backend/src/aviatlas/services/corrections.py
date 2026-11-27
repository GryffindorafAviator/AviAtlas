"""Inspect, edit, and exclude chunks (FR-LIB-11) through stored corrections.

Edits take effect at once: the chunk is updated in the record and both indexes
are rewritten, re-embedding only the edited chunk.
"""
from __future__ import annotations
from dataclasses import dataclass
from uuid import UUID, uuid4

from aviatlas.chunking.anchors import normalize, occurrences, text_anchor
from aviatlas.retrieval.guard import index_guard
from aviatlas.services.errors import (
    ChunkNotFound, CorrectionNotFound, DocumentBusy, DocumentNotReady, DocumentProcessingFailed, InvalidInput,
)
from aviatlas.storage.correction_repository import Correction, SQLiteCorrectionRepository

MAX_TEXT = 20000
UNSET = object()


@dataclass
class OrphanedCorrection:
    correction: Correction
    # Current chunks built from any of the correction's source blocks.
    candidate_chunk_ids: list[UUID]


class CorrectionService:
    def __init__(self, documents, repository: SQLiteCorrectionRepository):
        self.documents, self.repository = documents, repository

    def overview(self, document_id: UUID):
        """The record (chunks carry their corrections) and corrections that need review."""
        record = self.documents.get(document_id)
        anchors_by_chunk = {c.id: self._block_anchors(record, c) for c in record.chunks}
        orphans = []
        for correction in self.repository.for_document(document_id):
            if correction.status != "orphaned":
                continue
            wanted = set(correction.block_anchors)
            orphans.append(OrphanedCorrection(correction, [
                c.id for c in record.chunks if wanted & set(anchors_by_chunk[c.id]) and c.correction_id is None]))
        return record, orphans

    def update(self, document_id: UUID, chunk_id: UUID, *, text=UNSET, excluded=UNSET):
        """`text`: replacement, or None to restore the generated text. `excluded`: leave retrieval or rejoin it."""
        with self._editing(document_id) as record:
            chunk = self._chunk(record, chunk_id)
            new_text = chunk.edited_text if text is UNSET else self._text(text, chunk.content)
            new_excluded = chunk.excluded if excluded is UNSET else bool(excluded)
            self._apply(record, chunk, new_text, new_excluded)
            return chunk

    def attach(self, document_id: UUID, correction_id: UUID, chunk_id: UUID):
        """Apply an orphaned correction to a chunk the user picked."""
        with self._editing(document_id) as record:
            correction = self._correction(document_id, correction_id)
            if correction.status != "orphaned":
                raise InvalidInput("Only corrections awaiting review can be attached.")
            chunk = self._chunk(record, chunk_id)
            if chunk.correction_id is not None:
                raise InvalidInput("This chunk already has a correction. Reset it first.")
            anchor, occurrence = self._anchor(record, chunk)
            self.repository.reanchor(correction_id, anchor, occurrence, chunk.content,
                                     self._block_anchors(record, chunk), chunk.id, "applied")
            chunk.correction_id = correction_id
            try:
                self._apply(record, chunk, self._text(correction.text, chunk.content) if correction.text else None,
                            correction.excluded)
            except Exception:
                self.repository.reanchor(correction_id, correction.anchor, correction.occurrence,
                                         correction.original_text, correction.block_anchors, None, "orphaned")
                chunk.correction_id = None
                raise
            return chunk

    def dismiss(self, document_id: UUID, correction_id: UUID) -> None:
        correction = self._correction(document_id, correction_id)
        if correction.status != "orphaned":
            raise InvalidInput("Reset an applied correction from its chunk instead.")
        self.repository.delete(correction_id)

    # --- internals ---

    def _editing(self, document_id: UUID):
        documents = self.documents

        class Editing:
            def __enter__(inner):
                inner.lock = documents._lock_for(document_id)
                if not inner.lock.acquire(blocking=False):
                    raise DocumentBusy("The document is being processed. Edit chunks after it finishes.")
                record = documents.get(document_id)
                if record.status != "indexed":
                    inner.lock.release()
                    raise DocumentNotReady("Chunks can be edited once the document is indexed.")
                return record

            def __exit__(inner, *exc):
                inner.lock.release()
        return Editing()

    @staticmethod
    def _chunk(record, chunk_id: UUID):
        chunk = next((c for c in record.chunks if c.id == chunk_id), None)
        if chunk is None:
            raise ChunkNotFound("Chunk not found. The document may have been reprocessed; reload it.")
        return chunk

    def _correction(self, document_id: UUID, correction_id: UUID) -> Correction:
        correction = self.repository.get(correction_id)
        if correction is None or correction.document_id != document_id:
            raise CorrectionNotFound("Correction not found.")
        return correction

    @staticmethod
    def _text(text, original: str) -> str | None:
        if text is None:
            return None
        if not isinstance(text, str) or not text.strip():
            raise InvalidInput("Chunk text cannot be empty. Exclude the chunk instead.")
        if len(text) > MAX_TEXT:
            raise InvalidInput(f"Chunk text must be at most {MAX_TEXT} characters.")
        # Saving the generated text unchanged is not an edit.
        return None if normalize(text) == normalize(original) else text

    @staticmethod
    def _anchor(record, chunk) -> tuple[str, int]:
        return dict(zip((c.id for c in record.chunks), occurrences([c.content for c in record.chunks])))[chunk.id]

    @staticmethod
    def _block_anchors(record, chunk) -> list[str]:
        blocks = {b.id: b for b in record.blocks}
        return [text_anchor(blocks[b].content) for b in chunk.block_ids if b in blocks]

    def _apply(self, record, chunk, new_text: str | None, new_excluded: bool) -> None:
        """Store the correction, update the chunk, and rewrite the indexes; undo everything on failure."""
        before = (chunk.edited_text, chunk.excluded, chunk.correction_id)
        stored = self.repository.get(chunk.correction_id) if chunk.correction_id else None
        if new_text is None and not new_excluded:
            if chunk.correction_id:
                self.repository.delete(chunk.correction_id)
            chunk.correction_id = None
        else:
            anchor, occurrence = self._anchor(record, chunk)
            pages = [p.page_number for b in record.blocks if b.id in chunk.block_ids for p in b.provenance]
            saved = self.repository.upsert(Correction(
                chunk.correction_id or uuid4(), record.id, anchor, occurrence, chunk.content, new_text, new_excluded,
                self._block_anchors(record, chunk), "applied", chunk.id,
                {"section_title": chunk.section_title, "page_range": [min(pages), max(pages)] if pages else None}))
            chunk.correction_id = saved.correction_id
        changed = {chunk.id} if new_text != chunk.edited_text else set()
        chunk.edited_text, chunk.excluded = new_text, new_excluded
        try:
            with index_guard.write():
                self.documents.reindex(record, changed)
                self.documents.repository.save(record)
        except Exception as exc:
            created = chunk.correction_id if stored is None else None
            chunk.edited_text, chunk.excluded, chunk.correction_id = before
            if stored is not None:
                self.repository.upsert(stored)
            elif created is not None:
                self.repository.delete(created)
            try:
                with index_guard.write():
                    self.documents.reindex(record, {chunk.id})
            except Exception:
                pass  # the digest check reports a mismatch until the document is reprocessed
            raise DocumentProcessingFailed("Could not update the index. The chunk was not changed.") from exc
