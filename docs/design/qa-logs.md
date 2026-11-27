# Q&A Logs — Implementation

Implements part of System Design §16 (structured logging, retrieval diagnostics). Status: M6. `storage/qa_log_repository.py`; written by `QuestionAnsweringService.answer` and conversation turns.

One row per question, for `/api/v1/ask` (`kind: ask`) and conversation messages (`kind: conversation`):

| Field | Content |
|---|---|
| `status` | `complete`, `no_evidence` (grounded, nothing retrieved), `stopped`, `failed` |
| `question`, `search_query` | the question and the query retrieval ran (after follow-up rewriting and selection) |
| `mode`, `scope`, `document_ids`, `skipped_document_ids` | request and resolved scope |
| `sources` | per citation: document id and name, chunk id, pages, section, rank, per-strategy scores and ranks — no excerpt text |
| `answer`, `segments`, `invalid_citations` | the answer as returned |
| `model`, `error` | configured model; the user-facing error message |
| `timings_ms` | `retrieval`, `generation` (ask) or `first_token` (conversation), `total` |
| `conversation_id`, `message_id` | for conversation turns |

Writing a log never fails an answer (errors are logged and ignored).

API: `GET /api/v1/logs?limit=&offset=&status=`, `GET /api/v1/logs/{id}`, `DELETE /api/v1/logs/{id}`, `DELETE /api/v1/logs` (clear). Frontend: 问答日志 tab.

## Retention and deletion

- The newest 2000 rows are kept; older rows are deleted on insert.
- Sources are references, so deleting a document leaves no excerpt in the logs; its id and file name remain until the row ages out or the log is cleared. Questions and answers remain like conversation history.
- Deleting a conversation does not delete its log rows.
