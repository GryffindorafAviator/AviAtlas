"""ChromaDB Vector Store implementation."""

import chromadb
from sentence_transformers import SentenceTransformer


class ChromaVectorStore:
    """Low-level wrapper for ChromaDB operations."""

    def __init__(
        self, 
        model_name: str = "BAAI/bge-small-en-v1.5", 
        persist_directory: str = "./chroma_db"
    ):
        print(f"⏳ Loading Embedding Model: {model_name}...")
        self._model = SentenceTransformer(model_name)
        print("✅ Embedding Model loaded.")

        self._client = chromadb.PersistentClient(path=persist_directory)
        self._collection = self._client.get_or_create_collection(
            name="aviatlas_chunks",
            metadata={"hnsw:space": "cosine"}
        )

    def add_chunks(self, ids: list[str], documents: list[str], metadatas: list[dict]):
        """Insert data into the vector store."""
        if not ids:
            return
            
        print(" Calculating embeddings for chunks (ensuring model consistency)...")
        
        embeddings = self._model.encode(documents).tolist()
        
        self._collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings, 
            metadatas=metadatas,
        )

    def search(self, query: str, top_k: int):
        """Perform semantic search and return raw results."""
        query_embedding = self._model.encode(query).tolist()
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["metadatas", "documents", "distances"]
        )

    @property
    def count(self) -> int:
        return self._collection.count()