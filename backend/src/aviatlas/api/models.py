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
    source_format: str = "pdf"
    text_layer: str | None = None
    ocr_page_count: int = 0
    # Indexed with an older chunker; processing again applies the current one.
    needs_reprocess: bool = False
    edited_chunks: int = 0
    excluded_chunks: int = 0
    # Corrections whose text was not found after reprocessing; they need review.
    pending_corrections: int = 0

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
    checks: list[dict] = Field(default_factory=list)


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
    # Local citation checks saved with the answer, and the newest verification report, if any.
    checks: list[dict] = Field(default_factory=list)
    verification: dict | None = None


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


class ContentBlock(BaseModel):
    block_id: UUID
    type: str
    content: str
    page_number: int | None = None


class DocumentContent(BaseModel):
    document_id: UUID
    source_format: str
    blocks: list[ContentBlock]


class ChunkOutput(BaseModel):
    chunk_id: UUID
    position: int
    content: str               # what retrieval uses: the edited text if any
    original: str              # generated text
    edited: bool
    excluded: bool
    correction_id: UUID | None = None
    block_ids: list[UUID]
    page_range: tuple[int, int] | None = None
    section_title: str | None = None
    kind: str = "text"
    part: int | None = None
    parts: int | None = None


class OrphanedCorrectionOutput(BaseModel):
    correction_id: UUID
    original_text: str
    text: str | None
    excluded: bool
    context: dict
    candidate_chunk_ids: list[UUID]
    updated_at: datetime


class DocumentChunks(BaseModel):
    document_id: UUID
    status: str
    chunks: list[ChunkOutput]
    orphaned: list[OrphanedCorrectionOutput]


class ChunkUpdate(BaseModel):
    """Send `text` (null restores the generated text) and/or `excluded`."""

    text: str | None = Field(default=None, max_length=20000)
    excluded: bool | None = None


class AttachCorrection(BaseModel):
    chunk_id: UUID


class LogSummary(BaseModel):
    log_id: UUID
    created_at: datetime
    kind: str
    question: str
    mode: str
    status: str
    source_count: int
    model: str | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class LogDetail(LogSummary):
    search_query: str | None = None
    scope: dict
    document_ids: list[str]
    skipped_document_ids: list[str]
    sources: list[dict]
    answer: str
    segments: list[dict]
    invalid_citations: list[int]
    conversation_id: str | None = None
    message_id: str | None = None


class LogListResponse(BaseModel):
    items: list[LogSummary]
    total: int
    limit: int
    offset: int
