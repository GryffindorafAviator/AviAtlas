"""KeywordIndex backends: SQLite postings for persistence, in-memory for tests.

Both store term frequencies and chunk lengths per document and score with the
shared BM25 implementation over the documents in scope.
"""
from collections import Counter
from collections.abc import Collection, Sequence
from uuid import UUID

from aviatlas.retrieval import bm25
from aviatlas.retrieval.models import ScoredHit
from aviatlas.retrieval.tokenizer import TOKENIZER_VERSION
from aviatlas.services.errors import DocumentNotReady
from aviatlas.storage.digest import chunk_digest
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


def _check(entry, chunk_ids, tokenizer_version):
    if entry is None:
        raise DocumentNotReady("Document is not ready. Please parse it first.")
    digest, tokenizer = entry
    if tokenizer != tokenizer_version:
        raise DocumentNotReady("Keyword tokenizer changed. Please parse this document again to rebuild its index.")
    if digest != chunk_digest(chunk_ids):
        raise DocumentNotReady("Document keyword index is inconsistent. Please parse it again.")


class InMemoryKeywordIndex:
    def __init__(self, tokenizer_version: str = TOKENIZER_VERSION):
        self.tokenizer_version = tokenizer_version
        self._manifest: dict[UUID, tuple[str, str]] = {}
        self._chunks: dict[UUID, dict[UUID, tuple[Counter, int]]] = {}

    def replace(self, document_id: UUID, chunks: Sequence[tuple[UUID, list[str]]]) -> None:
        self._chunks[document_id] = {chunk_id: (Counter(tokens), len(tokens)) for chunk_id, tokens in chunks}
        self._manifest[document_id] = (chunk_digest([c for c, _ in chunks]), self.tokenizer_version)

    def delete(self, document_id: UUID) -> None:
        self._chunks.pop(document_id, None)
        self._manifest.pop(document_id, None)

    def contains(self, document_id: UUID) -> bool:
        return document_id in self._manifest

    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID]) -> None:
        _check(self._manifest.get(document_id), chunk_ids, self.tokenizer_version)

    def search(self, terms: Sequence[str], top_k: int, document_ids: Collection[UUID]) -> list[ScoredHit]:
        terms = set(terms)
        chunks = [(c, d, counts, length) for d in document_ids for c, (counts, length) in self._chunks.get(d, {}).items()]
        if not terms or not chunks:
            return []
        postings = [(t, c, d, counts[t], length) for c, d, counts, length in chunks for t in terms if counts[t]]
        frequencies = Counter(t for t, *_ in postings)
        average = sum(length for *_, length in chunks) / len(chunks)
        return bm25.rank(postings, frequencies, len(chunks), average, top_k)


class SQLiteKeywordIndex:
    def __init__(self, repository: SQLiteDocumentRepository, tokenizer_version: str = TOKENIZER_VERSION):
        self.repository = repository
        self.tokenizer_version = tokenizer_version
        with repository.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS keyword_manifest (
                    document_id TEXT PRIMARY KEY, chunk_digest TEXT NOT NULL, tokenizer TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS keyword_chunks (
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, length INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS keyword_postings (
                    term TEXT NOT NULL, chunk_id TEXT NOT NULL, document_id TEXT NOT NULL, tf INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS keyword_chunks_document ON keyword_chunks(document_id);
                CREATE INDEX IF NOT EXISTS keyword_postings_term ON keyword_postings(term, document_id);
                CREATE INDEX IF NOT EXISTS keyword_postings_document ON keyword_postings(document_id);
            """)

    @staticmethod
    def _clear(connection, document_id: UUID) -> None:
        for table in ("keyword_manifest", "keyword_chunks", "keyword_postings"):
            connection.execute(f"DELETE FROM {table} WHERE document_id=?", (str(document_id),))

    def replace(self, document_id: UUID, chunks: Sequence[tuple[UUID, list[str]]]) -> None:
        doc = str(document_id)
        # One transaction: readers see either the old entries or the complete new ones.
        with self.repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._clear(connection, document_id)
            connection.executemany("INSERT INTO keyword_chunks VALUES (?, ?, ?)",
                                   [(str(c), doc, len(tokens)) for c, tokens in chunks])
            connection.executemany("INSERT INTO keyword_postings VALUES (?, ?, ?, ?)",
                                   [(term, str(c), doc, tf) for c, tokens in chunks
                                    for term, tf in Counter(tokens).items()])
            connection.execute("INSERT INTO keyword_manifest VALUES (?, ?, ?)",
                               (doc, chunk_digest([c for c, _ in chunks]), self.tokenizer_version))

    def delete(self, document_id: UUID) -> None:
        with self.repository.connect() as connection:
            self._clear(connection, document_id)

    def _entry(self, document_id: UUID):
        with self.repository.connect() as connection:
            return connection.execute("SELECT chunk_digest, tokenizer FROM keyword_manifest WHERE document_id=?",
                                      (str(document_id),)).fetchone()

    def contains(self, document_id: UUID) -> bool:
        return self._entry(document_id) is not None

    def ensure_ready(self, document_id: UUID, chunk_ids: Sequence[UUID]) -> None:
        _check(self._entry(document_id), chunk_ids, self.tokenizer_version)

    def search(self, terms: Sequence[str], top_k: int, document_ids: Collection[UUID]) -> list[ScoredHit]:
        terms, docs = sorted(set(terms)), [str(d) for d in document_ids]
        if not terms or not docs:
            return []
        in_docs, in_terms = ",".join("?" * len(docs)), ",".join("?" * len(terms))
        with self.repository.connect() as connection:
            count, average = connection.execute(
                f"SELECT COUNT(*), AVG(length) FROM keyword_chunks WHERE document_id IN ({in_docs})", docs).fetchone()
            rows = connection.execute(
                f"SELECT p.term, p.chunk_id, p.document_id, p.tf, c.length FROM keyword_postings p "
                f"JOIN keyword_chunks c ON c.chunk_id = p.chunk_id "
                f"WHERE p.term IN ({in_terms}) AND p.document_id IN ({in_docs})", [*terms, *docs]).fetchall()
        postings = [(t, UUID(c), UUID(d), tf, length) for t, c, d, tf, length in rows]
        return bm25.rank(postings, Counter(t for t, *_ in postings), count, average or 0.0, top_k)
