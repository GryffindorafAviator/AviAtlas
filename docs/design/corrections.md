# Chunk Corrections and Index Rebuilding — Implementation

Implements System Design §7.3 (user corrections), §12.4 (index rebuilding), FR-LIB-11. Status: M6.

Modules: `services/corrections.py` (`CorrectionService`), `storage/correction_repository.py`, `chunking/anchors.py`, `services/documents.py` (`_parse`, `reindex`, recovery), `retrieval/guard.py`.

## Corrections

| Action | API | Effect |
|---|---|---|
| list | `GET /documents/{id}/chunks` | chunks (effective and generated text, flags) and orphaned corrections with candidate chunks |
| edit | `PATCH /documents/{id}/chunks/{chunk}` `{"text": "..."}` | retrieval and answers use the edited text; only that chunk is re-embedded |
| restore | `{"text": null}` | back to the generated text |
| exclude / include | `{"excluded": true\|false}` | chunk removed from / returned to both indexes; an edit is kept |
| attach orphan | `POST /documents/{id}/corrections/{c}/attach` `{"chunk_id": ...}` | apply an orphaned correction to a chosen chunk |
| dismiss orphan | `DELETE /documents/{id}/corrections/{c}` | delete it |

Editing requires status `indexed` (400) and fails with 409 while the document is processing. Saving the generated text unchanged removes the correction. Text: 1–20000 characters.

Storage (`documents.sqlite3`): `chunk_corrections(id, document_id FK ON DELETE CASCADE, anchor, occurrence, original_text, text, excluded, block_anchors, status, chunk_id, context, created_at, updated_at)`, unique on `(document_id, anchor, occurrence)`. The record's chunks carry `edited_text`, `excluded`, `correction_id` as a materialized copy; `content` always stays the generated text.

### Anchoring

Block and chunk ids are regenerated on every parse, so a correction is anchored to content: `anchor` = SHA-256 of the whitespace-normalized generated chunk text, `occurrence` = how many earlier chunks have the same text. After chunking, a correction is reapplied to the chunk with the same `(anchor, occurrence)`; otherwise it becomes `orphaned` (§7.3: never discarded silently).

For review, an orphan lists current chunks sharing any of its source blocks (`block_anchors`, SHA-256 of block text) that have no correction. The library card shows `pending_corrections`; the reader's 分块 tab lists them.

## Rebuilding

Reprocessing a document that has a usable index (both indexes pass `ensure_ready` for its chunks):

1. Status `queued → parsing → indexing`; the old chunks and indexes keep serving search and questions.
2. Parse, chunk, reapply corrections, embed — nothing is written yet.
3. Switch: vector index (new collection, then manifest), keyword index (one transaction), record. Held under the index guard.

| Failure | Result |
|---|---|
| steps 1–2 (parsing, chunking, embedding) | status back to `indexed`, old version untouched, `last_error` says so |
| step 3 (index writes) | status `failed`, chunks and indexes removed (as before M6) |
| process killed | startup restores `indexed` if the old indexes still match, else `failed`; vector collections no manifest points to are deleted (`collect_garbage`) |

A document never indexed before fails as before. `ChromaVectorIndex.replace` writes the new collection before dropping the old one, so a failed write leaves the old index serving.

### Index guard

Vector index, keyword index, and record share no transaction. Searches (`HybridRetriever.run`) hold `index_guard` shared; switches, chunk edits, and index deletion hold it exclusively (writers first). Before the guard, a live reprocess under continuous search produced 16 errors in a 140 ms window (inconsistent index, or a hit on a chunk the record did not have yet); with it: 0 errors in 293 searches, slowest 0.48 s. In-process only (single worker).

## Known limits

- An edit spanning a changed paragraph after reprocessing needs manual review; there is no automatic merge.
- Embedding of an edited chunk runs inside the guard; searches wait for it (one chunk, ~25 ms on CPU).
