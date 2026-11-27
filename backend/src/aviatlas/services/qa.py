"""Retrieve evidence and generate source-aware answers independently of HTTP."""
import logging
from datetime import datetime, timezone
from uuid import UUID
from aviatlas.answering.generator import AnswerConfigurationError, AnswerGenerationError
from aviatlas.answering.segments import invalid_citations, join_segments, split_segments
from aviatlas.retrieval.hybrid import METHODS, RetrievalRun
from aviatlas.retrieval.models import RetrievalResult
from aviatlas.services.errors import (
    InvalidInput, DocumentNotFound, DocumentNotReady, RetrievalUnavailable,
    GenerationUnavailable, GenerationFailed,
)
from aviatlas.services.models import (
    AnswerMode, AnswerResult, AnswerSegment, ResolvedScope, ScopeRequest, SearchResult, Selection,
)
from aviatlas.services.ports import DocumentRepository, Answerer, Retriever
from aviatlas.services.scope import ScopeResolver

logger = logging.getLogger(__name__)

MAX_SELECTION_CHARS = 2000
NO_EVIDENCE = "文档中没有可用于回答此问题的内容。"
NO_DOCUMENTS = "当前范围内没有可检索的文档。请先上传并处理文档。"


def validate_question(question: str, mode: str, selection: Selection | None,
                      top_k: int) -> tuple[str, Selection | None]:
    """Return the stripped question and selection (None if blank)."""
    query = question.strip()
    if not query:
        raise InvalidInput("Question cannot be empty.")
    if not 1 <= top_k <= 10:
        raise InvalidInput("top_k must be between 1 and 10.")
    if mode not in ("grounded", "open"):
        raise InvalidInput("mode must be 'grounded' or 'open'.")
    if selection is not None:
        text = selection.text.strip()
        if not text:
            return query, None
        if len(text) > MAX_SELECTION_CHARS:
            raise InvalidInput(f"Selected text must be at most {MAX_SELECTION_CHARS} characters.")
        selection = Selection(text, selection.document_id, selection.page_number)
    return query, selection


def retrieve_evidence(retriever: Retriever, resolver: ScopeResolver, resolved: ResolvedScope,
              query: str, top_k: int) -> list[RetrievalResult]:
    if not resolved.document_ids:
        return []
    try:
        return retriever.search(query, resolver.retrieval_scope(resolved), top_k)
    except DocumentNotReady:
        raise
    except Exception as exc:
        logger.exception("Retrieval failed for %d documents", len(resolved.document_ids))
        raise RetrievalUnavailable("Retrieval failed. Please retry.") from exc


class SearchService:
    """Knowledge search; available even when no answer model is configured (§15.4)."""

    def __init__(self, retriever, resolver: ScopeResolver):
        self.retriever = retriever  # a HybridRetriever: supports method= and run()
        self.resolver = resolver

    @staticmethod
    def _validate(query: str, top_k: int, method: str) -> str:
        query = query.strip()
        if not query:
            raise InvalidInput("Search query cannot be empty.")
        if not 1 <= top_k <= 50:
            raise InvalidInput("top_k must be between 1 and 50.")
        if method not in METHODS:
            raise InvalidInput(f"method must be one of: {', '.join(METHODS)}.")
        return query

    def search(self, query: str, scope: ScopeRequest, top_k: int = 10, method: str = "hybrid") -> SearchResult:
        query = self._validate(query, top_k, method)
        resolved = self.resolver.resolve(scope)
        run = self._run(resolved, query, top_k, method)
        return SearchResult(query, run.results if run else [], resolved.document_ids, resolved.skipped_document_ids)

    def inspect(self, query: str, scope: ScopeRequest, top_k: int = 10,
                method: str = "hybrid") -> tuple[RetrievalRun, ResolvedScope]:
        """Retrieval with every stage's candidates, scores, and timings (§9.8)."""
        query = self._validate(query, top_k, method)
        resolved = self.resolver.resolve(scope)
        return self._run(resolved, query, top_k, method) or RetrievalRun(query, method, []), resolved

    def _run(self, resolved: ResolvedScope, query: str, top_k: int, method: str) -> RetrievalRun | None:
        if not resolved.document_ids:
            return None
        try:
            return self.retriever.run(query, self.resolver.retrieval_scope(resolved), top_k, method)
        except DocumentNotReady:
            raise
        except Exception as exc:
            logger.exception("Retrieval failed for %d documents", len(resolved.document_ids))
            raise RetrievalUnavailable("Retrieval failed. Please retry.") from exc


class QuestionAnsweringService:
    def __init__(self, repository: DocumentRepository, retriever: Retriever, answerer: Answerer,
                 resolver: ScopeResolver | None = None):
        self.repository = repository
        self.retriever = retriever
        self.answerer = answerer
        self.resolver = resolver or ScopeResolver(repository)

    def ask(self, document_id: UUID, question: str, top_k: int = 3) -> AnswerResult:
        """Document-grounded question answering over one document."""
        record = self.repository.get(document_id)
        if record is None:
            raise DocumentNotFound("Document not found.")
        if not record.chunks:
            raise DocumentNotReady("Document has not been indexed yet. Please parse it first.")
        return self.answer(question, ScopeRequest(document_ids=[document_id]), top_k=top_k)

    def answer(self, question: str, scope: ScopeRequest, *, mode: AnswerMode = "grounded",
               selection: Selection | None = None, top_k: int = 5) -> AnswerResult:
        query, selection = validate_question(question, mode, selection, top_k)
        resolved = self.resolver.resolve(scope)
        single = scope.document_ids[0] if scope.document_ids and len(scope.document_ids) == 1 else None
        # The selected passage sharpens retrieval toward the part being read.
        retrieval_query = f"{query}\n{selection.text}" if selection else query
        sources = retrieve_evidence(self.retriever, self.resolver, resolved, retrieval_query, top_k)

        def result(segments: list[AnswerSegment], invalid: list[int] | None = None) -> AnswerResult:
            return AnswerResult(single, question, join_segments(segments), sources,
                                datetime.now(timezone.utc), mode, segments, invalid or [],
                                resolved.document_ids, resolved.skipped_document_ids)

        if not sources and mode == "grounded":
            # Never ask the model to answer without evidence in grounded modes.
            message = NO_EVIDENCE if resolved.document_ids else NO_DOCUMENTS
            return result([AnswerSegment("library", message)])
        try:
            raw = self.answerer.generate(query, sources, mode=mode, selection=selection)
        except AnswerConfigurationError as exc:
            raise GenerationUnavailable(str(exc)) from exc
        except AnswerGenerationError as exc:
            raise GenerationFailed(str(exc)) from exc
        segments = split_segments(raw, mode)
        if not segments:
            raise GenerationFailed("The model returned an empty answer. Please retry.")
        return result(segments, invalid_citations(raw, len(sources)))
