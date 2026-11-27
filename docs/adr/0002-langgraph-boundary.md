# ADR 0002 — Use LangGraph for conversation and agent orchestration only

| Field | Description |
|---|---|
| Status | Accepted; decision 6 amended 2026-11-27 (M4) |
| Date | 2026-11-27 |
| Related | System Design §5.10 (Agent Orchestration), §5.13 (Context and Memory), §10 (Answering), §13.4 (Streaming) |

## Context

Phase 2 introduces multi-turn conversations, streaming responses, and conversation threads. Phase 3 adds tool use, external retrieval, and multi-step research. A graph-based orchestrator offers thread checkpointing, streaming events, and controlled tool loops.

AviAtlas's core value depends on data that general-purpose framework abstractions do not carry: block references, page and region provenance, per-strategy retrieval scores and ranks, and knowledge source types. Grounded answering modes must not let a model skip library retrieval.

## Decision

1. Adopt LangGraph in Phase 2 for conversation and agent orchestration.
2. Keep parsing, chunking, embedding, retrieval (dense, BM25, RRF), provenance, citation construction, and verification in AviAtlas's own components and data contracts. Do not replace them with LangChain document, retriever, or splitter abstractions.
3. Expose AviAtlas retrieval to graphs as nodes or tools that wrap the `Retriever` contract and return AviAtlas result models.
4. In Document-grounded and Library-grounded modes, retrieval is a deterministic graph node that runs before generation (prefetch). The model cannot skip it.
5. Translate LangGraph streaming events into AviAtlas-defined SSE events (§13.4). Do not expose framework event formats or internal reasoning to clients.
6. Store conversation threads with a SQLite checkpointer, consistent with the current application database. Conversation and message records remain authoritative application data (§7.3).
7. Keep the existing non-agent answering path (`QuestionAnsweringService`) as the baseline for evaluation and as a fallback.

## Initial graph (Phase 2)

```text
prefetch_retrieve → build_context → generate → construct_citations
```

Phase 3 extends the same graph with tool selection, external retrieval, and iterative retrieval, bounded by limits on execution time, tool calls, and model usage (§5.10).

## Consequences

- Positive: thread state, streaming, and tool loops without custom infrastructure; provenance and diagnostics stay under AviAtlas's control; grounding is enforced by the graph structure.
- Negative: an additional dependency and an adapter layer between framework events and API events; framework upgrades must be checked against that adapter.

## Amendment (M4) — no checkpointer yet

Decision 6 is replaced: conversation state lives only in AviAtlas's `conversations` / `messages` tables; each turn rebuilds graph input from them. A checkpointer would hold a second copy of the same history that deletion and the document deletion policy would have to keep in sync, and the Phase 2 graph has no interrupt or resume that needs one. Add a checkpointer when Phase 3 introduces interruptible or long-running tool loops.

The implemented graph adds `condense_question` before `prefetch_retrieve` and a `no_evidence` branch; see [docs/design/conversations.md](../design/conversations.md).
