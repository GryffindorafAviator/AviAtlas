"""ChromaDB-based Retriever implementation."""

import uuid
from aviatlas.parsing.models import Block
from aviatlas.retrieval.models import RetrievalChunk, SearchResult
from aviatlas.retrieval.retrievers.base import Retriever
from aviatlas.storage.chroma_store import ChromaVectorStore # 依赖 Storage 层

class ChromaRetriever(Retriever):
    def __init__(self, chunks: list[RetrievalChunk], blocks: list[Block], persist_directory: str = "./chroma_db"):
        self._block_map = {block.id: block for block in blocks}
        
        # Initialize the ChromaDB store
        self._store = ChromaVectorStore(persist_directory=persist_directory)
        
        # Ingest data on first run if the collection is empty
        if self._store._collection.count() == 0 and chunks:
            self._ingest(chunks)

    def _ingest(self, chunks: list[RetrievalChunk]):
        ids = [str(c.id) for c in chunks]
        docs = [c.content for c in chunks]
        # Convert block_ids to a string and store in metadata
        metas = [{"block_ids_str": ",".join([str(bid) for bid in c.block_ids])} for c in chunks]
        self._store.add_chunks(ids, docs, metas)

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        # 1. Call the underlying store to get raw results
        raw_results = self._store.search(query, top_k)
        
        # 2. Assemble business results (The Magic)
        enriched_results = []
        for res_id, doc, distance, metadata in zip(
            raw_results['ids'][0], raw_results['documents'][0], 
            raw_results['distances'][0], raw_results['metadatas'][0]
        ):
            # Restore block_ids
            block_ids = [uuid.UUID(bid) for bid in metadata.get("block_ids_str", "").split(",") if bid]
            
            # Retrieve Provenance
            provenance_items = []
            for block_id in block_ids:
                if block_id in self._block_map:
                    provenance_items.extend(self._block_map[block_id].provenance)

            enriched_results.append(SearchResult(
                chunk_id=uuid.UUID(res_id),
                content=doc,
                score=1.0 - distance,
                provenance=provenance_items,
            ))
        return enriched_results