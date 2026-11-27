# ADR 0001 — Keep ChromaDB behind a backend-neutral vector index; migrate to Milvus later

| Field | Description |
|---|---|
| Status | Accepted |
| Date | 2026-11-27 |
| Related | System Design §9 (Retrieval), §12 (Storage), §14 (Technology Direction) |

## Context

Hybrid retrieval, cross-document search, and the Retrieval Inspector all need one retrieval scope applied identically by every retrieval strategy. The original index interface was per document (`IndexRepository.get(document_id).search(query, top_k)`), so it could not serve those requirements, whichever vector database sat behind it.

Milvus was considered as the vector database. It offers a clear path from embedded use (Milvus Lite) to standalone and distributed deployments, which matters for the institutional phase. The initial release, however, is single-user and local-first (§2.8, §19.1). ChromaDB already works there and its persisted data is in use.

## Decision

1. Keep ChromaDB as the vector index for now.
2. Define retrieval through backend-neutral contracts in `services/ports.py`:
   - `RetrievalScope`: explicit document ids, resolved by the application layer from documents, collections, metadata filters, and access rules.
   - `Embedder`: text to normalized vectors, identified by `model_name`.
   - `VectorIndex`: `replace`, `delete`, `contains`, `ensure_ready`, and `search(vector, top_k, document_ids)`.
   - `Retriever`: `search(query, scope, top_k)`.
3. Chunk text and provenance are always resolved from authoritative document records, never from the vector store.
4. Every `VectorIndex` backend must pass `tests/retrieval/test_vector_index_contract.py`.
5. Keyword retrieval (BM25) and rank fusion (RRF) live in the application layer, above `VectorIndex`, so that per-strategy ranks and scores stay observable.

## Migration triggers

Migrate to Milvus when one or more of the following holds:

- Dense retrieval latency for library-wide scopes exceeds the target set in the evaluation plan. The Chroma implementation queries one collection per document in scope.
- Multi-user or server deployment (Phase 5) requires a networked vector service.
- Native sparse or BM25 retrieval in the vector database outperforms the application-layer implementation in evaluation, without losing per-strategy diagnostics.

## Migration path

Vectors are derived data (§7.3, §12.4). Migration therefore means writing a Milvus `VectorIndex`, adding it to the contract tests, and re-embedding chunks from authoritative records. No authoritative data is migrated. Chroma can remain as a fallback until the new backend is verified.

## Consequences

- Positive: lower operational complexity now; the vector backend can be replaced without touching services; one scope semantics for dense, keyword, and hybrid retrieval.
- Negative: in the Chroma implementation, scoped search fans out across per-document collections; scores are comparable only because one embedding model is enforced per index.
