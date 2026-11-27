"""Process-local VectorIndex for tests and lightweight runs."""
from collections.abc import Collection, Sequence
from uuid import UUID

import numpy as np

from aviatlas.retrieval.models import VectorHit
from aviatlas.services.errors import DocumentNotReady


class InMemoryVectorIndex:
    def __init__(self):
        self._documents: dict[UUID, tuple[str, list[UUID], np.ndarray]] = {}

    def replace(self, document_id: UUID, chunk_ids: Sequence[UUID], vectors, *, model_name: str) -> None:
        vectors = np.asarray(vectors, dtype=float)
        if len(vectors) != len(chunk_ids):
            raise ValueError("Each chunk needs exactly one vector.")
        self._documents[document_id] = (model_name, list(chunk_ids), vectors)

    def delete(self, document_id: UUID) -> None:
        self._documents.pop(document_id, None)

    def contains(self, document_id: UUID) -> bool:
        return document_id in self._documents

    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID], *, model_name: str) -> None:
        entry = self._documents.get(document_id)
        if entry is None:
            raise DocumentNotReady("Document is not ready. Please parse it first.")
        if entry[0] != model_name:
            raise DocumentNotReady("Embedding model changed. Please parse this document again to rebuild its index.")
        if entry[1] != list(chunk_ids):
            raise DocumentNotReady("Document index is inconsistent. Please parse it again.")

    def search(self, vector, top_k: int, document_ids: Collection[UUID]) -> list[VectorHit]:
        query = np.asarray(vector, dtype=float)
        hits = []
        for document_id in document_ids:
            entry = self._documents.get(document_id)
            if entry is None or not len(entry[1]):
                continue
            _, chunk_ids, vectors = entry
            hits.extend(VectorHit(chunk_id, document_id, float(score))
                        for chunk_id, score in zip(chunk_ids, vectors @ query))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]
