"""Workflow outputs; no dependency on API schemas."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from aviatlas.retrieval.models import RetrievalResult

@dataclass
class ParseResult:
    document_id: UUID
    status: str
    blocks_count: int
    chunks_count: int
    message: str = "Document parsed and indexed successfully."

@dataclass
class AnswerResult:
    document_id: UUID
    question: str
    answer: str
    sources: list[RetrievalResult]
    created_at: datetime | None = None
