"""Tokenization, BM25, keyword index contract, and hybrid RRF fusion."""
import math
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from aviatlas.retrieval import bm25
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.retrieval.models import RetrievalChunk, RetrievalScope
from aviatlas.retrieval.tokenizer import tokenize
from aviatlas.services.errors import DocumentNotReady
from aviatlas.storage.keyword_index import InMemoryKeywordIndex, SQLiteKeywordIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository


def test_tokenizer_handles_mixed_chinese_and_english():
    tokens = tokenize('多头注意力机制 Multi-Head Attention 的复杂度是 O(n^2)。')
    assert {'多头', '注意', '注意力', '机制', 'multi', 'head', 'attention', '复杂度'} <= set(tokens)
    assert not {'的', '是', '-', '(', '。', ' '} & set(tokens)
    assert tokenize('The BLEU score of the model') == ['bleu', 'score', 'model']
    assert tokenize('什么是分词？') == ['分词']


def test_bm25_matches_the_formula():
    a, b = uuid4(), uuid4()
    doc = uuid4()
    # term "x" appears twice in a 4-token chunk; corpus of 2 chunks, avg length 3, df=1
    [hit] = bm25.rank([('x', a, doc, 2, 4)], {'x': 1}, chunk_count=2, average_length=3, top_k=5)
    expected = math.log(1 + (2 - 1 + .5) / (1 + .5)) * 2 * 2.5 / (2 + 1.5 * (1 - .75 + .75 * 4 / 3))
    assert hit.chunk_id == a and hit.score == pytest.approx(expected) and hit.matched_terms == ('x',)
    assert bm25.rank([], {}, 0, 0, 5) == []


@pytest.fixture(params=['memory', 'sqlite'])
def keyword_index(request, tmp_path):
    if request.param == 'memory':
        return InMemoryKeywordIndex()
    return SQLiteKeywordIndex(SQLiteDocumentRepository(tmp_path / 'documents.sqlite3'))


def chunks(*texts):
    return [(uuid4(), tokenize(t)) for t in texts]


def test_rare_terms_and_short_chunks_rank_higher(keyword_index):
    doc = uuid4()
    entries = chunks('attention attention model', 'model training data', 'attention model with a long list of other words here')
    keyword_index.replace(doc, entries)
    hits = keyword_index.search(['attention'], 5, [doc])
    assert [h.chunk_id for h in hits] == [entries[0][0], entries[2][0]]
    assert hits[0].matched_terms == ('attention',)
    # "training" is rarer than "model" across the corpus
    assert keyword_index.search(['model', 'training'], 1, [doc])[0].chunk_id == entries[1][0]


def test_scope_and_global_top_k(keyword_index):
    first, second = uuid4(), uuid4()
    a, b = chunks('BLEU score table', 'other'), chunks('BLEU results', 'BLEU baseline')
    keyword_index.replace(first, a)
    keyword_index.replace(second, b)
    assert {h.document_id for h in keyword_index.search(['bleu'], 5, [second])} == {second}
    assert len(keyword_index.search(['bleu'], 2, [first, second])) == 2
    assert keyword_index.search([], 5, [first]) == [] and keyword_index.search(['bleu'], 5, []) == []


def test_replace_delete_and_readiness(keyword_index):
    doc = uuid4()
    entries = chunks('entropy')
    keyword_index.replace(doc, entries)
    ids = [c for c, _ in entries]
    keyword_index.ensure_ready(doc, ids)
    with pytest.raises(DocumentNotReady, match='inconsistent'):
        keyword_index.ensure_ready(doc, [uuid4()])
    keyword_index.tokenizer_version = 'next-tokenizer'
    with pytest.raises(DocumentNotReady, match='tokenizer changed'):
        keyword_index.ensure_ready(doc, ids)
    replacement = chunks('enthalpy')
    keyword_index.replace(doc, replacement)
    assert keyword_index.search(['entropy'], 5, [doc]) == []
    assert keyword_index.search(['enthalpy'], 5, [doc])[0].chunk_id == replacement[0][0]
    keyword_index.delete(doc)
    assert not keyword_index.contains(doc) and keyword_index.search(['enthalpy'], 5, [doc]) == []
    with pytest.raises(DocumentNotReady):
        keyword_index.ensure_ready(doc, ids)


