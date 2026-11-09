# AviAtlas backend

PDF → Docling blocks with page locations → multilingual semantic retrieval → grounded answers with numbered citations.

## Run

From `backend/` (Python 3.11+):

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
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

1. `POST /api/v1/documents/upload` — multipart field `file`; save the returned `document_id`.
2. `POST /api/v1/documents/parse?document_id=...` — returns `indexed` once parsing and embedding finish.
3. `POST /api/v1/documents/ask` — JSON:

```json
{"document_id":"<returned UUID>","question":"这本书的核心观点是什么？","top_k":3}
```

Answers use `[1]`, `[2]`, etc. to refer to the ordered `sources` array. Each source carries original text, page range, and page coordinates. `confidence` remains null: retrieval similarity is not answer confidence. Insufficient evidence should be stated in the answer.

The multilingual embedding model and Docling models download on first use. Embeddings run locally; the question and retrieved excerpts are sent to the configured Responses-compatible provider, with `store=False`. Missing model configuration returns 503; provider failures return 502.

## Current limits

Document records and semantic indexes are in memory. Uploaded files remain on disk, but after a restart you must upload and parse them again. Run a single server worker. There is no user authentication, cross-document query, conversation history, or frontend yet. Parsing is synchronous and may take time for large books. The chunker preserves entire blocks, so exceptionally long blocks can exceed the embedding model's input window. Citations are requested through prompting; citation correctness and answer quality need evaluation with real books.

## Tests

```sh
python -m pytest tests -q
```

API tests use deterministic encodings and a fake provider to verify orchestration, citation metadata, isolation, and failure behavior without external calls.

Responses integration follows [OpenAI's text generation documentation](https://developers.openai.com/api/docs/guides/text).
