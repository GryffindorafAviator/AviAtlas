"""Pydantic models for API request and response schemas."""

from uuid import UUID
from pydantic import BaseModel


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