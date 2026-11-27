"""Multi-turn conversations: persistence, context, and streamed turns.

A turn runs in its own thread and publishes events to a queue. The HTTP stream
only reads that queue, so a client that disconnects or stops cannot leave a
turn half-saved: the thread always writes the final message and releases the
conversation.
"""
from __future__ import annotations
import logging
import queue
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event, Lock, Thread
from uuid import UUID

from aviatlas.answering.segments import join_segments, split_segments
from aviatlas.orchestration.turn_graph import Stopped, build_turn_graph
from aviatlas.services.errors import ConversationBusy, ConversationNotFound, InvalidInput, ServiceError
from aviatlas.services.models import (
    AnswerMode, ConversationRecord, MessageRecord, ResolvedScope, ScopeRequest, Selection,
)
from aviatlas.services.ports import ConversationalAnswerer, Retriever
from aviatlas.services.qa import retrieve_evidence, validate_question
from aviatlas.services.scope import ScopeResolver
from aviatlas.storage.conversation_repository import SQLiteConversationRepository

logger = logging.getLogger(__name__)

TITLE_CHARS = 60
MAX_TITLE_CHARS = 200
INTERRUPTED = "Answer was interrupted. Please ask again."
_END = object()


@dataclass
class TurnEvent:
    """`started` {user, assistant}, `retrieval` {search_query, sources, ...}, `delta` {text}, `done` {message}, `error` {detail, message}."""
    name: str
    data: dict


class Turn:
    def __init__(self, service: ConversationService, conversation_id: UUID, user: MessageRecord,
                 assistant: MessageRecord, state: dict, resolved: ResolvedScope, top_k: int, lock: Lock):
        self.conversation_id, self.user, self.assistant = conversation_id, user, assistant
        self._service, self._state, self._resolved, self._top_k, self._lock = service, state, resolved, top_k, lock
        self._events: queue.Queue = queue.Queue()
        self._cancelled = Event()
        self._thread = Thread(target=self._run, name=f"turn-{assistant.message_id}", daemon=True)

    def start(self) -> Turn:
        self._events.put(TurnEvent("started", {"user": self.user, "assistant": self.assistant}))
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancelled.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def events(self) -> Iterator[TurnEvent]:
        while (event := self._events.get()) is not _END:
            yield event

    def _run(self) -> None:
        service, mode = self._service, self._state["mode"]
        sources, search_query, parts = [], None, []
        payload = {"mode": mode, "document_ids": [str(d) for d in self._resolved.document_ids],
                   "skipped_document_ids": [str(d) for d in self._resolved.skipped_document_ids]}

        def finish(content: str, status: str, **extra) -> MessageRecord:
            final = {**payload, "search_query": search_query, "source_count": len(sources), **extra}
            service.repository.finish_message(self.assistant.message_id, content, status, final, sources)
            return next(m for m in service.repository.messages(self.conversation_id)
                        if m.message_id == self.assistant.message_id)

        try:
            graph = build_turn_graph(
                service.answerer,
                lambda query: retrieve_evidence(service.retriever, service.resolver, self._resolved, query, self._top_k),
                self._cancelled, history_chars=service.history_chars)
            state = {}
            for kind, chunk in graph.stream(self._state, stream_mode=["custom", "values"]):
                if kind == "values":
                    state = chunk
                elif chunk["event"] == "retrieval":
                    sources, search_query = chunk["sources"], chunk["search_query"]
                    self._events.put(TurnEvent("retrieval", {
                        "search_query": search_query, "sources": sources, **{
                            k: payload[k] for k in ("document_ids", "skipped_document_ids")}}))
                else:
                    parts.append(chunk["text"])
                    self._events.put(TurnEvent("delta", {"text": chunk["text"]}))
            segments = state["segments"]
            message = finish(join_segments(segments), "complete",
                             segments=[s.__dict__ for s in segments], invalid_citations=state["invalid_citations"])
            self._events.put(TurnEvent("done", {"message": message}))
        except Stopped:
            partial = split_segments("".join(parts), mode)
            message = finish(join_segments(partial), "stopped", segments=[s.__dict__ for s in partial],
                             invalid_citations=[])
            self._events.put(TurnEvent("done", {"message": message}))
        except Exception as exc:
            if isinstance(exc, ServiceError):
                detail = str(exc)
                if exc.__cause__ is not None:  # provider or retrieval failure behind a user-facing message
                    logger.warning("Conversation turn failed: %s", self.conversation_id, exc_info=exc)
            else:
                logger.exception("Conversation turn failed: %s", self.conversation_id)
                detail = "Answer failed. Please retry."
            try:
                message = finish("".join(parts), "failed", error=detail)
            except Exception:
                logger.exception("Could not save failed turn: %s", self.assistant.message_id)
                message = None
            self._events.put(TurnEvent("error", {"detail": detail, "message": message}))
        finally:
            self._lock.release()
            self._events.put(_END)


