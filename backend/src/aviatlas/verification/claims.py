"""Split answers into checkable claims and run local checks (no model call).

A claim is one sentence of a library-sourced segment, with the citation numbers
it carries. Model-supplied segments are not checked against documents: their
source type already says they did not come from them (§11.5).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from aviatlas.answering.segments import _CITATION
from aviatlas.chunking.anchors import normalize

MIN_CLAIM_CHARS = 8
MAX_CLAIMS = 20
_SENTENCE = re.compile(r"(?<=[。！？!?；])|(?<=[.;])\s+(?=[A-Z0-9\"'(\[])|\n+")
_MARKDOWN = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+|>\s*)")
# Integers with 2+ digits, decimals, percentages; single digits are too common to check.
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*%?|\d+\.\d+")


@dataclass
class Claim:
    index: int
    text: str
    source_type: str
    citations: list[int]
    hints: list[str] = field(default_factory=list)


def _citations(text: str) -> list[int]:
    return sorted({int(n) for group in _CITATION.findall(text) for n in group.split(",")})


def extract_claims(segments) -> list[Claim]:
    """Claims from `library` and `unlabeled` segments, in order, at most MAX_CLAIMS."""
    claims: list[Claim] = []
    for segment in segments:
        if segment.source_type == "model":
            continue
        for piece in _SENTENCE.split(segment.text):
            text = _MARKDOWN.sub("", piece or "").strip()
            bare = normalize(_CITATION.sub("", text)).strip(" .。:：")
            if len(bare) < MIN_CLAIM_CHARS:
                continue
            claims.append(Claim(len(claims) + 1, text, segment.source_type, _citations(text)))
            if len(claims) == MAX_CLAIMS:
                return claims
    return claims


def numbers(text: str) -> set[str]:
    found = set()
    for match in _NUMBER.findall(_CITATION.sub("", text)):
        value = match.rstrip("%").replace(",", "")
        if len(value.replace(".", "")) >= 2:
            found.add(value)
    return found


def local_checks(claims: list[Claim], texts: dict[int, str]) -> list[Claim]:
    """Attach hints: missing citations, unknown citation numbers, numbers absent from the cited text.
    `texts` maps citation number to source text."""
    for claim in claims:
        if not claim.citations:
            claim.hints.append("uncited")
            continue
        unknown = [n for n in claim.citations if n not in texts]
        if unknown:
            claim.hints.append("unknown_citation:" + ",".join(map(str, unknown)))
        cited = " ".join(texts[n] for n in claim.citations if n in texts).replace(",", "")
        missing = sorted(n for n in numbers(claim.text) if n not in cited)
        if missing and len(unknown) < len(claim.citations):
            claim.hints.append("number_not_in_source:" + ",".join(missing))
    return claims


def checks(segments, texts: dict[int, str]) -> list[dict]:
    """Claims that have at least one hint, for storing with an answer."""
    return [{"claim": c.index, "text": c.text, "hints": c.hints}
            for c in local_checks(extract_claims(segments), texts) if c.hints]
