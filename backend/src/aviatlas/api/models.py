"""Pydantic models for API request and response schemas."""

from datetime import datetime
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