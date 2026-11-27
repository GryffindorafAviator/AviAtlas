"""Pick a parser by file extension; Docling loads only for PDF and DOCX."""
from pathlib import Path
from threading import Lock

from aviatlas.parsing.formats import source_format
from aviatlas.parsing.models import ParsedDocument
from aviatlas.parsing.text_parser import MarkdownParser, PlainTextParser


class FormatRouter:
    def __init__(self, docling_factory):
        self._docling_factory, self._docling = docling_factory, None
        self._docling_lock = Lock()  # documents processed at once must share one Docling parser
        self._text = {"markdown": MarkdownParser(), "text": PlainTextParser()}

    def parse(self, file_path: Path) -> ParsedDocument:
        fmt = source_format(Path(file_path).name)
        if fmt in self._text:
            return self._text[fmt].parse(file_path)
        with self._docling_lock:
            if self._docling is None:
                self._docling = self._docling_factory()
        return self._docling.parse(file_path)
