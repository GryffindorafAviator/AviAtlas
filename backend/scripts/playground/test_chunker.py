"""Test the GreedyChunker with real parsed Blocks."""
import sys
from pathlib import Path

# Network config (keep your working config here)
import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ.pop("HF_ENDPOINT", None)

from aviatlas.parsing.docling_parser import DoclingParser
from aviatlas.retrieval.chunker import GreedyChunker

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/playground/test_chunker.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: File not found at {pdf_path}")
        sys.exit(1)

    print(f"🚀 Step 1: Parsing {pdf_path.name}...")
    parser = DoclingParser()
    parsed_doc = parser.parse(pdf_path)
    print(f"✅ Parsed successfully! Found {len(parsed_doc.blocks)} Blocks.\n")

    print("🚀 Step 2: Chunking Blocks...")
    chunker = GreedyChunker(max_chars=1000)
    chunks = chunker.chunk(
        blocks=parsed_doc.blocks,
        document_id=None, # Can be set to a real UUID in production
        knowledge_base_id=None,
    )
    print(f"✅ Chunking successful! Generated {len(chunks)} Chunks.\n")

    print("--- 📊 Chunking Statistics ---")
    for i, chunk in enumerate(chunks):
        print(f"Chunk {i+1}:")
        print(f"  - Length: {len(chunk.content)} chars")
        print(f"  - Source Blocks: {len(chunk.block_ids)}")
        print(f"  - Preview: {chunk.content[:80]}...")
        print()