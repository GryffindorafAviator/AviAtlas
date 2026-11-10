"""Versioned, document-scoped Chroma indexes with a durable SQLite manifest."""
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from aviatlas.retrieval.models import RetrievalResult
from aviatlas.retrieval.semantic_retriever import SemanticRetriever, embedding_model_name, get_encoder
from aviatlas.services.errors import DocumentNotReady
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


def chunk_digest(chunks) -> str:
    return hashlib.sha256("|".join(str(c.id) for c in chunks).encode()).hexdigest()


class PersistentSearchIndex:
    def __init__(self, collection, record, model_name, encoder_factory):
        self.collection = collection
        self.record = record
        self.model_name = model_name
        self.encoder_factory = encoder_factory
        self.chunks = {str(chunk.id): chunk for chunk in record.chunks}
        self.blocks = {block.id: block for block in record.blocks}

    def search(self, query: str, top_k: int = 3) -> list[RetrievalResult]:
        vector = self.encoder_factory(self.model_name).encode([query], normalize_embeddings=True)
        raw = self.collection.query(query_embeddings=vector.tolist(),
                                    n_results=min(top_k, len(self.chunks)), include=["distances"])
        results = []
        for chunk_id, distance in zip(raw["ids"][0], raw["distances"][0]):
            chunk = self.chunks[chunk_id]
            provenance = [p for bid in chunk.block_ids if bid in self.blocks
                          for p in self.blocks[bid].provenance]
            pages = [p.page_number for p in provenance]
            results.append(RetrievalResult(
                query=query, document_id=self.record.id, chunk_id=chunk.id,
                content=chunk.resolved_text, score=1.0 - distance, provenance=provenance,
                page_range=(min(pages), max(pages)) if pages else None,
            ))
        return results


class PersistentIndexStore:
    def __init__(self, repository: SQLiteDocumentRepository, directory: Path,
                 model_name: str | None = None, encoder_factory=get_encoder):
        self.repository = repository
        self.directory = Path(directory).resolve()
        self.model_name = model_name or embedding_model_name()
        self.encoder_factory = encoder_factory
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

    def _manifest(self, document_id):
        with self.repository.connect() as connection:
            return connection.execute(
                "SELECT collection_name, model FROM index_manifest WHERE document_id=?",
                (str(document_id),),
            ).fetchone()

    def get(self, document_id: UUID):
        row = self._manifest(document_id)
        if row is None:
            return None
        name, model = row
        if model != self.model_name:
            raise DocumentNotReady("Embedding model changed. Please parse this document again to rebuild its index.")
        record = self.repository.get(document_id)
        if record is None or not record.chunks:
            return None
        from chromadb.errors import NotFoundError
        try:
            collection = self.client.get_collection(name, embedding_function=None)
        except NotFoundError:
            raise DocumentNotReady("Document index is missing. Please parse it again.") from None
        metadata = collection.metadata or {}
        if (metadata.get("document_id") != str(document_id)
                or metadata.get("embedding_model") != model
                or metadata.get("chunk_digest") != chunk_digest(record.chunks)
                or collection.count() != len(record.chunks)):
            raise DocumentNotReady("Document index is inconsistent. Please parse it again.")
        return PersistentSearchIndex(collection, record, model, self.encoder_factory)

    def save(self, document_id: UUID, index: SemanticRetriever) -> None:
        if not isinstance(index, SemanticRetriever) or index.embeddings is None:
            raise ValueError("Persistent indexes require a built semantic index.")
        if index.embedding_model != self.model_name:
            raise ValueError("Embedding model changed while building the index.")
        if any(chunk.document_id != document_id for chunk in index.chunks):
            raise ValueError("Index contains chunks from another document.")
        self.delete(document_id)
        name = f"doc-{document_id.hex}-{uuid4().hex}"
        collection = self.client.create_collection(name, embedding_function=None, metadata={
            "hnsw:space": "cosine", "document_id": str(document_id),
            "embedding_model": self.model_name, "chunk_digest": chunk_digest(index.chunks),
        })
        try:
            size = self.client.get_max_batch_size()
            for start in range(0, len(index.chunks), size):
                batch = index.chunks[start:start + size]
                collection.add(ids=[str(c.id) for c in batch],
                               embeddings=index.embeddings[start:start + size].tolist())
            with self.repository.connect() as connection:
                connection.execute("INSERT INTO index_manifest VALUES (?, ?, ?)",
                                   (str(document_id), name, self.model_name))
        except Exception:
            self.client.delete_collection(name)
            raise

    def delete(self, document_id: UUID) -> None:
        row = self._manifest(document_id)
        if row:
            from chromadb.errors import NotFoundError
            try:
                self.client.delete_collection(row[0])
            except NotFoundError:
                pass
        with self.repository.connect() as connection:
            connection.execute("DELETE FROM index_manifest WHERE document_id=?", (str(document_id),))

    def recover_interrupted(self):
        # One server process only: any in-flight records belong to the previous run.
        for record in self.repository.all_records():
            if record.status in {"queued", "parsing", "indexing"} or (
                    record.status == "uploaded" and record.filepath is None):
                self.delete(record.id)
                record.set_status("failed")
                record.update_metadata(last_error="Processing was interrupted. Upload or parse again.")
                self.repository.save(record)
