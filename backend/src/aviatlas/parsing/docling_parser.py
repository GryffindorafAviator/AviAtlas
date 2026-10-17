"""Docling-based parser for AviAtlas."""

import uuid
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter
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
from aviatlas.parsing.parser import DocumentParser


class DoclingParser(DocumentParser):
    """Parser that uses Docling to convert PDFs into AviAtlas's document model."""

    def __init__(self):
        self._converter = DocumentConverter()

    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse a PDF file and return an AviAtlas ParsedDocument."""
        result = self._converter.convert(str(file_path))
        doc = result.document

        # 1. Extract Metadata (Safely)
        metadata = DocumentMetadata(
            title=file_path.stem,
            authors=[],
            page_count=len(doc.pages),
            language=None,
        )

        # 2. Extract Blocks
        blocks = []
        order_counter = 0

        for item in doc.texts:
            block = self._create_block(item, doc, order_counter)
            if block:
                blocks.append(block)
                order_counter += 1

        for item in doc.tables:
            block = self._create_block(item, doc, order_counter)
            if block:
                blocks.append(block)
                order_counter += 1

        for item in doc.pictures:
            block = self._create_block(item, doc, order_counter)
            if block:
                blocks.append(block)
                order_counter += 1

        # Sort blocks by order to ensure correct reading sequence
        blocks.sort(key=lambda b: b.order)

        return ParsedDocument(metadata=metadata, blocks=blocks)

    def _create_block(self, item: Any, doc: Any, order: int) -> Block | None:
        """Convert a single Docling item into an AviAtlas Block."""
        
        block_type = self._map_label_to_block_type(item.label)
        if block_type is None:
            return None

        content = getattr(item, "text", "") or getattr(item, "get_text", lambda: "")()
        if not content and block_type == BlockType.TABLE:
            content = "[Table]" 

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