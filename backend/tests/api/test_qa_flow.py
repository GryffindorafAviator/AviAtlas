"""Exercise API orchestration without downloading models or calling providers."""
from types import SimpleNamespace
from uuid import uuid4
import json
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.api import main
from aviatlas.parsing.models import (
    Block, BlockType, BoundingBox, DocumentMetadata, ParsedDocument, ProvenanceItem,
)
from aviatlas.retrieval.semantic_retriever import SemanticRetriever
from aviatlas.storage.document_registry import DocumentRegistry


class Encoder:
    def encode(self, texts, **kwargs):
        # A deterministic semantic substitute: queries need not share source words.
        return np.array([[1., 0.] if '光合' in t or '阳光' in t else [0., 1.] for t in texts])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    from aviatlas.api.dependencies import Services, get_services
    from aviatlas.answering.generator import AnswerGenerator
    from aviatlas.chunking.greedy_chunker import GreedyChunker
    from aviatlas.services.documents import DocumentService
    from aviatlas.services.qa import QuestionAnsweringService
    from aviatlas.storage.index_store import IndexStore
    repository = DocumentRegistry()
    indexes = IndexStore()
    block = Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0,
                  content='植物通过光合作用将光能转化为化学能。', provenance=[
                      ProvenanceItem(page_number=7, bbox=BoundingBox(x0=0,y0=0,x1=1,y1=1))])
    parser = SimpleNamespace(parse=lambda path: ParsedDocument(
        metadata=DocumentMetadata(title='植物', page_count=7), blocks=[block]))
    services = Services(
        DocumentService(repository, indexes, tmp_path, lambda: parser, GreedyChunker(),
                        lambda chunks, blocks: SemanticRetriever(chunks, blocks, Encoder())),
        QuestionAnsweringService(repository, indexes, AnswerGenerator()),
    )
    main.app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(main.app) as test_client:
            test_client.services = services
            test_client.parser = parser
            yield test_client
    finally:
        main.app.dependency_overrides.pop(get_services, None)



def upload(client):
    response = client.post('/api/v1/documents/upload', params={'auto_process': False}, files={'file': ('book.pdf', b'%PDF-fake')})
    assert response.status_code == 200
    return response.json()['document_id']


def test_upload_index_answer_with_pages(client, monkeypatch):
    doc_id = upload(client)
    other_id = upload(client)
    assert client.post('/api/v1/documents/parse', params={'document_id': doc_id}).json()['status'] == 'indexed'
    calls = []
    class Provider:
        def __init__(self, **kwargs):
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text='植物利用阳光制造养分 [1]（第7页）。')
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=Provider))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    response = client.post('/api/v1/documents/ask', json={
        'document_id': doc_id, 'question': '阳光有什么作用？'})
    assert response.status_code == 200
    data = response.json()
    assert '[1]' in data['answer']
    assert data['sources'][0]['page_range'] == [7, 7]
    assert data['sources'][0]['document_id'] == doc_id
    evidence = json.loads(calls[0]['input'])['evidence']
    assert evidence[0]['pages'] == [7]
    assert calls[0]['store'] is False
    assert client.post('/api/v1/documents/ask', json={
        'document_id': other_id, 'question': '阳光'}).status_code == 400


def test_missing_configuration_is_explicit(client, monkeypatch):
    doc_id = upload(client)
    client.post('/api/v1/documents/parse', params={'document_id': doc_id})
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    response = client.post('/api/v1/documents/ask', json={'document_id': doc_id, 'question': '阳光'})
    assert response.status_code == 503
    assert 'OPENAI_API_KEY' in response.json()['detail']


def test_parse_failure_marks_document_failed(client, monkeypatch):
    doc_id = upload(client)
    def fail(*args): raise RuntimeError('broken PDF')
    monkeypatch.setattr(client.parser, 'parse', fail)
    assert client.post('/api/v1/documents/parse', params={'document_id': doc_id}).status_code == 500
    assert client.services.documents.repository.get(doc_id).status == 'failed'
    assert client.services.documents.indexes.get(client.services.documents.repository.get(doc_id).id) is None


def test_semantic_ranking_and_document_scope():
    from aviatlas.retrieval.models import RetrievalChunk
    doc_id = uuid4()
    chunks = [RetrievalChunk(content='岩石', document_id=doc_id),
              RetrievalChunk(content='光合作用', document_id=doc_id)]
    hits = SemanticRetriever(chunks, [], Encoder()).search('阳光', top_k=1)
    assert len(hits) == 1
    assert hits[0].chunk_id == chunks[1].id
    assert hits[0].document_id == doc_id


