# AviAtlas backend

PDF → Docling blocks with page locations → multilingual semantic retrieval → grounded answers with numbered citations.

## Run

From `backend/` (Python 3.11+):

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
# Only on first setup; preserve an existing .env.
cp -n .env.example .env
# Fill OPENROUTER_API_KEY in .env.
uvicorn aviatlas.api.main:app --reload
```

For OpenRouter, add this to `.env`:

```dotenv
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=openrouter/free
```

The OpenRouter key takes precedence over direct OpenAI configuration. The endpoint is fixed to `https://openrouter.ai/api/v1` for this key. `openrouter/free` routes to an available free model; limits and model availability vary. To compare answer quality reproducibly, set `OPENROUTER_MODEL` to a specific available `:free` model. No paid-model fallback is configured. See [OpenRouter's free router](https://openrouter.ai/docs/guides/routing/routers/free-router).

For direct OpenAI, leave `OPENROUTER_API_KEY` empty and set `OPENAI_API_KEY` and `OPENAI_MODEL`. `OPENAI_BASE_URL` optionally selects another Responses-compatible endpoint.

Open http://127.0.0.1:8000/docs to upload and query a document (PDF, DOCX, Markdown, or TXT):

1. `POST /api/v1/documents/upload` — multipart field `file`; saves the file and queues background processing by default. Save the returned `document_id`.
2. `GET /api/v1/documents/{document_id}` — poll until `status` is `indexed`; inspect `last_error` if `failed`.
3. `POST /api/v1/documents/ask` — JSON:

```json
{"document_id":"<returned UUID>","question":"这本书的核心观点是什么？","top_k":3}
```

Answers use `[1]`, `[2]`, etc. to refer to the ordered `sources` array. Each source carries original text, page range, and page coordinates. `confidence` remains null: retrieval similarity is not answer confidence. Insufficient evidence should be stated in the answer.

The multilingual embedding model and Docling models download on first use. Embeddings run locally; the question and retrieved excerpts are sent to the configured Responses-compatible provider, with `store=False`. Missing model configuration returns 503; provider failures return 502.

## Search and library questions

Both endpoints take an optional scope: `document_ids` (every listed document must be indexed), `collection_id`, or neither for the whole library. Collection and library scopes search the indexed documents and list the others in `skipped_document_ids`.

- `POST /api/v1/search` — `{"query": "...", "collection_id": "<uuid>", "top_k": 10, "method": "hybrid"}`. `method` is `hybrid` (default), `dense`, or `bm25`. Returns ranked chunks with `document_name`, page range, provenance, `rank`, `retrieval_method`, per-strategy `scores` and `ranks`, and the BM25 `matched_terms`. For hybrid results `score` is the RRF score. Search does not call the answer model, so it works without provider configuration.
- `POST /api/v1/search/debug` — same body. Also returns the query terms, the dense and BM25 candidate lists, the fused results, stage timings in milliseconds, and the retrieval configuration. The frontend's 检索调试 tab shows this.

Hybrid retrieval runs dense and BM25 recall in parallel (20 candidates each) and fuses them with Reciprocal Rank Fusion (`1 / (60 + rank)` summed over strategies), so cosine similarities and BM25 scores never share a scale. BM25 runs in the application over SQLite postings in the document database. Tokenization uses jieba search mode for Chinese and lower-cased word tokens for other text. Corpus statistics are computed over the documents in scope. Keyword entries are written when a document is indexed. On startup, entries that are missing or built with another tokenizer version are rebuilt from stored chunks, with no reparsing or re-embedding. `/api/v1/ask` retrieves with hybrid search.
- `POST /api/v1/ask` — `{"question": "...", "document_ids": ["<uuid>"], "mode": "grounded", "selection": {"text": "...", "page_number": 3}, "top_k": 5}`.
  - `mode: "grounded"` answers only from evidence; with no evidence the model is not called.
  - `mode: "open"` also allows the model's own explanations. The answer is returned as `segments` with `source_type` `library`, `model`, or `unlabeled` (an open-mode answer the model did not mark). These labels are the model's account of its sources, not verification.
  - `selection` is text the reader selected; it is added to the retrieval query and given to the model as context.
  - `invalid_citations` lists citation numbers in the answer that match no returned source.

`/api/v1/documents/ask` remains for single-document grounded answers.

## Conversations

