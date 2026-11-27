"""Application workflows can run without FastAPI or model providers."""
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.models import Block, BlockType, DocumentMetadata, ParsedDocument
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.services.documents import DocumentService
from aviatlas.services.errors import DocumentProcessingFailed, DocumentNotReady
from aviatlas.services.qa import QuestionAnsweringService
from aviatlas.storage.document_registry import DocumentRegistry
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex


class Encoder:
    def encode(self, texts, **kwargs):
        return np.array([[1., 0.] for _ in texts])


def test_services_share_index_and_invalidate_it_on_failed_reparse(tmp_path):
    repository, vectors = DocumentRegistry(), InMemoryVectorIndex()
    embedder = SentenceTransformerEmbedder('test-model', Encoder())
    block = Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0, content='test evidence')
    parsed = ParsedDocument(metadata=DocumentMetadata(page_count=1), blocks=[block])
    parser = SimpleNamespace(parse=lambda path: parsed)
    docs = DocumentService(repository, vectors, tmp_path, lambda: parser, GreedyChunker(), embedder)
    qa = QuestionAnsweringService(repository, DenseRetriever(repository, embedder, vectors),
                                 SimpleNamespace(generate=lambda question, sources, **kwargs: 'answer [1]'))
    record = docs.upload('test.pdf', BytesIO(b'%PDF'))
    assert record.filepath.read_bytes() == b'%PDF'
    docs.parse(record.id)
    [hit] = qa.ask(record.id, 'question').sources
    assert (hit.content, hit.document_id, hit.rank, hit.retrieval_method) == (
        'test evidence', record.id, 1, 'dense')
    def fail(path): raise ValueError('bad input')
    parser.parse = fail
    with pytest.raises(DocumentProcessingFailed): docs.parse(record.id)
    assert record.status == 'failed'
    assert not vectors.contains(record.id)
    with pytest.raises(DocumentNotReady): qa.ask(record.id, 'question')
    parser.parse = lambda path: parsed
    docs.parse(record.id)
    assert qa.ask(record.id, 'question').answer == 'answer [1]'


def test_empty_retrieval_does_not_invoke_generator(tmp_path):
    repository = DocumentRegistry()
    record = repository.register('test.pdf')
    from aviatlas.retrieval.models import RetrievalChunk
    record.chunks = [RetrievalChunk(content='evidence')]
    record.set_status('indexed')
    def unexpected(*args): raise AssertionError('Generator must not be called')
    qa = QuestionAnsweringService(repository, SimpleNamespace(search=lambda query, scope, top_k: []),
                                 SimpleNamespace(generate=unexpected))
    assert qa.ask(record.id, 'question').sources == []
