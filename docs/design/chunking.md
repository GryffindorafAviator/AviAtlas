# Chunking — Implementation

Implements System Design §5.4. Status: M5. `chunking/structured_chunker.py`, version `structured-v1` (stored per document as `metadata.chunker`).

## Rules

| Block | Rule |
|---|---|
| table | Own chunk with its caption (adjacent before or after) and the headings directly above. Over `max_chars × slack`: split by rows; every part repeats caption and header row. |
| equation, code | Never split; stays with the preceding text; may push a chunk to `max_chars × slack`. |
| heading | Starts a new chunk once the current one has `min_chars`; never ends a chunk. |
| paragraph over `max_chars` | Split at sentence ends (`。！？；!?` or `.;` + space); a longer sentence is cut hard. |
| other | Packed greedily up to `max_chars`. |

Parameters: `max_chars = 1000`, `min_chars = 200`, `slack = 1.5`.

`section_title`: the heading of the section contributing most of the chunk's text. `metadata.kind`: `text` or `table`; split tables add `part`, `parts`.

Without `min_chars`, papers with many numbered headings produce chunks of a heading and one sentence.

## Effect (Attention paper)

| | Before (greedy) | After |
|---|---|---|
| Chunks | 42 | 49 (43 text, 6 table) |
| Largest | 9608 chars | 988 chars |
| Equations with text | 0 / 5 | 5 / 5 |

## Existing documents

Documents chunked by an older version report `needs_reprocess`; the library shows 重新处理. Reprocessing deletes the old index first, so the document is not searchable while it runs and stays failed if processing fails (safe rebuild is M6).

## Known limit: embedding window

The embedding model reads at most 128 tokens (`max_seq_length`). A 1000-character chunk is roughly 200–250 tokens, so dense retrieval sees only its beginning; BM25 sees all of it. Options: smaller chunks or a long-context model (e.g. BGE-M3, 8192 tokens), decided with evaluation (M3).
