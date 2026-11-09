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
    monkeypatch.setattr(main, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(main, 'registry', DocumentRegistry())
    monkeypatch.setattr(main, 'retrievers', {})
    monkeypatch.setattr(main, 'SemanticRetriever',
                        lambda chunks, blocks: SemanticRetriever(chunks, blocks, Encoder()))
    block = Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0,
                  content='植物通过光合作用将光能转化为化学能。', provenance=[
                      ProvenanceItem(page_number=7, bbox=BoundingBox(x0=0,y0=0,x1=1,y1=1))])
    monkeypatch.setattr(main.DoclingParser, 'parse', lambda self, path: ParsedDocument(
        metadata=DocumentMetadata(title='植物', page_count=7), blocks=[block]))
    return TestClient(main.app)


def upload(client):
    response = client.post('/api/v1/documents/upload', files={'file': ('book.pdf', b'%PDF-fake')})
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
    monkeypatch.setattr(main.DoclingParser, 'parse', fail)
    assert client.post('/api/v1/documents/parse', params={'document_id': doc_id}).status_code == 500
    assert main.registry.get(doc_id).status == 'failed'
    assert main.registry.get(doc_id).id not in main.retrievers


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
    monkeypatch.setattr(main.AnswerGenerator, 'generate', fail)
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