- `POST /api/v1/conversations` — `{"document_ids": [...]}`, `{"collection_id": "..."}`, or `{}` for the library; optional `title`. The scope is fixed for the conversation.
- `GET /api/v1/conversations`, `GET|PATCH|DELETE /api/v1/conversations/{id}` — list, read with messages, rename, delete.
- `POST /api/v1/conversations/{id}/messages` — `{"question": "...", "mode": "grounded", "selection": {...}, "top_k": 5}`. Streams Server-Sent Events `started`, `retrieval`, `delta`…, then `done` or `error`. Follow-up questions are rewritten into a standalone retrieval query (`search_query`) using the conversation.

Closing the stream stops generation; the partial answer is saved as `stopped`. Details: [design/conversations.md](design/conversations.md).

## Current limits

Run a single server worker and one application instance per data directory. There is no user authentication. A local frontend is available; see [frontend setup](../frontend/README.md). Background processing runs in this server process using FastAPI background tasks; it is not an external worker or durable job queue. The legacy `/documents/parse` endpoint still waits for processing to finish. The embedding model reads only the first 128 tokens of each chunk ([chunking.md](design/chunking.md#known-limit-embedding-window)). Citations are requested through prompting; citation correctness and answer quality need evaluation with real books.

## Tests

```sh
python -m pytest tests -q
```

API tests use deterministic encodings and a fake provider to verify orchestration, citation metadata, isolation, and failure behavior without external calls.

Responses integration follows [OpenAI's text generation documentation](https://developers.openai.com/api/docs/guides/text).

## Architecture

```text
HTTP API (api/main.py)
  ├─ DocumentService: upload → parse → chunk → embed → write vectors + keyword postings
  ├─ SearchService: resolve scope → hybrid retrieve
  ├─ QuestionAnsweringService: resolve scope → hybrid retrieve → generate → segments + evidence
  └─ ConversationService: LangGraph turn (rewrite → retrieve → stream) → SSE, SQLite history
       ↕ shared document repository, Embedder, VectorIndex, KeywordIndex
Concrete adapters: Docling, GreedyChunker, SentenceTransformerEmbedder,
                   ChromaVectorIndex, SQLiteKeywordIndex,
                   HybridRetriever(DenseRetriever, KeywordRetriever), AnswerGenerator
```

Implementation details: [design/parsing.md](design/parsing.md), [design/chunking.md](design/chunking.md), [design/retrieval.md](design/retrieval.md), [design/answering.md](design/answering.md), [design/conversations.md](design/conversations.md). Decisions: [adr/](adr/). Changes per milestone: [CHANGELOG.md](../CHANGELOG.md).

`api/dependencies.py` wires the concrete adapters and shared stores together once per process. Routes translate request/response schemas and application errors into HTTP responses; they do not own document processing or question answering logic.

`services/` contains the workflows, their output models, error types, and dependency protocols. It does not import FastAPI or API request/response models. The parser factory and the embedder defer expensive model loading until first use. Tests replace dependencies through FastAPI overrides or call services directly without HTTP.

`storage/sqlite_repository.py` persists document snapshots, including blocks, chunks, provenance, and processing state. `storage/chroma_vector_index.py` stores versioned Chroma collections and a SQLite index manifest. The in-memory registry and `InMemoryVectorIndex` remain available for lightweight tests. Re-parsing invalidates the previous index, and failures prevent stale content from being queried. Uploads enqueue in-process background work. Document locks reject duplicate processing and deletion while processing; interruption recovery allows explicit retry.

Retrieval is backend-neutral. A `RetrievalScope` (explicit document ids, resolved by the application from documents, collections, or filters) is passed to a `Retriever`; `DenseRetriever` embeds the query, asks the `VectorIndex` for hits across the scope, and resolves chunk text and provenance from authoritative document records. Results carry `rank` and `retrieval_method` for diagnostics. To change the vector backend (e.g. Milvus), implement `VectorIndex` in `services/ports.py` and add it to `tests/retrieval/test_vector_index_contract.py`. To change answer generation, supply an `Answerer`. Keep external API schemas in `api/models.py` and workflow outputs in `services/models.py`.

## Persistence and restart verification

By default all runtime data lives in `backend/data/`, regardless of the working directory:

```text
data/
  documents.sqlite3       # document snapshots and active index manifest
  chroma/                 # persistent vectors, one versioned collection per document
  uploads/                # original uploaded files
```

Set `AVIATLAS_DATA_DIR` in `backend/.env` to use a different directory; an absolute path is recommended. Keep the whole directory together when moving or backing it up, with the server stopped. Upload paths are stored as absolute paths, so moving the upload directory requires updating those paths. Runtime files are excluded from Git at the default location.

Document mutations are explicitly saved to SQLite. An index becomes queryable only after all vectors are written, the manifest is committed, and the document reaches `indexed`. On restart, records left in `queued`, `parsing`, or `indexing` are marked `failed` and their active indexes invalidated; parse them again to retry. This is recovery for a single process, not a background task queue or a distributed transaction across SQLite and Chroma.

The manifest records the embedding model name and collection version; collection metadata validates document identity and chunk IDs. Changing `EMBEDDING_MODEL` requires re-parsing affected documents. Restarting with the same model does not re-embed document chunks; only the question is embedded. Model weights may still load into memory on the first query. Keep the same model files for reproducible results: model-name validation does not detect an upstream weight update under the same name.

Manual acceptance check:

1. Upload a PDF, parse it, and ask a question; save the `document_id`.
2. Stop the server completely, then start it again with the same data directory.
3. Call `/api/v1/documents/ask` with the saved ID; no upload or parsing is needed.
4. Confirm the source text, page range, and page coordinates are preserved.

Existing documents from the earlier in-memory version cannot be recovered by ID because their registry was never saved. Upload and parse those PDFs once with this version; existing upload files are left untouched.

Storage integration tests use real SQLite and Chroma, deterministic embeddings, and a fake answer generator. They verify document isolation, source round-trips, index replacement, model changes, failed processing, recovery, and a full interpreter exit/restart without network calls.

## Document management

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/documents?limit=20&offset=0` | Newest documents first, paginated summaries with `total` |
| `GET /api/v1/documents/{id}` | Filename, status, timestamps, page/block/chunk counts, and `last_error` |
| `POST /api/v1/documents/{id}/process` | Queue processing or re-indexing; returns 202 immediately |
| `POST /api/v1/documents/{id}/retry` | Queue a failed document again; returns 202 |
| `DELETE /api/v1/documents/{id}` | Remove the PDF, vector collection, manifest, and document record; returns 204 |

Status flow: `uploaded → queued → parsing → indexing → indexed`, or `failed`. Poll the detail endpoint every few seconds to see the current stage; counts are not a percentage progress estimate. Details and lists exclude file paths and full document text.

Upload defaults to `auto_process=true`. To retain the earlier manual workflow, call `/documents/upload?auto_process=false`, then use the background `/documents/{id}/process` endpoint or the existing synchronous `/documents/parse?document_id=...` endpoint.

Duplicate processing returns 409. Processing documents cannot be deleted until their task finishes. A queued document can be deleted; its callback will skip the removed record. A failed deletion remains in `deleting` with an error and can be retried with DELETE; startup also resumes incomplete deletions. Retrying processing is allowed only for `failed` records that still have their uploaded file. Re-upload a document if the file is missing.

Restart interruption marks queued/processing records as failed rather than silently continuing them. Retry them explicitly. Keep one server process per data directory. Existing API clients using explicit parse should upload with `auto_process=false` to avoid competing with automatic processing.


## Collections

Collection tables are created automatically in the existing SQLite database at startup. Existing documents remain visible under all documents with no collection memberships. A document can belong to multiple collections without copying files or rebuilding vectors.

| Endpoint | Behavior |
| --- | --- |
| `POST /api/v1/collections` | Create with JSON `{"name":"Physics"}`; returns 201 |
| `GET /api/v1/collections` | List collections, timestamps, and document counts |
| `PATCH /api/v1/collections/{id}` | Rename with JSON `{"name":"Mechanics"}` |
| `DELETE /api/v1/collections/{id}` | Delete the collection and memberships, retaining documents and indexes |
| `GET /api/v1/documents?collection_id={id}` | Filter before pagination; `total` is the filtered count |
| `GET /api/v1/documents/{id}/collections` | Read a document's collection IDs |
| `PUT /api/v1/documents/{id}/collections` | Atomically replace memberships with `{"collection_ids":["<UUID>"]}`; `[]` removes all memberships |
| `POST /api/v1/documents/upload?collection_id={id}` | Upload directly into an existing collection |

Document summaries include `collection_ids`. Collection names are trimmed, must contain 1–100 characters, and are unique using SQLite NOCASE collation (ASCII case-insensitive). Unknown collections return 404; invalid or duplicate names return 400 or request validation errors. Deleting a document cascades its memberships. Collection changes do not affect document processing state, evidence, or search indexes. Collections do not yet provide cross-document search or Q&A.
