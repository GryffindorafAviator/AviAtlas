"""Fingerprints that tie derived indexes to the chunk records they were built from."""
from collections.abc import Sequence
import hashlib
from uuid import UUID


def chunk_digest(chunk_ids: Sequence[UUID]) -> str:
    return hashlib.sha256("|".join(str(chunk_id) for chunk_id in chunk_ids).encode()).hexdigest()
