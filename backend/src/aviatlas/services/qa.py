"""Retrieve evidence and generate an answer independently of HTTP."""
import logging
from datetime import datetime, timezone
from uuid import UUID
from aviatlas.answering.generator import AnswerConfigurationError, AnswerGenerationError
from aviatlas.services.errors import (
    InvalidInput, DocumentNotFound, DocumentNotReady, RetrievalUnavailable,
    GenerationUnavailable, GenerationFailed,
)
from aviatlas.retrieval.models import RetrievalScope
from aviatlas.services.models import AnswerResult
from aviatlas.services.ports import DocumentRepository, Answerer, Retriever

logger = logging.getLogger(__name__)

class QuestionAnsweringService:
    def __init__(self, repository: DocumentRepository, retriever: Retriever, answerer: Answerer):
        self.repository = repository
        self.retriever = retriever
        self.answerer = answerer

    def ask(self, document_id: UUID, question: str, top_k: int = 3) -> AnswerResult:
        record = self.repository.get(document_id)
        if record is None:
            raise DocumentNotFound("Document not found.")
        if not record.chunks:
            raise DocumentNotReady("Document has not been indexed yet. Please parse it first.")
        query = question.strip()
        if not query:
            raise InvalidInput("Question cannot be empty.")
        if not 1 <= top_k <= 10:
            raise InvalidInput("top_k must be between 1 and 10.")
        if record.status != "indexed":
            raise DocumentNotReady("Document is not ready. Please parse it first.")
        try:
            results = self.retriever.search(query, RetrievalScope.for_document(document_id), top_k)
        except DocumentNotReady:
            raise
        except Exception as exc:
            logger.exception("Document retrieval failed: %s", document_id)
            raise RetrievalUnavailable("Semantic retrieval failed. Please retry.") from exc
        if not results:
            return AnswerResult(document_id, question, "文档中没有可用于回答此问题的内容。", [])
        try:
            answer = self.answerer.generate(query, results)
        except AnswerConfigurationError as exc:
            raise GenerationUnavailable(str(exc)) from exc
        except AnswerGenerationError as exc:
            raise GenerationFailed(str(exc)) from exc
        return AnswerResult(document_id, question, answer, results, datetime.now(timezone.utc))
