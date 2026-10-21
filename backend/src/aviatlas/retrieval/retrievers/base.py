"""Base interface for Retrievers."""

from abc import ABC, abstractmethod

from aviatlas.retrieval.models import SearchResult


class Retriever(ABC):
    """Interface for retrieving chunks and enriching them with provenance."""

    @abstractmethod
    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        raise NotImplementedError