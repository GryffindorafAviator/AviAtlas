"""End-to-end test of the Retrieval and Provenance mapping pipeline."""
import sys
from pathlib import Path

# Network config
import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ.pop("HF_ENDPOINT", None)

from aviatlas.parsing.docling_parser import DoclingParser
from aviatlas.retrieval.chunkers import GreedyChunker
from aviatlas.retrieval.retrievers import InMemoryRetriever
from aviatlas.retrieval.retrievers import ChromaRetriever

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/playground/test_retriever.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: File not found at {pdf_path}")
        sys.exit(1)

    print(f"🚀 Step 1: Parsing {pdf_path.name}...")
    parser = DoclingParser()
    parsed_doc = parser.parse(pdf_path)
    print(f"✅ Parsed! Found {len(parsed_doc.blocks)} Blocks.\n")

    print("🚀 Step 2: Chunking Blocks...")
    chunker = GreedyChunker(max_chars=1000)
    chunks = chunker.chunk(blocks=parsed_doc.blocks)
    print(f"✅ Chunked! Generated {len(chunks)} Chunks.\n")

    print("🚀 Step 3: Initializing In-Memory Retriever...")
    # retriever = InMemoryRetriever(chunks=chunks, blocks=parsed_doc.blocks)
    retriever = ChromaRetriever(
        chunks=chunks, 
        blocks=parsed_doc.blocks,
        persist_directory="./test_chroma_db"
    )
    print("✅ Retriever ready.\n")

    # --- Test Search Queries ---
    test_queries = [
        "What is self-attention?",
        "encoder decoder architecture",
        "positional encoding"
    ]

    for query in test_queries:
        print(f"🔍 Searching for: '{query}'")
        print("-" * 50)
        
        results = retriever.search(query, top_k=2)
        
        if not results:
            print("  No results found.\n")
            continue

        for i, res in enumerate(results):
            print(f"  [Result {i+1}] Score: {res.score:.4f}")
            print(f"  Content Preview: {res.content[:100]}...")
            
            print(f"  📍 Provenance (Source Locations):")
            if not res.provenance:
                print("     (No provenance data available)")
            else:
                for p in res.provenance:
                    bbox = p.bbox
                    print(f"     -> Page {p.page_number}: "
                          f"BBox(x0={bbox.x0:.3f}, y0={bbox.y0:.3f}, x1={bbox.x1:.3f}, y1={bbox.y1:.3f})")
            print()
        
        print("=" * 50 + "\n")