"""Workflow inputs and outputs; no dependency on API schemas."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID
from aviatlas.retrieval.models import RetrievalResult

# Policies from System Design §10.4. "grounded" covers document- and
# library-grounded answering; the scope decides which of the two it is.
AnswerMode = Literal["grounded", "open"]
# Knowledge source types from PRD §7.1 that answering can currently produce.
SourceType = Literal["library", "model", "unlabeled"]

@dataclass
class ParseResult:
    document_id: UUID
    status: str
    blocks_count: int
    chunks_count: int
    message: str = "Document parsed and indexed successfully."

@dataclass
class ScopeRequest:
    """What the user asked to search: explicit documents, a collection, or (neither) the whole library."""
    document_ids: list[UUID] | None = None
    collection_id: UUID | None = None

@dataclass
class ResolvedScope:
    document_ids: list[UUID]
    # Requested via a collection or the library but not searchable yet.
    skipped_document_ids: list[UUID] = field(default_factory=list)

@dataclass
class Selection:
    """Text the reader selected in a document, used as reading context."""
    text: str
    document_id: UUID | None = None
    page_number: int | None = None

@dataclass
class AnswerSegment:
    source_type: SourceType
    text: str

@dataclass
class SearchResult:
    query: str
    results: list[RetrievalResult]
    document_ids: list[UUID]
    skipped_document_ids: list[UUID] = field(default_factory=list)

@dataclass
class AnswerResult:
    document_id: UUID | None
    question: str
    answer: str
    sources: list[RetrievalResult]
    created_at: datetime | None = None
    mode: AnswerMode = "grounded"
    segments: list[AnswerSegment] = field(default_factory=list)
    # Citation numbers in the answer that match no supplied source (§10.7).
    invalid_citations: list[int] = field(default_factory=list)
    document_ids: list[UUID] = field(default_factory=list)
    skipped_document_ids: list[UUID] = field(default_factory=list)
