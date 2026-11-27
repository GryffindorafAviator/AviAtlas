# ADR 0003 — Application-layer BM25 with RRF fusion

| Field | Description |
|---|---|
| Status | Accepted |
| Date | 2026-11-27 |
| Related | System Design §9.4–9.8; ADR 0001; [docs/design/retrieval.md](../design/retrieval.md) |

## Decision

1. BM25 runs in the application over SQLite postings in `documents.sqlite3`. No search engine, no vector-database sparse index.
2. Tokenize with jieba search mode for Chinese and word tokens otherwise. The tokenizer carries a version; a version change triggers a rebuild from stored chunks.
3. Fuse dense and BM25 with RRF (k = 60, 20 candidates each). Never combine raw scores.

## Reasons

- Local-first, single process: no extra service (§2.8, §19.1).
- Per-strategy ranks, scores, and matched terms must stay visible to the inspector and to evaluation.
- Corpus statistics must follow the request scope; a per-document or global engine index would not give that without extra work.
- Cosine and BM25 scores have different scales; RRF needs only ranks.

## Revisit when

- Keyword latency on library-wide scopes exceeds the evaluation target.
- M3 shows other k, candidate counts, or weighted fusion perform better.
- Morphology matters (no stemming today).
- Migrating to Milvus (ADR 0001) and its native sparse retrieval matches quality with diagnostics intact.
