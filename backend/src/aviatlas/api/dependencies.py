"""Composition root: wire concrete adapters into application services."""
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from pathlib import Path
import os
from aviatlas.answering.generator import AnswerGenerator
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.retrieval.semantic_retriever import SemanticRetriever
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService
from aviatlas.services.collections import CollectionService
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.persistent_indexes import PersistentIndexStore

def create_parser():
    from aviatlas.parsing.docling_parser import DoclingParser
    return DoclingParser()

@dataclass
class Services:
    documents: DocumentService
    qa: QuestionAnsweringService
    collections: CollectionService | None = None

_initialization_lock = Lock()

def get_services() -> Services:
    with _initialization_lock:
        return _build_services()

@lru_cache(maxsize=1)
def _build_services() -> Services:
    data_dir = Path(os.getenv("AVIATLAS_DATA_DIR") or Path(__file__).resolve().parents[3] / "data").resolve()
    repository = SQLiteDocumentRepository(data_dir / "documents.sqlite3")
    indexes = PersistentIndexStore(repository, data_dir / "chroma")
    indexes.recover_interrupted()
    services = Services(
        DocumentService(repository, indexes, data_dir / "uploads", create_parser,
                        GreedyChunker(max_chars=1000), SemanticRetriever),
        QuestionAnsweringService(repository, indexes, AnswerGenerator()),
        CollectionService(SQLiteCollectionRepository(repository)),
    )

    services.documents.resume_deletions()
    return services
