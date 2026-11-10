"""Application workflows can run without FastAPI or model providers."""
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.models import Block, BlockType, DocumentMetadata, ParsedDocument
from aviatlas.retrieval.models import RetrievalResult
from aviatlas.services.documents import DocumentService
from aviatlas.services.errors import DocumentProcessingFailed, DocumentNotReady
from aviatlas.services.qa import QuestionAnsweringService
from aviatlas.storage.document_registry import DocumentRegistry
from aviatlas.storage.index_store import IndexStore


def test_services_share_index_and_invalidate_it_on_failed_reparse(tmp_path):
    repository, indexes = DocumentRegistry(), IndexStore()
    block = Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0, content='test evidence')
    parsed = ParsedDocument(metadata=DocumentMetadata(page_count=1), blocks=[block])
    parser = SimpleNamespace(parse=lambda path: parsed)
    hit = RetrievalResult(content='test evidence')
    index = SimpleNamespace(search=lambda query, top_k: [hit])
    docs = DocumentService(repository, indexes, tmp_path, lambda: parser,
                           GreedyChunker(), lambda chunks, blocks: index)
    qa = QuestionAnsweringService(repository, indexes,
                                 SimpleNamespace(generate=lambda question, sources: 'answer [1]'))
    record = docs.upload('test.pdf', BytesIO(b'%PDF'))
    assert record.filepath.read_bytes() == b'%PDF'
    docs.parse(record.id)
    assert qa.ask(record.id, 'question').sources == [hit]
    def fail(path): raise ValueError('bad input')
    parser.parse = fail
    with pytest.raises(DocumentProcessingFailed): docs.parse(record.id)
    assert record.status == 'failed'
    assert indexes.get(record.id) is None
    with pytest.raises(DocumentNotReady): qa.ask(record.id, 'question')
    parser.parse = lambda path: parsed
    docs.parse(record.id)
    assert qa.ask(record.id, 'question').answer == 'answer [1]'


def test_empty_retrieval_does_not_invoke_generator(tmp_path):
    repository, indexes = DocumentRegistry(), IndexStore()
    record = repository.register('test.pdf')
    from aviatlas.retrieval.models import RetrievalChunk
    record.chunks = [RetrievalChunk(content='evidence')]
    record.set_status('indexed')
    indexes.save(record.id, SimpleNamespace(search=lambda query, top_k: []))
    def unexpected(*args): raise AssertionError('Generator must not be called')
    qa = QuestionAnsweringService(repository, indexes, SimpleNamespace(generate=unexpected))
    assert qa.ask(record.id, 'question').sources == []