class ConversationService:
    def __init__(self, repository: SQLiteConversationRepository, retriever: Retriever,
                 answerer: ConversationalAnswerer, resolver: ScopeResolver, *,
                 history_turns: int = 6, history_chars: int = 8000):
        self.repository, self.retriever, self.answerer, self.resolver = repository, retriever, answerer, resolver
        self.history_turns, self.history_chars = history_turns, history_chars
        self._locks: dict[UUID, Lock] = {}
        self._locks_guard = Lock()

    def _lock(self, conversation_id: UUID) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(conversation_id, Lock())

    @staticmethod
    def _title(title: str | None) -> str | None:
        if title is None:
            return None
        title = " ".join(title.split())
        if not title or len(title) > MAX_TITLE_CHARS:
            raise InvalidInput(f"Titles must contain 1 to {MAX_TITLE_CHARS} characters.")
        return title

    def create(self, scope: ScopeRequest, title: str | None = None) -> ConversationRecord:
        self.resolver.resolve(scope)  # Unknown documents or collections fail now, not on the first question.
        return self.repository.create(scope.document_ids, scope.collection_id, self._title(title))

    def list(self, limit: int = 100) -> list[ConversationRecord]:
        return self.repository.list(limit)

    def get(self, conversation_id: UUID) -> ConversationRecord:
        record = self.repository.get(conversation_id)
        if record is None:
            raise ConversationNotFound("Conversation not found.")
        return record

    def messages(self, conversation_id: UUID) -> list[MessageRecord]:
        self.get(conversation_id)
        return self.repository.messages(conversation_id)

    def rename(self, conversation_id: UUID, title: str) -> ConversationRecord:
        if not self.repository.rename(conversation_id, self._title(title)):
            raise ConversationNotFound("Conversation not found.")
        return self.get(conversation_id)

    def delete(self, conversation_id: UUID) -> None:
        lock = self._lock(conversation_id)
        if not lock.acquire(blocking=False):
            raise ConversationBusy("An answer is still being generated. Stop it or wait, then delete.")
        try:
            if not self.repository.delete(conversation_id):
                raise ConversationNotFound("Conversation not found.")
        finally:
            lock.release()

    def history(self, messages: list[MessageRecord]) -> list[dict]:
        """Completed exchanges only, newest `history_turns`; failed or stopped turns are left out."""
        exchanges = [(q, a) for q, a in zip(messages, messages[1:])
                     if q.role == "user" and a.role == "assistant" and a.status == "complete"]
        recent = exchanges[-self.history_turns:] if self.history_turns else []
        return [m for q, a in recent for m in ({"role": "user", "content": q.content},
                                               {"role": "assistant", "content": a.content})]

    def ask(self, conversation_id: UUID, question: str, *, mode: AnswerMode = "grounded",
            selection: Selection | None = None, top_k: int = 5) -> Turn:
        """Validate, save the question, and start answering. Errors before this returns are HTTP errors;
        later ones arrive as `error` events."""
        query, selection = validate_question(question, mode, selection, top_k)
        conversation = self.get(conversation_id)
        lock = self._lock(conversation_id)
        if not lock.acquire(blocking=False):
            raise ConversationBusy("An answer is still being generated in this conversation.")
        try:
            resolved = self.resolver.resolve(ScopeRequest(conversation.document_ids, conversation.collection_id))
            history = self.history(self.repository.messages(conversation_id))
            user = self.repository.add_message(
                conversation_id, "user", query, "complete",
                {"mode": mode, "selection": selection and {"text": selection.text, "page_number": selection.page_number,
                                                           "document_id": str(selection.document_id) if selection.document_id else None}},
                title=query[:TITLE_CHARS])
            assistant = self.repository.add_message(conversation_id, "assistant", "", "streaming", {"mode": mode})
            state = {"question": query, "mode": mode, "selection": selection, "history": history,
                     "has_documents": bool(resolved.document_ids)}
            return Turn(self, conversation_id, user, assistant, state, resolved, top_k, lock).start()
        except BaseException:
            lock.release()
            raise

    def recover_interrupted(self) -> int:
        return self.repository.fail_unfinished(INTERRUPTED)
