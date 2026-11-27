# Retrieval — Implementation

Implements System Design §9 and §16.3. Status: M2.

## Modules (`backend/src/aviatlas`)

| Module | Role |
|---|---|
| `services/scope.py` | `ScopeResolver`: request → document ids |
| `retrieval/dense.py` | `DenseRetriever` |
| `retrieval/keyword.py`, `retrieval/bm25.py`, `retrieval/tokenizer.py` | `KeywordRetriever`, BM25 scoring, tokenizer |
| `retrieval/hybrid.py` | `HybridRetriever`: parallel recall, RRF, `RetrievalRun` diagnostics |
| `retrieval/resolution.py` | Readiness checks; builds results from document records |
| `storage/chroma_vector_index.py` | `VectorIndex` on Chroma |
| `storage/keyword_index.py` | `KeywordIndex` on SQLite (in-memory version for tests) |

## Scope

| Request | Result |
|---|---|
| `document_ids` | All must exist (404) and be indexed (400). Max 100. |
| `collection_id` | Indexed members; the rest returned as `skipped_document_ids` |
| neither | Whole library, same skipping |
| both | 400 |

Explicit ids fail instead of skipping: the user named those documents, so silently answering from fewer would mislead. Collection and library scopes skip, so one document still processing does not block the rest. The 100 cap bounds per-document fan-out (see Known limits).

## Parameters

| Parameter | Value |
|---|---|
| Embedding model | `paraphrase-multilingual-MiniLM-L12-v2` (`EMBEDDING_MODEL`), normalized cosine |
| Tokenizer | jieba `cut_for_search`, lower-cased, `\w+` tokens, EN/ZH stopwords; version `jieba-search-v1` |
| BM25 | k1 = 1.5, b = 0.75, IDF `ln(1 + (N − df + 0.5)/(df + 0.5))`; N, df, avg length over the scope |
| Candidates per strategy | 20; both strategies run in parallel threads |
| RRF | `Σ 1/(60 + rank)`; ties by best single rank, then chunk id (deterministic order for evaluation) |
| `top_k` | search 1–50 (default 10); ask 1–10 (default 5) |

No stemming: `tokenizer` and `tokenizers` are distinct terms.

## Embedding device

Embeddings run on CPU by default (`EMBEDDING_DEVICE` overrides). Docling's models use the Apple GPU, and two models using it from different threads at once abort the process (Metal command-buffer assertion), observed when documents were processed concurrently. CPU cost: ~23 ms per query, ~1.7 s per 200 chunks (GPU: 10 ms, 0.9 s).

Calls into the model are serialized (it is not thread-safe) in batches of 64, with a pause between batches; without it the indexing thread retakes the lock at once and a query waited 8.9 s. Measured during a 738-chunk reprocess: slowest search 2.2 s while parsing, 1.0 s while indexing.

## Storage

Chroma: one collection per document. `index_manifest(document_id, collection_name, model)` in `documents.sqlite3`; collection metadata holds `document_id`, `embedding_model`, `chunk_digest`.

SQLite keyword index, same database:

```
keyword_manifest(document_id PK, chunk_digest, tokenizer)
keyword_chunks(chunk_id PK, document_id, length)
keyword_postings(term, chunk_id, document_id, tf)   -- indexed on (term, document_id)
```

`replace` runs in one `BEGIN IMMEDIATE` transaction.

## Consistency

`chunk_digest` = SHA-256 over the record's chunk ids in order. Before a search, each index must match the record:

- Vector: manifest exists, model matches, collection metadata digest and count match.
- Keyword: manifest exists, tokenizer version matches, digest matches.

Mismatch raises `DocumentNotReady`. Indexes live outside the record's write, so after an interrupted reparse an index can hold old chunk ids; the digest catches that instead of returning stale or missing chunks. Dense-only search does not need the keyword index.

Content and provenance in results always come from the document record; indexes store only ids, vectors, and postings.

## Startup sequence

`repair_records` (backfill SHA-256, clear UUID titles) → `recover_interrupted` → `resume_deletions` → `rebuild_keyword_indexes` (missing or old-tokenizer entries, from stored chunks).

Rebuild runs last so it skips documents that recovery marked failed or deletion removed. Rebuilding from stored chunks avoids reparsing and re-embedding.

## Diagnostics

`POST /api/v1/search/debug` returns `RetrievalRun`: `query_terms`, `dense`, `bm25`, `results`, `timings_ms` (scope, dense, bm25, fusion, total), `config` (rrf_k, candidate_k, embedding_model, tokenizer). The frontend's 检索调试 tab displays it.

## Known limits

- Chroma search fans out over one collection per document (ADR 0001).
- Loading full record JSON dominates latency (≈ 40–160 ms of 60–190 ms on 4 documents). Cache candidate.
- Chinese queries on English documents get no BM25 hits.
- RRF and candidate parameters not yet evaluated (M3).
- Dense retrieval sees only the first 128 tokens of a chunk ([chunking.md](chunking.md#known-limit-embedding-window)).
