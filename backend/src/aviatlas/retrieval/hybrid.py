"""Hybrid retrieval: dense and BM25 recall in parallel, fused by Reciprocal Rank Fusion.

RRF uses only ranks (score = sum of 1 / (k + rank) over strategies), so dense
cosine similarities and BM25 scores never need to be put on one scale. Every
run can also return diagnostics for the Retrieval Inspector (System Design §9.8).
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.retrieval.models import RetrievalResult, RetrievalScope, ScoredHit
from aviatlas.retrieval.resolution import ScopedRecords

Method = Literal["hybrid", "dense", "bm25"]
METHODS = ("hybrid", "dense", "bm25")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retrieval")


@dataclass
class RetrievalRun:
    query: str
    method: str
    results: list[RetrievalResult]
    query_terms: list[str] = field(default_factory=list)
    dense: list[RetrievalResult] = field(default_factory=list)
    bm25: list[RetrievalResult] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    config: dict[str, object] = field(default_factory=dict)


def _ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 2)


class HybridRetriever:
    def __init__(self, dense: DenseRetriever, keyword: KeywordRetriever, *, rrf_k: int = 60, candidate_k: int = 20):
        self.dense = dense
        self.keyword = keyword
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k

    def search(self, query: str, scope: RetrievalScope, top_k: int = 3, method: Method = "hybrid") -> list[RetrievalResult]:
        return self.run(query, scope, top_k, method).results

    def run(self, query: str, scope: RetrievalScope, top_k: int = 3, method: Method = "hybrid") -> RetrievalRun:
        if method not in METHODS:
            raise ValueError(f"Unknown retrieval method: {method}")
        started = perf_counter()
        use_dense, use_bm25 = method in ("hybrid", "dense"), method in ("hybrid", "bm25")

        def ensure_ready(document_id, chunk_ids):
            if use_dense:
                self.dense.ensure_ready(document_id, chunk_ids)
            if use_bm25:
                self.keyword.ensure_ready(document_id, chunk_ids)

        records = ScopedRecords(self.dense.repository, scope, ensure_ready)
        terms = self.keyword.tokenizer(query) if use_bm25 else []
        timings = {"scope": _ms(started)}
        candidate_k = max(top_k, self.candidate_k) if method == "hybrid" else top_k

        def timed(name, fn, *args):
            start = perf_counter()
            hits = fn(*args)
            timings[name] = _ms(start)
            return hits

        dense_job = _pool.submit(timed, "dense", self.dense.hits, query, records, candidate_k) if use_dense else None
        bm25_hits = timed("bm25", self.keyword.hits, terms, records, candidate_k) if use_bm25 else []
        dense_hits = dense_job.result() if dense_job else []

        start = perf_counter()
        dense_results = [records.result(query, h, rank=i, method="dense") for i, h in enumerate(dense_hits, 1)]
        bm25_results = [records.result(query, h, rank=i, method="bm25") for i, h in enumerate(bm25_hits, 1)]
        if method == "dense":
            results = dense_results[:top_k]
        elif method == "bm25":
            results = bm25_results[:top_k]
        else:
            results = self._fuse(query, records, dense_hits, bm25_hits, top_k)
        timings["fusion"] = _ms(start)
        timings["total"] = _ms(started)
        config = {"rrf_k": self.rrf_k, "candidate_k": candidate_k,
                  "embedding_model": self.dense.embedder.model_name,
                  "tokenizer": self.keyword.keywords.tokenizer_version}
        return RetrievalRun(query, method, results, terms, dense_results, bm25_results, timings, config)

    def _fuse(self, query: str, records: ScopedRecords, dense_hits: list[ScoredHit],
              bm25_hits: list[ScoredHit], top_k: int) -> list[RetrievalResult]:
        fused: dict = {}
        for name, hits in (("dense", dense_hits), ("bm25", bm25_hits)):
            for rank, hit in enumerate(hits, start=1):
                entry = fused.setdefault(hit.chunk_id, {"hit": hit, "rrf": 0.0, "scores": {}, "ranks": {}, "terms": ()})
                entry["rrf"] += 1.0 / (self.rrf_k + rank)
                entry["scores"][name], entry["ranks"][name] = hit.score, rank
                if name == "bm25":
                    entry["terms"] = hit.matched_terms
        ordered = sorted(fused.values(), key=lambda e: (-e["rrf"], min(e["ranks"].values()), str(e["hit"].chunk_id)))
        results = []
        for rank, entry in enumerate(ordered[:top_k], start=1):
            hit = entry["hit"]
            rrf = round(entry["rrf"], 6)
            results.append(records.result(
                query, ScoredHit(hit.chunk_id, hit.document_id, rrf), rank=rank, method="hybrid",
                scores={**entry["scores"], "rrf": rrf}, ranks=entry["ranks"], matched_terms=entry["terms"]))
        return results
