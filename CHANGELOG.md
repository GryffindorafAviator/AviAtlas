# Changelog

## M5 — Formats, OCR, structured chunking

- Upload DOCX, Markdown, and TXT (UTF-8 or GBK) besides PDF; content is checked against the extension.
- PDFs routed by text layer: no OCR for text PDFs, region OCR for mixed, full-page OCR for scans. OCR page count shown in the library.
- PDF formulas recovered from the text layer (previously empty); duplicate table captions and table padding removed.
- Structured chunker: tables with captions in their own chunks (long tables split by rows with repeated header), formulas and code kept with their text, headings never end a chunk, section titles on every chunk.
- Text reader for non-PDF documents; sources jump to the cited block. Results carry `block_ids`.
- `GET /api/v1/documents/{id}/content`; `/file` serves the original with its media type.
- Documents from the old chunker show 重新处理.
- Fixes: concurrent document processing crashed the server (Apple GPU); embeddings now run on CPU and calls are serialized. Docling models are no longer reloaded for every document.
- Migration: none required; reprocess documents to apply the new chunking.

## M4 — Conversations

- Multi-turn conversations per document, collection, or library; stored in SQLite.
- Each turn is a LangGraph graph: follow-up rewrite → hybrid retrieval → streamed generation → citations.
- `POST /api/v1/conversations/{id}/messages` streams SSE; plus create, list, read, rename, delete endpoints.
- Stop button; disconnecting keeps the partial answer as `stopped`.
- Frontend: conversation picker, 新对话, 删除, live streaming, rewritten query shown as `检索：…`.
- Document deletion removes its single-document conversations and its excerpts elsewhere.
- New dependency: `langgraph`. Migration: tables created on startup.

## M2 — Hybrid retrieval (fe5f7ec)

- BM25 keyword retrieval (jieba) with a SQLite keyword index.
- Hybrid retrieval: dense + BM25, fused with RRF. Default for search and ask.
- `POST /api/v1/search` accepts `method`: `hybrid` | `dense` | `bm25`. Results add `rank`, `retrieval_method`, `scores`, `ranks`, `matched_terms`.
- `POST /api/v1/search/debug` and the 检索调试 tab.
- Migration: keyword index built at startup from stored chunks. No reparsing.

## Fixes (e96039b)

- Identical uploads rejected with 409 (SHA-256); duplicates made every hit appear twice. Existing duplicates are kept for the user to delete.
- Titles come from the parser's first title block; UUID titles cleared at startup. Docling used the storage filename (a UUID) as the title. `document_name` is the original filename.

## M1 — Library search and answering (1ef40f0)

- `POST /api/v1/search`: document, collection, or library scope.
- `POST /api/v1/ask`: same scopes, `grounded` / `open` modes, source-typed segments, citation check, selected-text questions.
- Frontend: 搜索 and 问答 tabs, mode toggle, selection chip, source labels.
- `POST /api/v1/documents/ask` kept for compatibility.
