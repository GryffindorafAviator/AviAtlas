"""Local script to explore Docling parsing output."""
import os
import sys
from pathlib import Path
import requests

# ==========================================
# 1. Network Configuration (Clean Proxy Mode)
# ==========================================

# Your Clash Mixed Port
PROXY_PORT = "7890" 

# Set environment variables to make Python use the proxy
os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{PROXY_PORT}"
os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{PROXY_PORT}"

# ==========================================

# 2. Load .env (at this point .env should be empty or without HF_ENDPOINT)
from dotenv import load_dotenv
load_dotenv() 

# 3. Force clear any residual mirror site configuration (double insurance)
os.environ.pop("HF_ENDPOINT", None)
os.environ.pop("HF_HUB_ENABLE_XET", None) # Let the new version of the library handle it properly

from aviatlas.parsing.docling_parser import DoclingParser

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/playground/explore_parser.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: File not found at {pdf_path}")
        sys.exit(1)

    print(f"✅ Proxy Settings: HTTP={os.environ.get('HTTP_PROXY')}")
    print(f"✅ HF Endpoint: {os.environ.get('HF_ENDPOINT', 'Official (None)')}")
    
    parser = DoclingParser()
    # We need to use DocumentConverter to convert the PDF to Docling's document format
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    print(f"\n--- 🔍 Inspecting Page Dimensions ---")
    # The document object from Docling usually contains pages information
    # Let's try to print the dimensions of the first page
    if hasattr(doc, 'pages') and len(doc.pages) > 0:
        page_1 = doc.pages[1] # Docling page numbers are usually 1-based
        print(f"Page 1 object: {page_1}")
        print(f"Page 1 dir: {dir(page_1)}") # Print all attributes to help us find width/height
        
        # Try common attribute names for page dimensions
        if hasattr(page_1, 'size'):
            print(f"Page 1 size: {page_1.size}")
        if hasattr(page_1, 'width') and hasattr(page_1, 'height'):
            print(f"Page 1 width: {page_1.width}, height: {page_1.height}")
            
    print("--- End Inspection ---\n")