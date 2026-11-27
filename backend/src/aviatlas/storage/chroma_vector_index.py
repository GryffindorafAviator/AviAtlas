"""Chroma-backed VectorIndex with a durable SQLite manifest.

Each indexing run writes a fresh, uniquely named collection per document and
only then records it in the manifest, so a failed or interrupted run never
exposes partial vectors. Scoped searches fan out over the documents' collections
and merge by cosine similarity, which is comparable because one embedding model
is enforced per index. A backend with metadata filtering (e.g. Milvus) can serve
the same contract from a single collection.
"""
from collections.abc import Collection, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from aviatlas.retrieval.models import VectorHit
from aviatlas.services.errors import DocumentNotReady
from aviatlas.storage.digest import chunk_digest
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


class ChromaVectorIndex:
    def __init__(self, repository: SQLiteDocumentRepository, directory: Path):
        self.repository = repository
        self.directory = Path(directory).resolve()
        self._client = None
        with self.repository.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS index_manifest "
                               "(document_id TEXT PRIMARY KEY, collection_name TEXT NOT NULL, model TEXT NOT NULL)")

    @property
    def client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(
                path=str(self.directory), settings=Settings(anonymized_telemetry=False))
        return self._client

    def _manifest(self, document_id: UUID):
        with self.repository.connect() as connection:
            return connection.execute(
                "SELECT collection_name, model FROM index_manifest WHERE document_id=?",
                (str(document_id),),
            ).fetchone()

    def _collection(self, name: str):
        from chromadb.errors import NotFoundError
        try:
            return self.client.get_collection(name, embedding_function=None)
        except NotFoundError:
            raise DocumentNotReady("Document index is missing. Please parse it again.") from None

    def contains(self, document_id: UUID) -> bool:
        return self._manifest(document_id) is not None

    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID], *, model_name: str) -> None:
        row = self._manifest(document_id)
        if row is None:
            raise DocumentNotReady("Document is not ready. Please parse it first.")
        name, model = row
        if model != model_name:
            raise DocumentNotReady("Embedding model changed. Please parse this document again to rebuild its index.")
        collection = self._collection(name)
        metadata = collection.metadata or {}
        if (metadata.get("document_id") != str(document_id)
                or metadata.get("embedding_model") != model
                or metadata.get("chunk_digest") != chunk_digest(chunk_ids)
                or collection.count() != len(chunk_ids)):
            raise DocumentNotReady("Document index is inconsistent. Please parse it again.")

    def replace(self, document_id: UUID, chunk_ids: Sequence[UUID], vectors, *, model_name: str) -> None:
        """Write a new collection, then switch the manifest to it; the old index serves until the switch
        and stays intact if writing fails."""
        if len(vectors) != len(chunk_ids):
            raise ValueError("Each chunk needs exactly one vector.")
        previous = self._manifest(document_id)
        name = f"doc-{document_id.hex}-{uuid4().hex}"
        collection = self.client.create_collection(name, embedding_function=None, metadata={
            "hnsw:space": "cosine", "document_id": str(document_id),
            "embedding_model": model_name, "chunk_digest": chunk_digest(chunk_ids),
        })
        try:
            size = self.client.get_max_batch_size()
            for start in range(0, len(chunk_ids), size):
                collection.add(ids=[str(c) for c in chunk_ids[start:start + size]],
                               embeddings=[list(map(float, v)) for v in vectors[start:start + size]])
            with self.repository.connect() as connection:
                connection.execute("INSERT OR REPLACE INTO index_manifest VALUES (?, ?, ?)",
                                   (str(document_id), name, model_name))
        except Exception:
            self.client.delete_collection(name)
            raise
        if previous:
            self._drop(previous[0])

    def _drop(self, name: str) -> None:
        from chromadb.errors import NotFoundError
        try:
            self.client.delete_collection(name)
        except NotFoundError:
            pass

    def vectors(self, document_id: UUID, chunk_ids: Sequence[UUID]) -> dict[UUID, list[float]]:
        """Stored vectors for the given chunks (missing ones are left out)."""
        row = self._manifest(document_id)
        if row is None or not chunk_ids:
            return {}
        raw = self._collection(row[0]).get(ids=[str(c) for c in chunk_ids], include=["embeddings"])
        return {UUID(i): list(map(float, v)) for i, v in zip(raw["ids"], raw["embeddings"])}

    def collect_garbage(self) -> int:
        """Delete collections no manifest row points to (left by an interrupted rebuild)."""
        with self.repository.connect() as connection:
            live = {row[0] for row in connection.execute("SELECT collection_name FROM index_manifest")}
        stale = [c.name if hasattr(c, "name") else c for c in self.client.list_collections()]
        stale = [n for n in stale if n.startswith("doc-") and n not in live]
        for name in stale:
            self._drop(name)
        return len(stale)

    def delete(self, document_id: UUID) -> None:
        row = self._manifest(document_id)
        if row:
            self._drop(row[0])
        with self.repository.connect() as connection:
            connection.execute("DELETE FROM index_manifest WHERE document_id=?", (str(document_id),))

    def search(self, vector, top_k: int, document_ids: Collection[UUID]) -> list[VectorHit]:
        query = [float(x) for x in vector]
        hits = []
        for document_id in document_ids:
            row = self._manifest(document_id)
            if row is None:
                continue
            collection = self._collection(row[0])
            n_results = min(top_k, collection.count())
            if n_results == 0:
                continue
            raw = collection.query(query_embeddings=[query], n_results=n_results, include=["distances"])
            hits.extend(VectorHit(UUID(chunk_id), document_id, 1.0 - distance)
                        for chunk_id, distance in zip(raw["ids"][0], raw["distances"][0]))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]
