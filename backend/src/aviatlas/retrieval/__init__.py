"""Retrieval components for AviAtlas.

The project contains both lightweight utilities and optional heavyweight runtime
extras (Docling, Chroma, sentence-transformers). Importing the package should not
fail just because those optional dependencies are not installed yet.
"""

from __future__ import annotations

from aviatlas.retrieval.base import Retriever
from aviatlas.retrieval.models import RetrievalResult

__all__ = [
    "Retriever",
    "RetrievalResult",
    "GreedyChunker",
    "InMemoryRetriever",
    "ChromaRetriever",
]


def __getattr__(name: str):
    if name == "GreedyChunker":
        from aviatlas.chunking.greedy_chunker import GreedyChunker
        return GreedyChunker
    if name == "InMemoryRetriever":
        from aviatlas.retrieval.memory_retriever import InMemoryRetriever
        return InMemoryRetriever
    if name == "ChromaRetriever":
        from aviatlas.retrieval.chroma_retriever import ChromaRetriever
        return ChromaRetriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")