def test_provider_failure_returns_502(client, monkeypatch):
    from aviatlas.answering.generator import AnswerGenerationError
    doc_id = upload(client)
    client.post('/api/v1/documents/parse', params={'document_id': doc_id})
    def fail(*args): raise AnswerGenerationError('Provider unavailable')
    monkeypatch.setattr(client.services.qa.answerer, 'generate', fail)
    response = client.post('/api/v1/documents/ask', json={'document_id': doc_id, 'question': '阳光'})
    assert response.status_code == 502


def test_parser_preserves_interleaved_table_order(tmp_path):
    from docling_core.types.doc import DocItemLabel
    from aviatlas.parsing.docling_parser import DoclingParser
    text1 = SimpleNamespace(label=DocItemLabel.TEXT, text='Before table')
    table = SimpleNamespace(label=DocItemLabel.TABLE,
                            export_to_markdown=lambda **kwargs: '| evidence |')
    text2 = SimpleNamespace(label=DocItemLabel.TEXT, text='After table')
    document = SimpleNamespace(pages={1: object()},
                               iterate_items=lambda: iter([(text1, 0), (table, 0), (text2, 0)]))
    parser = DoclingParser.__new__(DoclingParser)
    parser._converter = SimpleNamespace(convert=lambda path: SimpleNamespace(document=document))
    result = parser.parse(tmp_path / 'book.pdf')
    assert [b.content for b in result.blocks] == ['Before table', '| evidence |', 'After table']
    assert [b.order for b in result.blocks] == [0, 1, 2]


def test_http_validation_and_not_found_mapping(client):
    assert client.post('/api/v1/documents/parse', params={'document_id': 'invalid'}).status_code == 400
    assert client.post('/api/v1/documents/parse', params={'document_id': str(uuid4())}).status_code == 404
    assert client.post('/api/v1/documents/ask', json={
        'document_id': str(uuid4()), 'question': 'test'}).status_code == 404
    assert client.post('/api/v1/documents/upload', params={'auto_process': False}, files={
        'file': ('book.txt', b'text')}).status_code == 400


def test_auto_processing_list_detail_and_delete(client):
    response = client.post('/api/v1/documents/upload', files={'file': ('book.pdf', b'%PDF')})
    assert response.status_code == 200
    assert response.json()['status'] == 'queued'
    doc_id = response.json()['document_id']
    detail = client.get(f'/api/v1/documents/{doc_id}').json()
    assert detail['status'] == 'indexed'
    assert detail['page_count'] == 7
    assert detail['blocks_count'] == 1
    assert 'filepath' not in detail
    assert 'blocks' not in detail
    listing = client.get('/api/v1/documents', params={'limit': 1}).json()
    assert listing['total'] == 1
    assert listing['items'][0]['document_id'] == doc_id
    from uuid import UUID
    record = client.services.documents.get(UUID(doc_id))
    path = record.filepath
    assert client.delete(f'/api/v1/documents/{doc_id}').status_code == 204
    assert not path.exists()
    assert client.get(f'/api/v1/documents/{doc_id}').status_code == 404
    assert client.get('/api/v1/documents').json()['total'] == 0
    assert client.post('/api/v1/documents/ask', json={'document_id': doc_id, 'question': 'test'}).status_code == 404


def test_background_failure_and_retry(client, monkeypatch):
    original = client.parser.parse
    def fail(path): raise ValueError('Cannot extract text')
    monkeypatch.setattr(client.parser, 'parse', fail)
    doc_id = client.post('/api/v1/documents/upload', files={
        'file': ('fail.pdf', b'%PDF')}).json()['document_id']
    detail = client.get(f'/api/v1/documents/{doc_id}').json()
    assert detail['status'] == 'failed'
    assert 'Cannot extract text' in detail['last_error']
    monkeypatch.setattr(client.parser, 'parse', original)
    response = client.post(f'/api/v1/documents/{doc_id}/retry')
    assert response.status_code == 202
    assert response.json()['status'] == 'queued'
    detail = client.get(f'/api/v1/documents/{doc_id}').json()
    assert detail['status'] == 'indexed'
    assert detail['last_error'] is None
    assert client.post(f'/api/v1/documents/{doc_id}/retry').status_code == 400


def test_manual_process_and_duplicate_queue(client):
    from uuid import UUID
    doc_id = upload(client)
    assert client.post(f'/api/v1/documents/{doc_id}/process').status_code == 202
    assert client.get(f'/api/v1/documents/{doc_id}').json()['status'] == 'indexed'
    client.services.documents.enqueue(UUID(doc_id))
    assert client.post(f'/api/v1/documents/{doc_id}/process').status_code == 409
    assert client.post('/api/v1/documents/parse', params={'document_id': doc_id}).status_code == 409
    # Deleting a queued document cancels its pending callback.
    assert client.delete(f'/api/v1/documents/{doc_id}').status_code == 204
    client.services.documents.process_queued(UUID(doc_id))
    assert client.get(f'/api/v1/documents/{doc_id}').status_code == 404
