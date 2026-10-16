"""Docling-based parser for AviAtlas."""

from pathlib import Path

from docling.document_converter import DocumentConverter

from aviatlas.parsing.models import ParsedDocument
from aviatlas.parsing.parser import DocumentParser


class DoclingParser(DocumentParser):
    """Parser that uses Docling to convert PDFs into AviAtlas's document model."""

    def __init__(self):
        # Initialize the Docling converter. 
        # We can configure it later (e.g., specific OCR, table models) if needed.
        self._converter = DocumentConverter()

    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse a PDF file and return an AviAtlas ParsedDocument."""
        result = self._converter.convert(str(file_path))
        doc = result.document

        # --- Exploration / Debugging ---
        # Before writing the full mapping, let's inspect Docling's raw output.
        # We want to see the structure of `prov` and the format of `bbox`.
        print(f"\n--- Inspecting Docling output for: {file_path.name} ---")
        print(f"Total text items found: {len(doc.texts)}")

        # Inspect the first 3 text items
        for i, item in enumerate(doc.texts[:3]):
            print(f"\n[Item {i}] Label: {item.label}")
            print(f"  Text preview: '{item.text[:60]}...'")
            
            if item.prov:
                print(f"  Provenance entries: {len(item.prov)}")
                for p in item.prov:
                    # Docling's bbox usually has attributes like l, t, r, b (left, top, right, bottom)
                    # or x0, y0, x1, y1. Let's print the whole object to see.
                    print(f"    -> page_no: {p.page_no}, bbox: {p.bbox}")
            else:
                print("  Provenance: None")
        
        print("--- End Inspection ---\n")

        # TODO: Step 3 - Implement the actual mapping from DoclingDocument to ParsedDocument
        raise NotImplementedError("Full mapping to ParsedDocument is not implemented yet.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m aviatlas.parsing.docling_parser <path_to_pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"Error: File not found at {pdf_path}")
        sys.exit(1)

    parser = DoclingParser()
    try:
        parser.parse(pdf_path)
    except Exception as e:
        print(f"An error occurred: {e}")