"""Small contracts for interchangeable workflow dependencies."""
from collections.abc import Collection, Iterator, Sequence
from typing import Protocol
from uuid import UUID
from aviatlas.parsing.models import Block, ParsedDocument
from aviatlas.retrieval.models import RetrievalChunk, RetrievalResult, RetrievalScope, VectorHit
from aviatlas.storage.document_registry import DocumentRecord
from pathlib import Path

class DocumentRepository(Protocol):
    def all_records(self) -> list[DocumentRecord]: ...
    def delete(self, doc_id: UUID) -> None: ...
    def save(self, record: DocumentRecord) -> None: ...
    def register(self, filename: str, filepath: Path | str | None = None) -> DocumentRecord: ...
    def get(self, doc_id: UUID | str) -> DocumentRecord | None: ...

class Parser(Protocol):
    def parse(self, file_path: Path) -> ParsedDocument: ...

class Chunker(Protocol):
    def chunk(self, blocks: list[Block], document_id: UUID | None = None,
              knowledge_base_id: UUID | None = None) -> list[RetrievalChunk]: ...

class Embedder(Protocol):
    """Turns text into L2-normalized vectors; `model_name` identifies the vector space."""
    model_name: str
    def encode(self, texts: Sequence[str]): ...  # -> 2-D array, one row per text

class VectorIndex(Protocol):
    """Backend-neutral storage for chunk vectors (Chroma today, Milvus later).

    Vectors are derived data: they are written per document, replaced
    wholesale on reprocessing, and validated against authoritative chunk records.
    """
    def replace(self, document_id: UUID, chunk_ids: Sequence[UUID], vectors, *, model_name: str) -> None: ...
    def delete(self, document_id: UUID) -> None: ...
    def contains(self, document_id: UUID) -> bool: ...
    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID], *, model_name: str) -> None:
        """Raise DocumentNotReady unless stored vectors match these chunks and model."""
    def search(self, vector, top_k: int, document_ids: Collection[UUID]) -> list[VectorHit]:
        """Best hits across the given documents, highest score first."""

class KeywordIndex(Protocol):
    """Backend-neutral term index for BM25 over chunks, rebuilt from chunk records."""
    tokenizer_version: str
    def replace(self, document_id: UUID, chunks: Sequence[tuple[UUID, list[str]]]) -> None:
        """Store (chunk_id, tokens) pairs for one document, replacing earlier entries."""
    def delete(self, document_id: UUID) -> None: ...
    def contains(self, document_id: UUID) -> bool: ...
    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID]) -> None:
        """Raise DocumentNotReady unless stored entries match these chunks and tokenizer."""
    def search(self, terms: Sequence[str], top_k: int, document_ids: Collection[UUID]) -> list[VectorHit]:
        """BM25 hits across the given documents, highest score first."""

class Retriever(Protocol):
    def search(self, query: str, scope: RetrievalScope, top_k: int = 3) -> list[RetrievalResult]: ...

class Answerer(Protocol):
    def generate(self, question: str, sources: list[RetrievalResult], **options) -> str: ...

class ConversationalAnswerer(Answerer, Protocol):
    """Adds streaming and follow-up rewriting. `history` items are {"role", "content"}."""
    def stream(self, question: str, sources: list[RetrievalResult], **options) -> Iterator[str]: ...
    def condense(self, question: str, history: Sequence[dict]) -> str: ...
