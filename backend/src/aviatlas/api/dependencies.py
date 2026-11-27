"""Composition root: wire concrete adapters into application services."""
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from pathlib import Path
import os
from aviatlas.answering.generator import AnswerGenerator
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.services.collections import CollectionService
from aviatlas.services.conversations import ConversationService
from aviatlas.storage.conversation_repository import SQLiteConversationRepository
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.chroma_vector_index import ChromaVectorIndex
from aviatlas.storage.keyword_index import SQLiteKeywordIndex

def create_parser():
    from aviatlas.parsing.docling_parser import DoclingParser
    return DoclingParser()

@dataclass
class Services:
    documents: DocumentService
    qa: QuestionAnsweringService
    collections: CollectionService | None = None
    search: SearchService | None = None
    conversations: ConversationService | None = None

_initialization_lock = Lock()

def get_services() -> Services:
    with _initialization_lock:
        return _build_services()

@lru_cache(maxsize=1)
def _build_services() -> Services:
    data_dir = Path(os.getenv("AVIATLAS_DATA_DIR") or Path(__file__).resolve().parents[3] / "data").resolve()
    repository = SQLiteDocumentRepository(data_dir / "documents.sqlite3")
    vectors = ChromaVectorIndex(repository, data_dir / "chroma")
    embedder = SentenceTransformerEmbedder()
    keywords = SQLiteKeywordIndex(repository)
    retriever = HybridRetriever(DenseRetriever(repository, embedder, vectors), KeywordRetriever(repository, keywords))
    collections = CollectionService(SQLiteCollectionRepository(repository))
    resolver = ScopeResolver(repository, collections)
    answerer = AnswerGenerator()
    services = Services(
        DocumentService(repository, vectors, data_dir / "uploads", create_parser,
                        GreedyChunker(max_chars=1000), embedder, keywords),
        QuestionAnsweringService(repository, retriever, answerer, resolver),
        collections,
        SearchService(retriever, resolver),
        ConversationService(SQLiteConversationRepository(repository), retriever, answerer, resolver),
    )

    services.documents.repair_records()
    services.documents.recover_interrupted()
    services.documents.resume_deletions()
    services.documents.rebuild_keyword_indexes()
    services.conversations.recover_interrupted()
    return services
