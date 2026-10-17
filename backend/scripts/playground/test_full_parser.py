"""Test the full DoclingParser mapping."""
import sys
from pathlib import Path
import json

# Network config (keep your working config here)
import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
os.environ.pop("HF_ENDPOINT", None)

from aviatlas.parsing.docling_parser import DoclingParser

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/playground/test_full_parser.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: File not found at {pdf_path}")
        sys.exit(1)

    print(f"🚀 Parsing {pdf_path.name}...")
    parser = DoclingParser()
    
    try:
        parsed_doc = parser.parse(pdf_path)
        print("✅ Parsing successful!\n")
        
        # Print summary
        print(f"Title: {parsed_doc.metadata.title}")
        print(f"Pages: {parsed_doc.metadata.page_count}")
        print(f"Total Blocks: {len(parsed_doc.blocks)}")
        
        # Print first 2 blocks as JSON to verify structure
        print("\n--- First 2 Blocks Preview ---")
        for i, block in enumerate(parsed_doc.blocks[:2]):
            print(f"\n[Block {i}] Type: {block.type.value}")
            print(f"Content: {block.content[:100]}...")
            print(f"Provenance count: {len(block.provenance)}")
            if block.provenance:
                p = block.provenance[0]
                print(f"  -> Page {p.page_number}, BBox: x0={p.bbox.x0:.3f}, y0={p.bbox.y0:.3f}, x1={p.bbox.x1:.3f}, y1={p.bbox.y1:.3f}")
                if p.text_span:
                    print(f"     Text Span: [{p.text_span.start}, {p.text_span.end})")
                    
    except Exception as e:
        print(f"❌ Parsing failed: {e}")
        import traceback
        traceback.print_exc()