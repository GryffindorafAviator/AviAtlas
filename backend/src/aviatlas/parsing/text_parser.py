"""Markdown and plain-text parsers.

Block content keeps the source text verbatim (LaTeX, code, table pipes), so the
chunker, BM25, and the model see what the author wrote. Docling's Markdown
backend is not used because it splits `$$` blocks and drops LaTeX escapes.
"""
from __future__ import annotations
import re
import uuid
from pathlib import Path

from aviatlas.parsing.formats import decode_text
from aviatlas.parsing.models import Block, BlockType, DocumentMetadata, ParsedDocument

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_LIST = re.compile(r"^\s{0,3}([-*+]|\d{1,9}[.)])\s+")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _blocks(items: list[tuple[BlockType, str]]) -> list[Block]:
    kept = [(kind, text.strip("\n")) for kind, text in items if text.strip()]
    return [Block(id=uuid.uuid4(), type=kind, content=text, order=i) for i, (kind, text) in enumerate(kept)]


def parse_markdown(text: str) -> ParsedDocument:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    items: list[tuple[BlockType, str]] = []
    paragraph: list[str] = []
    title = None

    def end_paragraph():
        if paragraph:
            items.append((BlockType.PARAGRAPH, "\n".join(paragraph)))
            paragraph.clear()

    i = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        end = next((j for j in range(1, len(lines)) if lines[j].strip() in ("---", "...")), None)
        if end is not None:
            i = end + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if fence := _FENCE.match(line):
            end_paragraph()
            marker, body, i = fence.group(1), [], i + 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            items.append((BlockType.CODE, "\n".join(body)))
            i += 1
            continue
        if stripped.startswith("$$"):
            end_paragraph()
            body = [line]
            if not (len(stripped) > 2 and stripped.endswith("$$") and stripped != "$$"):
                i += 1
                while i < len(lines) and "$$" not in lines[i]:
                    body.append(lines[i])
                    i += 1
                if i < len(lines):
                    body.append(lines[i])
            items.append((BlockType.EQUATION, "\n".join(body)))
            i += 1
            continue
        if heading := _HEADING.match(line):
            end_paragraph()
            items.append((BlockType.HEADING, heading.group(2)))
            if title is None and len(heading.group(1)) == 1:
                title = heading.group(2)
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1]):
            end_paragraph()
            rows = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(lines[i])
                i += 1
            items.append((BlockType.TABLE, "\n".join(rows)))
            continue
        if not stripped or _RULE.match(line):
            end_paragraph()
            i += 1
            continue
        if _LIST.match(line):
            end_paragraph()
            item = [line]
            i += 1
            # Indented continuation lines belong to the item.
            while i < len(lines) and lines[i].strip() and lines[i].startswith((" ", "\t")) and not _LIST.match(lines[i]):
                item.append(lines[i])
                i += 1
            items.append((BlockType.LIST_ITEM, "\n".join(item)))
            continue
        paragraph.append(line)
        i += 1
    end_paragraph()
    return ParsedDocument(metadata=DocumentMetadata(title=title, source_format="markdown"), blocks=_blocks(items))


def parse_text(text: str) -> ParsedDocument:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", text)
    # Without blank lines, one line is one paragraph (common for exported or Chinese text).
    if len(parts) == 1:
        parts = text.split("\n")
    return ParsedDocument(metadata=DocumentMetadata(source_format="text"),
                          blocks=_blocks([(BlockType.PARAGRAPH, p) for p in parts]))


class MarkdownParser:
    def parse(self, file_path: Path) -> ParsedDocument:
        return parse_markdown(decode_text(Path(file_path).read_bytes()))


class PlainTextParser:
    def parse(self, file_path: Path) -> ParsedDocument:
        return parse_text(decode_text(Path(file_path).read_bytes()))
