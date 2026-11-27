# Conversations — Implementation

Implements System Design §5.10 (initial graph), §5.13 (short-term context), §13.4, FR-ASST-04/05/12/13. Status: M4. Boundary and graph per ADR 0002.

## Modules (`backend/src/aviatlas`)

| Module | Role |
|---|---|
| `orchestration/turn_graph.py` | LangGraph graph for one turn |
| `services/conversations.py` | `ConversationService`, `Turn` (thread + event queue) |
| `storage/conversation_repository.py` | SQLite tables below |
| `answering/generator.py` | `stream` (Responses streaming), `condense` (follow-up rewrite) |

`QuestionAnsweringService` / `POST /api/v1/ask` stays as the stateless baseline (ADR 0002 §7).

## Turn graph

```
condense_question → retrieve ─┬─ no_evidence (grounded, no sources) ──────────────→ END
                              └─ build_context → generate → construct_citations → END
```

| Node | Does |
|---|---|
| `condense_question` | With history: model rewrites the question into a standalone query. On failure: previous user question + current question. Appends the selection. |
| `retrieve` | Hybrid retrieval over the conversation scope; emits `retrieval` |
| `build_context` | Trims history to the character budget |
| `generate` | Streams deltas; checks the stop flag before each delta |
| `construct_citations` | `split_segments`, `invalid_citations` (same as [answering.md](answering.md)) |

The model answers the original question; only retrieval uses the rewritten one. The rewrite is shown in the UI (`检索：…`) because a wrong rewrite explains wrong sources.

## Context

| Parameter | Value |
|---|---|
| History | last 6 completed exchanges (`history_turns`) |
| Budget | 8000 characters, oldest dropped first; history always starts on a user message |
| Excluded | failed and stopped turns, and their questions |
| Evidence | current turn only; earlier citation numbers are declared stale in the instructions |

Failed and stopped turns are excluded so a half answer or an error is not treated as something the assistant said.

## Storage (`documents.sqlite3`)

```
conversations(id PK, title, scope JSON, document_id FK→documents ON DELETE CASCADE, created_at, updated_at)
messages(id PK, conversation_id FK ON DELETE CASCADE, seq, role, content, status, payload JSON, created_at)
message_sources(message_id FK ON DELETE CASCADE, position, document_id FK→documents ON DELETE CASCADE, payload JSON)
```

- Scope is fixed at creation; `document_id` is set only for single-document conversations.
- `status`: `streaming` → `complete` | `stopped` (partial text kept) | `failed` (`payload.error`).
- Title: first question, 60 characters, unless set at creation.
- No LangGraph checkpointer (ADR 0002, amended).

## Deletion policy (§18.5)

| Deleted | Effect |
|---|---|
| Document | Its single-document conversations are deleted. In other conversations its stored excerpts are deleted; the answer text stays and `removed_source_count` reports the gap. |
| Collection | Conversations remain; the next question fails with 404. |
| Conversation | Messages and sources deleted. Refused (409) while an answer is streaming. |

Excerpts are document content, so they follow the document. Answer text is the user's conversation record and stays.

## Streaming

`POST /api/v1/conversations/{id}/messages` returns `text/event-stream`:

| Event | Data |
|---|---|
| `started` | `user`, `assistant` (status `streaming`) messages |
| `retrieval` | `search_query`, `sources` (with `citation`), `document_ids`, `skipped_document_ids` |
| `delta` | `text` |
| `done` | final `message` (`complete` or `stopped`) |
| `error` | `detail`, saved `message` (`failed`) |

Validation, unknown conversation (404), and a busy conversation (409) are ordinary HTTP errors before the stream starts.

Each turn runs in its own thread and writes events to a queue; the HTTP response only reads the queue. On client disconnect the response sets the stop flag; the thread stops at the next delta, closes the provider stream, saves `stopped`, and releases the conversation. This guarantees one final write per turn however the client leaves. One turn per conversation at a time (in-process lock). Startup marks leftover `streaming` messages `failed`.

## Privacy

LangGraph installs `langsmith`; tracing stays off unless `LANGSMITH_TRACING` is set. Do not set it with real documents: traces would send questions and excerpts to LangSmith.

## Known limits

- Follow-up rewriting costs one extra model call per turn with history.
- Lock and stop flag are in-process: single worker only (same as the rest of the app).
- No message editing or regeneration.