class Encoder:
    """Dense space that only knows 'attention'; exact identifiers like BLEU are invisible to it."""
    def encode(self, texts, **kwargs):
        rows = [[1., .1] if 'attention' in t.lower() else [.1, 1.] for t in texts]
        return np.array([np.array(r) / np.linalg.norm(r) for r in rows])


@pytest.fixture
def hybrid(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    vectors, keywords = InMemoryVectorIndex(), SQLiteKeywordIndex(repo)
    embedder = SentenceTransformerEmbedder('test', Encoder())
    record = repo.register('paper.pdf')
    record.chunks = [RetrievalChunk(content=t, document_id=record.id) for t in [
        'Attentional focus of the model.', 'Table 2: BLEU 28.4 on WMT14.', 'Training used eight GPUs.',
        'Self-attention and BLEU improvements.']]
    record.set_status('indexed')
    repo.save(record)
    vectors.replace(record.id, [c.id for c in record.chunks], embedder.encode([c.content for c in record.chunks]),
                    model_name='test')
    keywords.replace(record.id, [(c.id, tokenize(c.content)) for c in record.chunks])
    retriever = HybridRetriever(DenseRetriever(repo, embedder, vectors), KeywordRetriever(repo, keywords), candidate_k=4)
    return SimpleNamespace(retriever=retriever, record=record, scope=RetrievalScope.for_document(record.id))


def test_bm25_finds_exact_identifiers_dense_misses(hybrid):
    texts = lambda results: [r.content for r in results]
    dense = {r.content: r.score for r in hybrid.retriever.search('BLEU', hybrid.scope, 4, method='dense')}
    # The dense space cannot tell the BLEU table from an unrelated chunk.
    assert dense['Table 2: BLEU 28.4 on WMT14.'] == pytest.approx(dense['Training used eight GPUs.'])
    bm25_only = hybrid.retriever.search('BLEU', hybrid.scope, 2, method='bm25')
    assert set(texts(bm25_only)) == {'Table 2: BLEU 28.4 on WMT14.', 'Self-attention and BLEU improvements.'}
    assert bm25_only[0].retrieval_method == 'bm25' and bm25_only[0].matched_terms == ['bleu']


def test_rrf_rewards_agreement_and_records_every_strategy(hybrid):
    run = hybrid.retriever.run('attention BLEU', hybrid.scope, 3)
    top = run.results[0]
    # The only chunk ranked highly by both strategies wins the fusion.
    assert top.content == 'Self-attention and BLEU improvements.'
    assert set(top.ranks) == {'dense', 'bm25'} and set(top.scores) == {'dense', 'bm25', 'rrf'}
    assert top.score == top.scores['rrf'] == pytest.approx(1 / (60 + top.ranks['dense']) + 1 / (60 + top.ranks['bm25']), abs=1e-6)
    assert [r.rank for r in run.results] == [1, 2, 3]
    assert all(r.retrieval_method == 'hybrid' for r in run.results)
    assert run.query_terms == ['attention', 'bleu']
    assert len(run.dense) == 4 and run.dense[0].retrieval_method == 'dense' and run.bm25[0].ranks == {'bm25': 1}
    assert set(run.timings_ms) == {'scope', 'dense', 'bm25', 'fusion', 'total'}
    assert run.config == {'rrf_k': 60, 'candidate_k': 4, 'embedding_model': 'test', 'tokenizer': 'jieba-search-v1'}


def test_missing_keyword_index_is_reported_for_hybrid_but_not_dense(hybrid):
    hybrid.retriever.keyword.keywords.delete(hybrid.record.id)
    with pytest.raises(DocumentNotReady):
        hybrid.retriever.search('attention', hybrid.scope)
    assert hybrid.retriever.search('attention', hybrid.scope, method='dense')
    with pytest.raises(ValueError):
        hybrid.retriever.search('attention', hybrid.scope, method='magic')
