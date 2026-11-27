"""Content fingerprints that identify a chunk or block across reprocessing.

Chunk and block ids are regenerated on every parse, so user corrections are
anchored to the generated text instead.
"""
import hashlib


def normalize(text: str) -> str:
    return " ".join(text.split())


def text_anchor(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def occurrences(texts) -> list[tuple[str, int]]:
    """(anchor, n) per text, where n counts earlier texts with the same anchor."""
    seen: dict[str, int] = {}
    out = []
    for text in texts:
        anchor = text_anchor(text)
        out.append((anchor, seen.get(anchor, 0)))
        seen[anchor] = seen.get(anchor, 0) + 1
    return out
