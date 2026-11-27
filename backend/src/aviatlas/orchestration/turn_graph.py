"""One conversation turn as a LangGraph graph (ADR 0002).

condense_question → retrieve → (no_evidence | build_context → generate) → construct_citations

Retrieval always runs before generation; in grounded mode an empty result ends
the turn without calling the model. Nodes report progress through the custom
stream: {"event": "retrieval", ...} and {"event": "delta", "text": ...}.
"""
from __future__ import annotations
from collections.abc import Callable, Sequence
from threading import Event
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from aviatlas.answering.generator import AnswerConfigurationError, AnswerGenerationError
from aviatlas.answering.segments import invalid_citations, split_segments
from aviatlas.retrieval.models import RetrievalResult
from aviatlas.services.errors import GenerationFailed, GenerationUnavailable
from aviatlas.services.models import AnswerMode, AnswerSegment, Selection
from aviatlas.services.ports import ConversationalAnswerer
from aviatlas.services.qa import NO_DOCUMENTS, NO_EVIDENCE


class Stopped(Exception):
    """The user cancelled the turn."""


class TurnState(TypedDict, total=False):
    question: str
    mode: AnswerMode
    selection: Selection | None
    history: list[dict]
    has_documents: bool
    search_query: str
    sources: list[RetrievalResult]
    raw: str
    segments: list[AnswerSegment]
    invalid_citations: list[int]


def build_turn_graph(answerer: ConversationalAnswerer, retrieve: Callable[[str], list[RetrievalResult]],
                     cancelled: Event, *, history_chars: int = 8000):
    def condense_question(state: TurnState) -> TurnState:
        question, history = state["question"], state.get("history") or []
        query = question
        if history:
            try:
                query = answerer.condense(question, history) or question
            except (AnswerGenerationError, AnswerConfigurationError):
                # Without a rewrite, the previous question still carries the topic.
                previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
                query = f"{previous}\n{question}".strip()
        selection = state.get("selection")
        # The selected passage sharpens retrieval toward the part being read.
        return {"search_query": f"{query}\n{selection.text}" if selection else query}

    def retrieve_node(state: TurnState) -> TurnState:
        sources = retrieve(state["search_query"]) if state["has_documents"] else []
        get_stream_writer()({"event": "retrieval", "search_query": state["search_query"], "sources": sources})
        return {"sources": sources}

    def route(state: TurnState) -> str:
        return "no_evidence" if not state["sources"] and state["mode"] == "grounded" else "build_context"

    def no_evidence(state: TurnState) -> TurnState:
        message = NO_EVIDENCE if state["has_documents"] else NO_DOCUMENTS
        return {"raw": message, "segments": [AnswerSegment("library", message)], "invalid_citations": []}

    def build_context(state: TurnState) -> TurnState:
        return {"history": _fit(state.get("history") or [], history_chars)}

    def generate(state: TurnState) -> TurnState:
        if cancelled.is_set():
            raise Stopped
        write, parts = get_stream_writer(), []
        try:
            stream = answerer.stream(state["question"], state["sources"], mode=state["mode"],
                                     selection=state.get("selection"), history=state.get("history") or None)
            try:
                for delta in stream:
                    # Text that arrives after a stop is dropped, so the saved partial answer is what the user saw.
                    if cancelled.is_set():
                        raise Stopped
                    parts.append(delta)
                    write({"event": "delta", "text": delta})
            finally:
                stream.close()
        except AnswerConfigurationError as exc:
            raise GenerationUnavailable(str(exc)) from exc
        except AnswerGenerationError as exc:
            raise GenerationFailed(str(exc)) from exc
        return {"raw": "".join(parts)}

    def construct_citations(state: TurnState) -> TurnState:
        segments = split_segments(state["raw"], state["mode"])
        if not segments:
            raise GenerationFailed("The model returned an empty answer. Please retry.")
        return {"segments": segments, "invalid_citations": invalid_citations(state["raw"], len(state["sources"]))}

    graph = StateGraph(TurnState)
    graph.add_node("condense_question", condense_question)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("no_evidence", no_evidence)
    graph.add_node("build_context", build_context)
    graph.add_node("generate", generate)
    graph.add_node("construct_citations", construct_citations)
    graph.add_edge(START, "condense_question")
    graph.add_edge("condense_question", "retrieve")
    graph.add_conditional_edges("retrieve", route, ["no_evidence", "build_context"])
    graph.add_edge("no_evidence", END)
    graph.add_edge("build_context", "generate")
    graph.add_edge("generate", "construct_citations")
    graph.add_edge("construct_citations", END)
    return graph.compile()


def _fit(history: Sequence[dict], budget: int) -> list[dict]:
    """Keep the newest messages whose total length fits the budget, oldest dropped first."""
    kept, used = [], 0
    for message in reversed(history):
        used += len(message["content"])
        if used > budget:
            break
        kept.append(message)
    kept.reverse()
    # Start on a user message so the history reads as whole exchanges.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept
