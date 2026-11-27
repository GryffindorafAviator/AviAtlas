"""Docling-based parser for PDF and DOCX."""

import re
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from docling_core.types.doc import DocItemLabel

from aviatlas.parsing.models import (
    Block,
    BlockType,
    BoundingBox,
    DocumentMetadata,
    ParsedDocument,
    ProvenanceItem,
    TextSpan,
)
from aviatlas.parsing.base import DocumentParser


# A page with fewer non-whitespace characters than this has no usable text layer.
MIN_PAGE_CHARS = 32
# At least this share of pages without text: treat the PDF as scanned and OCR whole pages.
SCANNED_SHARE = 0.5


def pages_without_text(file_path: Path) -> tuple[int, list[int]]:
    """Page count and 1-based numbers of pages whose text layer is (nearly) empty."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(file_path))
    try:
        missing = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                if sum(not c.isspace() for c in textpage.get_text_range()) < MIN_PAGE_CHARS:
                    missing.append(index + 1)
            finally:
                textpage.close()
                page.close()
        return len(pdf), missing
    finally:
        pdf.close()


def compact_table(markdown: str) -> str:
    """Drop the column padding of serialized Markdown tables; it is most of their length."""
    lines = []
    for line in markdown.split("\n"):
        if re.fullmatch(r"\s*\|?[\s:|-]*-[\s:|-]*", line):  # separator row
            cells = line.strip().strip("|").split("|")
            line = "|" + "|".join("---" for _ in cells) + "|"
        else:
            line = re.sub(r" {2,}", " ", line.strip())
        lines.append(line)
    return "\n".join(lines)


def text_layer(page_count: int, missing: list[int]) -> str:
    if not missing:
        return "native"
    return "scanned" if len(missing) >= SCANNED_SHARE * page_count else "mixed"


class DoclingParser(DocumentParser):
    """Docling for PDF and DOCX. PDFs are routed by text layer:

    - native: no OCR (faster, and figures are not OCRed into noise),
    - mixed: OCR of bitmap regions, which covers scanned pages inside a text PDF,
    - scanned: full-page OCR.
    """

    def __init__(self):
        self._converters = {}
        # One conversion at a time: converters are shared and conversion is CPU-bound anyway.
        self._lock = Lock()

    def _converter(self, layer: str):
        if layer not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            options = PdfPipelineOptions()
            options.do_ocr = layer != "native"
            options.ocr_options.force_full_page_ocr = layer == "scanned"
            self._converters[layer] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        return self._converters[layer]

    def parse(self, file_path: Path) -> ParsedDocument:
        with self._lock:
            return self._parse(Path(file_path))

    def _parse(self, file_path: Path) -> ParsedDocument:
        if file_path.suffix.lower() == ".pdf":
            page_count, missing = pages_without_text(file_path)
            layer = text_layer(page_count, missing)
            doc = self._converter(layer).convert(str(file_path)).document
            extra = {"source_format": "pdf", "text_layer": layer,
                     "ocr_pages": missing if layer != "native" else []}
        else:
            doc = self._converter("native").convert(str(file_path)).document
            extra = {"source_format": "docx"}

        blocks = []
        title = None
        for item, _level in doc.iterate_items():
            block = self._create_block(item, doc, len(blocks))
            if block:
                blocks.append(block)
                # Uploaded files are stored under generated names, so the file
                # stem is never a title; use the title Docling detects, if any.
                if title is None and item.label == DocItemLabel.TITLE and block.content:
                    title = " ".join(block.content.split())[:300]

        metadata = DocumentMetadata(title=title, authors=[], page_count=len(doc.pages) or None,
                                    language=None, **extra)
        return ParsedDocument(metadata=metadata, blocks=blocks)

    def _create_block(self, item: Any, doc: Any, order: int) -> Block | None:
        """Convert a single Docling item into an AviAtlas Block."""
        
        block_type = self._map_label_to_block_type(item.label)
        if block_type is None:
            return None

        content = getattr(item, "text", "") or getattr(item, "get_text", lambda: "")()
        if not content and block_type == BlockType.EQUATION:
            # Without formula enrichment Docling leaves `text` empty; `orig` keeps the PDF's text layer.
            content = " ".join((getattr(item, "orig", "") or "").split())
        if not content and block_type == BlockType.TABLE:
            content = item.export_to_markdown(doc=doc)
            # The serializer includes captions, which Docling also yields as caption items;
            # keep them out of the table so the chunker attaches each caption once.
            caption = " ".join((item.caption_text(doc) or "").split()) if hasattr(item, "caption_text") else ""
            if caption:
                content = "\n\n".join(part for part in content.split("\n\n") if " ".join(part.split()) != caption)
            content = compact_table(content)

        provenance = []
        if hasattr(item, "prov") and item.prov:
            for p in item.prov:
                page_no = p.page_no
                if page_no not in doc.pages:
                    continue
                
                page = doc.pages[page_no]
                page_width = page.size.width
                page_height = page.size.height

                l, t, r, b = p.bbox.l, p.bbox.t, p.bbox.r, p.bbox.b
                
                if page_width == 0 or page_height == 0:
                    continue

                x0 = l / page_width
                y0 = (page_height - t) / page_height
                x1 = r / page_width
                y1 = (page_height - b) / page_height

                bbox = BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)

                text_span = None
                # FIX: Only create TextSpan if start is strictly less than end
                if hasattr(p, "charspan") and p.charspan and p.charspan[0] < p.charspan[1]:
                    text_span = TextSpan(start=p.charspan[0], end=p.charspan[1])

                provenance.append(ProvenanceItem(
                    page_number=page_no,
                    bbox=bbox,
                    text_span=text_span
                ))

        return Block(
            id=uuid.uuid4(),
            type=block_type,
            content=content.strip(),
            order=order,
            provenance=provenance
        )

    def _map_label_to_block_type(self, label: DocItemLabel) -> BlockType | None:
        """Map Docling's label to AviAtlas's BlockType enum."""
        mapping = {
            DocItemLabel.TITLE: BlockType.HEADING,
            DocItemLabel.SECTION_HEADER: BlockType.HEADING,
            DocItemLabel.TEXT: BlockType.PARAGRAPH,
            DocItemLabel.LIST_ITEM: BlockType.LIST_ITEM,
            DocItemLabel.TABLE: BlockType.TABLE,
            DocItemLabel.PICTURE: BlockType.PICTURE,
            DocItemLabel.CAPTION: BlockType.CAPTION,
            DocItemLabel.FORMULA: BlockType.EQUATION,
            DocItemLabel.CODE: BlockType.CODE,
        }
        return mapping.get(label)