"""Unit tests for the GreedyChunker."""

import uuid
import pytest

from aviatlas.parsing.models import Block, BlockType
from aviatlas.retrieval.chunkers import GreedyChunker

def _make_block(content: str, block_type: BlockType = BlockType.PARAGRAPH) -> Block:
    """Helper to create a mock Block."""
    return Block(
        id=uuid.uuid4(),
        type=block_type,
        content=content,
        order=0,
        provenance=[]
    )


def test_chunker_merges_small_blocks():
    """Multiple small blocks should be merged into one chunk if under limit."""
    chunker = GreedyChunker(max_chars=100)
    
    # 3 blocks, each 20 chars. Total ~60 chars. Should merge.
    blocks = [
        _make_block("A" * 20),
        _make_block("B" * 20),
        _make_block("C" * 20),
    ]
    
    chunks = chunker.chunk(blocks)
    
    assert len(chunks) == 1
    assert len(chunks[0].block_ids) == 3
    assert "A" in chunks[0].content
    assert "C" in chunks[0].content


def test_chunker_splits_when_limit_reached():
    """Chunks should split when the character limit is exceeded."""
    chunker = GreedyChunker(max_chars=50)
    
    # Block 1: 30 chars. Block 2: 30 chars. 
    # Merging them would be ~60 chars > 50. Should split.
    blocks = [
        _make_block("A" * 30),
        _make_block("B" * 30),
    ]
    
    chunks = chunker.chunk(blocks)
    
    assert len(chunks) == 2
    assert len(chunks[0].block_ids) == 1
    assert len(chunks[1].block_ids) == 1


def test_chunker_never_splits_a_single_large_block():
    """A single block larger than max_chars should become its own chunk."""
    chunker = GreedyChunker(max_chars=50)
    
    # One huge block: 100 chars.
    blocks = [
        _make_block("X" * 100),
    ]
    
    chunks = chunker.chunk(blocks)
    
    assert len(chunks) == 1
    assert len(chunks[0].content) == 100
    assert chunks[0].block_ids[0] == blocks[0].id


def test_chunker_skips_empty_blocks():
    """Empty blocks should be ignored."""
    chunker = GreedyChunker(max_chars=100)
    
    blocks = [
        _make_block("Valid content"),
        _make_block("   "), # Empty/Whitespace
        _make_block("More content"),
    ]
    
    chunks = chunker.chunk(blocks)
    
    assert len(chunks) == 1
    assert len(chunks[0].block_ids) == 2 # Only the 2 valid blocks