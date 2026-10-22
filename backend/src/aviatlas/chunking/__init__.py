"""Chunking components for AviAtlas."""
from aviatlas.chunking.base import Chunker
from aviatlas.chunking.greedy import GreedyChunker

__all__ = ["Chunker", "GreedyChunker"]