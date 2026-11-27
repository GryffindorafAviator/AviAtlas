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

Open http://127.0.0.1:8000/docs to upload and query a PDF:

1. `POST /api/v1/documents/upload` — multipart field `file`; saves the PDF and queues background processing by default. Save the returned `document_id`.
2. `GET /api/v1/documents/{document_id}` — poll until `status` is `indexed`; inspect `last_error` if `failed`.
3. `POST /api/v1/documents/ask` — JSON:

```json
{"document_id":"<returned UUID>","question":"这本书的核心观点是什么？","top_k":3}
```

Answers use `[1]`, `[2]`, etc. to refer to the ordered `sources` array. Each source carries original text, page range, and page coordinates. `confidence` remains null: retrieval similarity is not answer confidence. Insufficient evidence should be stated in the answer.

The multilingual embedding model and Docling models download on first use. Embeddings run locally; the question and retrieved excerpts are sent to the configured Responses-compatible provider, with `store=False`. Missing model configuration returns 503; provider failures return 502.

## Current limits

Run a single server worker and one application instance per data directory. There is no user authentication, cross-document query, conversation history, or frontend yet. Background processing runs in this server process using FastAPI background tasks; it is not an external worker or durable job queue. The legacy `/documents/parse` endpoint still waits for processing to finish. The chunker preserves entire blocks, so exceptionally long blocks can exceed the embedding model's input window. Citations are requested through prompting; citation correctness and answer quality need evaluation with real books.

## Tests

```sh
python -m pytest tests -q
```

API tests use deterministic encodings and a fake provider to verify orchestration, citation metadata, isolation, and failure behavior without external calls.

Responses integration follows [OpenAI's text generation documentation](https://developers.openai.com/api/docs/guides/text).

## Architecture

```text
HTTP API (api/main.py)
  ├─ DocumentService: upload → parse → chunk → build index
  └─ QuestionAnsweringService: retrieve → generate → return evidence
       ↕ shared document repository and index repository
Concrete adapters: Docling, GreedyChunker, SemanticRetriever, AnswerGenerator
```

`api/dependencies.py` wires the concrete adapters and shared stores together once per process. Routes translate request/response schemas and application errors into HTTP responses; they do not own document processing or question answering logic.

`services/` contains the workflows, their output models, error types, and dependency protocols. It does not import FastAPI or API request/response models. Parser and index factories defer expensive model construction until document processing. Tests replace dependencies through FastAPI overrides or call services directly without HTTP.

`storage/sqlite_repository.py` persists document snapshots, including blocks, chunks, provenance, and processing state. `storage/persistent_indexes.py` stores versioned Chroma collections and a SQLite index manifest. The in-memory registry and index store remain available for lightweight tests. Re-parsing invalidates the previous index, and failures prevent stale content from being queried. Uploads enqueue in-process background work. Document locks reject duplicate processing and deletion while processing; interruption recovery allows explicit retry.

To change retrieval, supply a matching index factory and index repository implementing the service protocols. The persistent repository currently saves built `SemanticRetriever` vectors and opens them as `PersistentSearchIndex` objects. To change answer generation, supply an `Answerer`. Keep external API schemas in `api/models.py` and workflow outputs in `services/models.py`.

## Persistence and restart verification

By default all runtime data lives in `backend/data/`, regardless of the working directory:

```text
data/
  documents.sqlite3       # document snapshots and active index manifest
  chroma/                 # persistent vectors, one versioned collection per document
  uploads/                # original PDF files
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
