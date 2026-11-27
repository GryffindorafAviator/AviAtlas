"""Supported upload formats and content checks."""
from __future__ import annotations
import zipfile
from pathlib import Path

# suffix -> source format
FORMATS = {".pdf": "pdf", ".docx": "docx", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "markdown": "text/markdown; charset=utf-8",
    "text": "text/plain; charset=utf-8",
}
TEXT_ENCODINGS = ("utf-8-sig", "gb18030")


class UnsupportedFormat(ValueError):
    pass


def source_format(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in FORMATS:
        raise UnsupportedFormat("Supported formats: PDF, DOCX, Markdown (.md), and plain text (.txt).")
    return FORMATS[suffix]


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), then GB18030, which also covers GBK and GB2312."""
    if b"\x00" in data:
        raise UnsupportedFormat("The file is not a text file.")
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedFormat("Text files must be UTF-8 or GB18030/GBK encoded.")


def check_content(path: Path, fmt: str) -> None:
    """Reject files whose content does not match their extension."""
    if fmt == "pdf":
        with path.open("rb") as handle:
            if b"%PDF-" not in handle.read(1024):
                raise UnsupportedFormat("The file is not a valid PDF.")
    elif fmt == "docx":
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise UnsupportedFormat("The file is not a valid DOCX document.")
        except zipfile.BadZipFile as exc:
            raise UnsupportedFormat("The file is not a valid DOCX document. Legacy .doc files are not supported.") from exc
    else:
        decode_text(path.read_bytes())
