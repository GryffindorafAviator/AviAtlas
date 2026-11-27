"""Okapi BM25 scoring shared by every KeywordIndex backend.

Corpus statistics (chunk count, average length, document frequencies) are
computed over the retrieval scope, so the same query can rank differently in a
collection than in the whole library.
"""
from collections import defaultdict
from collections.abc import Iterable
import math
from uuid import UUID

from aviatlas.retrieval.models import ScoredHit

K1, B = 1.5, 0.75


def idf(chunk_count: int, document_frequency: int) -> float:
    return math.log(1 + (chunk_count - document_frequency + 0.5) / (document_frequency + 0.5))


def rank(postings: Iterable[tuple[str, UUID, UUID, int, int]], document_frequencies: dict[str, int],
         chunk_count: int, average_length: float, top_k: int) -> list[ScoredHit]:
    """`postings` are (term, chunk_id, document_id, term_frequency, chunk_length) rows."""
    if not chunk_count or average_length <= 0:
        return []
    scores: dict[UUID, float] = defaultdict(float)
    matched: dict[UUID, list[str]] = defaultdict(list)
    owners: dict[UUID, UUID] = {}
    for term, chunk_id, document_id, tf, length in postings:
        norm = tf + K1 * (1 - B + B * length / average_length)
        scores[chunk_id] += idf(chunk_count, document_frequencies[term]) * tf * (K1 + 1) / norm
        matched[chunk_id].append(term)
        owners[chunk_id] = document_id
    ordered = sorted(scores, key=lambda c: (-scores[c], str(c)))[:top_k]
    return [ScoredHit(c, owners[c], scores[c], tuple(sorted(matched[c]))) for c in ordered]
