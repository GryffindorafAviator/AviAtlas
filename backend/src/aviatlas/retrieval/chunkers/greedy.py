"""Greedy Chunker implementation."""

import uuid

from aviatlas.parsing.models import Block
from aviatlas.retrieval.chunkers.base import Chunker
from aviatlas.retrieval.models import RetrievalChunk


class GreedyChunker(Chunker):
    """V1 Chunker: Greedily merges Blocks until a character limit is reached.
    
    Design Philosophy:
    1. ZERO Overlap: Since we merge complete Blocks (which are semantically 
       intact), we do not need overlap to fix split sentences.
    2. Never Split a Block: If a single Block exceeds max_chars, it becomes 
       its own Chunk. This preserves the integrity of Block-level highlighting 
       and precise provenance.
    """

    def __init__(self, max_chars: int = 1000):
        self.max_chars = max_chars

    def chunk(
        self, 
        blocks: list[Block], 
        document_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
    ) -> list[RetrievalChunk]:
        
        chunks: list[RetrievalChunk] = []
        current_content = ""
        current_block_ids: list[uuid.UUID] = []

        for block in blocks:
            if not block.content.strip():
                continue

            potential_content = f"{current_content}\n{block.content}".strip() if current_content else block.content
            
            if len(potential_content) > self.max_chars and current_content:
                chunks.append(
                    RetrievalChunk(
                        id=uuid.uuid4(),
                        content=current_content,
                        block_ids=current_block_ids,
                        document_id=document_id,
                        knowledge_base_id=knowledge_base_id,
                    )
                )
                current_content = block.content
                current_block_ids = [block.id]
            else:
                current_content = potential_content
                current_block_ids.append(block.id)

        if current_content:
            chunks.append(
                RetrievalChunk(
                    id=uuid.uuid4(),
                    content=current_content,
                    block_ids=current_block_ids,
                    document_id=document_id,
                    knowledge_base_id=knowledge_base_id,
                )
            )

        return chunks