"""Pydantic models for API request and response schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from aviatlas.retrieval.models import RetrievalResult


class DocumentUploadResponse(BaseModel):
    """Response schema for document upload."""

    document_id: UUID
    filename: str
    status: str
    message: str


class ParseResponse(BaseModel):
    """Response schema for document parsing."""

    document_id: UUID
    status: str
    blocks_count: int
    chunks_count: int
    message: str


class QuestionRequest(BaseModel):
    """Request body for asking a question about an uploaded PDF."""

    document_id: UUID
    question: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class QuestionAnswer(BaseModel):
    """Grounded answer returned to the user along with its evidence."""

    document_id: UUID
    question: str
    answer: str
    sources: list[RetrievalResult] = Field(default_factory=list)
    confidence: float | None = None
    created_at: datetime | None = None


class AnswerResponse(BaseModel):
    """Backward-compatible response schema for the current answer endpoint."""

    document_id: UUID
    question: str
    answer: str
    sources: list[str] = Field(default_factory=list)
    score: float | None = None

class DocumentSummary(BaseModel):
    document_id: UUID
    filename: str
    title: str | None = None
    status: str
    uploaded_at: datetime
    last_updated_at: datetime
    parsed_at: datetime | None = None
    indexed_at: datetime | None = None
    page_count: int | None = None
    blocks_count: int
    chunks_count: int
    last_error: str | None = None
    collection_ids: list[UUID] = Field(default_factory=list)

class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int

class ProcessingResponse(BaseModel):
    document_id: UUID
    status: str
    message: str = "Document queued. Poll the document detail endpoint for status."


class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CollectionSummary(BaseModel):
    collection_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    document_count: int


class CollectionListResponse(BaseModel):
    items: list[CollectionSummary]


class DocumentCollections(BaseModel):
    collection_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ScopeInput(BaseModel):
    """Retrieval scope: explicit documents, a collection, or (neither) the whole library."""

    document_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    collection_id: UUID | None = None


class SearchRequest(ScopeInput):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=50)
    method: Literal["hybrid", "dense", "bm25"] = "hybrid"


class SearchDebugResponse(BaseModel):
    """Every retrieval stage for one query: candidates, fusion, and timings."""

    query: str
    method: str
    query_terms: list[str]
    document_ids: list[UUID]
    skipped_document_ids: list[UUID]
    dense: list[RetrievalResult]
    bm25: list[RetrievalResult]
    results: list[RetrievalResult]
    timings_ms: dict[str, float]
    config: dict[str, str | int | float]


class SearchResponse(BaseModel):
    query: str
    results: list[RetrievalResult]
    document_ids: list[UUID]
    skipped_document_ids: list[UUID]


class SelectionInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    document_id: UUID | None = None
    page_number: int | None = Field(default=None, ge=1)


class AskRequest(ScopeInput):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["grounded", "open"] = "grounded"
    selection: SelectionInput | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class AnswerSegmentOutput(BaseModel):
    source_type: Literal["library", "model", "unlabeled"]
    text: str


class SourceAwareAnswer(BaseModel):
    """An answer split by knowledge source type, with its evidence and scope."""

    question: str
    answer: str
    mode: Literal["grounded", "open"]
    segments: list[AnswerSegmentOutput]
    sources: list[RetrievalResult]
    invalid_citations: list[int]
    document_ids: list[UUID]
    skipped_document_ids: list[UUID]
    created_at: datetime | None = None


class CitedSource(RetrievalResult):
    """A stored source with its citation number in the answer."""

    citation: int


class SelectionOutput(BaseModel):
    text: str
    document_id: UUID | None = None
    page_number: int | None = None


class MessageOutput(BaseModel):
    message_id: UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["complete", "streaming", "failed", "stopped"]
    created_at: datetime
    mode: Literal["grounded", "open"] | None = None
    selection: SelectionOutput | None = None
    segments: list[AnswerSegmentOutput] = Field(default_factory=list)
    sources: list[CitedSource] = Field(default_factory=list)
    invalid_citations: list[int] = Field(default_factory=list)
    # The query retrieval actually ran, after follow-up rewriting.
    search_query: str | None = None
    # Sources whose documents were deleted after the answer.
    removed_source_count: int = 0
    skipped_document_ids: list[UUID] = Field(default_factory=list)
    error: str | None = None


class ConversationCreate(ScopeInput):
    title: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationSummary(BaseModel):
    conversation_id: UUID
    title: str | None
    document_ids: list[UUID] | None
    collection_id: UUID | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationDetail(ConversationSummary):
    messages: list[MessageOutput]


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]


class MessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["grounded", "open"] = "grounded"
    selection: SelectionInput | None = None
    top_k: int = Field(default=5, ge=1, le=10)
