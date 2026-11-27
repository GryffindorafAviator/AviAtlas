"""Structure-aware chunking: protects tables, formulas, and code.

Rules, in order of precedence:
- A table is its own chunk, with its caption and any headings directly above it.
  A table longer than `max_chars * slack` is split by rows; every part repeats
  the caption and the header row.
- Equations and code are never split and stay with the preceding text; they may
  push a chunk up to `max_chars * slack`.
- A heading starts a new chunk once the current one has `min_chars`, and a
  heading never ends a chunk.
- A paragraph longer than `max_chars` is split at sentence boundaries.
- Other blocks are packed greedily up to `max_chars`.
"""
from __future__ import annotations
import re
import uuid

from aviatlas.chunking.base import Chunker
from aviatlas.parsing.models import Block, BlockType
from aviatlas.retrieval.models import RetrievalChunk

CHUNKER_VERSION = "structured-v1"
_SENTENCE_END = re.compile(r"(?<=[。！？；!?])\s*|(?<=[.;])\s+")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")
_ATOMIC = {BlockType.EQUATION, BlockType.CODE}

Part = tuple[Block, str, str | None]  # block, text (may be a piece of the block), section


def split_sentences(text: str, limit: int) -> list[str]:
    """Pack sentences into pieces of at most `limit` characters; a longer sentence is cut hard."""
    pieces, current = [], ""

    def join(a: str, b: str) -> str:
        # The split drops the space after Western punctuation; CJK text has none.
        return a + b if not a or a.endswith(("。", "！", "？", "；")) else f"{a} {b}"

    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if not sentence:
            continue
        while len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(join(current, sentence)) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = join(current, sentence)
    if current:
        pieces.append(current)
    return pieces


class StructuredChunker(Chunker):
    version = CHUNKER_VERSION

    def __init__(self, max_chars: int = 1000, min_chars: int = 200, slack: float = 1.5):
        self.max_chars, self.min_chars, self.slack = max_chars, min_chars, slack

    def chunk(self, blocks: list[Block], document_id: uuid.UUID | None = None,
              knowledge_base_id: uuid.UUID | None = None) -> list[RetrievalChunk]:
        blocks = [b for b in blocks if b.content.strip()]
        chunks: list[RetrievalChunk] = []
        current: list[Part] = []
        section: str | None = None

        def size(parts: list[Part]) -> int:
            return len("\n".join(text for _, text, _ in parts))

        def has_body(parts: list[Part]) -> bool:
            return any(block.type != BlockType.HEADING for block, _, _ in parts)

        def emit(items: list[Part], kind: str = "text", **extra) -> None:
            body = [p for p in items if p[0].type != BlockType.HEADING] or items
            # A chunk can span a short section end and the next section; label it with the one
            # that contributes most of the text.
            weight: dict[str | None, int] = {}
            for _, text, sec in body:
                weight[sec] = weight.get(sec, 0) + len(text)
            label = max(weight, key=weight.get)
            ids = list(dict.fromkeys(block.id for block, _, _ in items))
            chunks.append(RetrievalChunk(
                id=uuid.uuid4(), content="\n".join(text for _, text, _ in items), block_ids=ids,
                document_id=document_id, knowledge_base_id=knowledge_base_id,
                section_title=label, metadata={"kind": kind, **extra}))

        def flush(parts: list[Part]) -> list[Part]:
            """Emit everything but trailing headings; return those headings to start the next chunk."""
            if not has_body(parts):
                return parts
            cut = len(parts)
            while parts[cut - 1][0].type == BlockType.HEADING:
                cut -= 1
            emit(parts[:cut])
            return parts[cut:]

        i = 0
        while i < len(blocks):
            block = blocks[i]
            part: Part = (block, block.content, section)
            if block.type == BlockType.HEADING:
                if has_body(current) and size(current) >= self.min_chars:
                    current = flush(current)
                section = " ".join(block.content.split())[:200]
                current.append((block, block.content, section))
            elif block.type == BlockType.TABLE:
                captions = []
                if current and current[-1][0].type == BlockType.CAPTION:
                    captions.append(current.pop())
                if i + 1 < len(blocks) and blocks[i + 1].type == BlockType.CAPTION:
                    i += 1
                    captions.append((blocks[i], blocks[i].content, section))
                headings = flush(current)
                current = []
                self._table(emit, headings, captions, part)
            elif block.type in _ATOMIC:
                if has_body(current) and size(current + [part]) > self.max_chars * self.slack:
                    current = flush(current)
                current.append(part)
            elif len(block.content) > self.max_chars:
                current = flush(current)
                pieces = split_sentences(block.content, self.max_chars)
                for k, piece in enumerate(pieces):
                    current.append((block, piece, section))
                    if k < len(pieces) - 1:
                        emit(current)
                        current = []
            else:
                if has_body(current) and size(current + [part]) > self.max_chars:
                    current = flush(current)
                current.append(part)
            i += 1
        if current:
            emit(current)
        return chunks

    def _table(self, emit, headings: list[Part], captions: list[Part], table: Part) -> None:
        context = headings + captions
        if len("\n".join(t for _, t, _ in context + [table])) <= self.max_chars * self.slack:
            emit(context + [table], "table")
            return
        block, content, section = table
        lines = content.split("\n")
        header, rows = (lines[:2], lines[2:]) if len(lines) > 2 and _TABLE_SEPARATOR.match(lines[1]) else ([], lines)
        fixed = len("\n".join(t for _, t, _ in captions + [(block, "\n".join(header), section)]))
        budget = max(self.max_chars - fixed, 1)
        groups, group = [], []
        for row in rows:
            if group and len("\n".join(group + [row])) > budget:
                groups.append(group)
                group = []
            group.append(row)
        if group:
            groups.append(group)
        for k, group in enumerate(groups, 1):
            piece = (block, "\n".join(header + group), section)
            # Headings once; caption and header row in every part so each part reads as a table.
            emit((headings if k == 1 else []) + captions + [piece], "table", part=k, parts=len(groups))
