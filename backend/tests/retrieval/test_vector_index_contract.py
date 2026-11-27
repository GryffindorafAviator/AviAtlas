"""Every VectorIndex backend must satisfy the same contract.

Add a new backend (e.g. Milvus) to `make_index` and the whole suite applies.
"""
from uuid import uuid4

import numpy as np
import pytest

from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.models import RetrievalChunk, RetrievalScope
from aviatlas.parsing.models import Block, BlockType, BoundingBox, ProvenanceItem
from aviatlas.services.errors import DocumentNotReady
from aviatlas.storage.chroma_vector_index import ChromaVectorIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository

AXES = {'plant': [1., 0., 0.], 'rock': [0., 1., 0.], 'star': [0., 0., 1.]}


class Encoder:
    def encode(self, texts, **kwargs):
        rows = [np.sum([AXES[w] for w in AXES if w in t] or [[.1, .1, .1]], axis=0) for t in texts]
        return np.array([r / np.linalg.norm(r) for r in rows])


@pytest.fixture(params=['memory', 'chroma'])
def setup(request, tmp_path):
    repository = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    vectors = InMemoryVectorIndex() if request.param == 'memory' else ChromaVectorIndex(repository, tmp_path / 'chroma')
    embedder = SentenceTransformerEmbedder('test-model', Encoder())

    def index_document(texts, page):
        record = repository.register('book.pdf')
        blocks = [Block(id=uuid4(), type=BlockType.PARAGRAPH, order=i, content=t,
                        provenance=[ProvenanceItem(page_number=page + i, bbox=BoundingBox(x0=0, y0=0, x1=1, y1=1))]) for i, t in enumerate(texts)]
        record.blocks = blocks
        record.chunks = [RetrievalChunk(content=b.content, block_ids=[b.id], document_id=record.id) for b in blocks]
        record.set_status('indexed')
        repository.save(record)
        vectors.replace(record.id, [c.id for c in record.chunks],
                        embedder.encode([c.content for c in record.chunks]), model_name='test-model')
        return record

    return repository, vectors, DenseRetriever(repository, embedder, vectors), index_document


def test_scope_limits_results_to_selected_documents(setup):
    _, _, retriever, index_document = setup
    botany = index_document(['plant cells', 'rock layers'], page=1)
    astronomy = index_document(['star formation', 'plant growth on mars'], page=10)
    only_astronomy = retriever.search('plant', RetrievalScope.for_document(astronomy.id), top_k=5)
    assert {r.document_id for r in only_astronomy} == {astronomy.id}
    assert only_astronomy[0].content == 'plant growth on mars'


def test_cross_document_results_merge_by_score_with_ranks_and_provenance(setup):
    _, _, retriever, index_document = setup
    botany = index_document(['plant cells', 'rock layers'], page=1)
    astronomy = index_document(['star formation', 'plant rock star'], page=10)
    results = retriever.search('plant', RetrievalScope.of([botany.id, astronomy.id]), top_k=2)
    assert [r.content for r in results] == ['plant cells', 'plant rock star']
    assert [r.rank for r in results] == [1, 2]
    assert all(r.retrieval_method == 'dense' for r in results)
    assert results[0].score == pytest.approx(1.0)
    assert results[1].score == pytest.approx(1 / np.sqrt(3), abs=1e-6)
    assert (results[0].document_id, results[0].page_range) == (botany.id, (1, 1))
    assert (results[1].document_id, results[1].page_range) == (astronomy.id, (11, 11))


def test_top_k_is_global_not_per_document(setup):
    _, _, retriever, index_document = setup
    ids = [index_document(['plant a', 'plant b'], page=1).id for _ in range(3)]
    assert len(retriever.search('plant', RetrievalScope.of(ids), top_k=4)) == 4


def test_replace_delete_and_readiness(setup):
    repository, vectors, retriever, index_document = setup
    record = index_document(['plant cells'], page=1)
    chunk_ids = [c.id for c in record.chunks]
    assert vectors.contains(record.id)
    vectors.ensure_ready(record.id, chunk_ids, model_name='test-model')
    with pytest.raises(DocumentNotReady, match='model changed'):
        vectors.ensure_ready(record.id, chunk_ids, model_name='other-model')
    with pytest.raises(DocumentNotReady, match='inconsistent'):
        vectors.ensure_ready(record.id, [uuid4()], model_name='test-model')
    vectors.replace(record.id, [chunk_ids[0]], Encoder().encode(['rock']), model_name='test-model')
    [hit] = vectors.search(Encoder().encode(['rock'])[0], 5, [record.id])
    assert hit.score == pytest.approx(1.0)
    vectors.delete(record.id)
    assert not vectors.contains(record.id)
    assert vectors.search(Encoder().encode(['rock'])[0], 5, [record.id]) == []
    with pytest.raises(DocumentNotReady):
        retriever.search('plant', RetrievalScope.for_document(record.id))


def test_unindexed_document_in_scope_is_reported(setup):
    repository, _, retriever, index_document = setup
    ready = index_document(['plant cells'], page=1)
    pending = repository.register('pending.pdf')
    with pytest.raises(DocumentNotReady):
        retriever.search('plant', RetrievalScope.of([ready.id, pending.id]))
    assert retriever.search('plant', RetrievalScope.of([])) == []
