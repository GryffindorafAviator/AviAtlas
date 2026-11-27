"""Library search and source-aware answering across documents and collections."""
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.answering.segments import invalid_citations, split_segments
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.main import app
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.models import Block, BlockType, BoundingBox, DocumentMetadata, ParsedDocument, ProvenanceItem
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.services.collections import CollectionService
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository

TOPICS = ['rotation', 'photosynthesis', 'entropy']


class Encoder:
    def encode(self, texts, **kwargs):
        rows = [[float(topic in t.lower()) for topic in TOPICS] + [.01] for t in texts]
        return np.array([np.array(r) / np.linalg.norm(r) for r in rows])


class Answerer:
    def __init__(self):
        self.calls, self.reply = [], 'Grounded answer [1].'

    def generate(self, question, sources, **kwargs):
        self.calls.append(SimpleNamespace(question=question, sources=sources, **kwargs))
        return self.reply


@pytest.fixture
def lib(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    vectors, embedder = InMemoryVectorIndex(), SentenceTransformerEmbedder('test', Encoder())

    def parse(path):
        return ParsedDocument(metadata=DocumentMetadata(page_count=4), blocks=[
            Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0, content=path.read_text(),
                  provenance=[ProvenanceItem(page_number=4, bbox=BoundingBox(x0=0, y0=0, x1=1, y1=1))])])

    docs = DocumentService(repo, vectors, tmp_path / 'uploads', lambda: SimpleNamespace(parse=parse),
                           GreedyChunker(), embedder)
    retriever, resolver = DenseRetriever(repo, embedder, vectors), ScopeResolver(repo, collections)
    answerer = Answerer()
    services = Services(docs, QuestionAnsweringService(repo, retriever, answerer, resolver),
                        collections, SearchService(retriever, resolver))

    def add(name, text, process=True):
        record = docs.upload(name, BytesIO(text.encode()))
        if process:
            docs.parse(record.id)
        return str(record.id)

    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, add=add, answerer=answerer, collections=collections)
    app.dependency_overrides.pop(get_services, None)


def test_library_search_spans_documents_and_reports_unprocessed(lib):
    physics = lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    biology = lib.add('plants.pdf', 'Photosynthesis converts light energy.')
    pending = lib.add('draft.pdf', 'Entropy notes.', process=False)
    data = lib.client.post('/api/v1/search', json={'query': 'rotation', 'top_k': 5}).json()
    assert data['results'][0]['document_id'] == physics
    assert data['results'][0]['document_name'] == 'mechanics.pdf'
    assert data['results'][0]['page_range'] == [4, 4]
    assert [r['rank'] for r in data['results']] == [1, 2]
    assert set(data['document_ids']) == {physics, biology}
    assert data['skipped_document_ids'] == [pending]
    assert not lib.answerer.calls


def test_collection_and_document_scopes(lib):
    physics = lib.add('mechanics.pdf', 'Rigid body rotation.')
    lib.add('plants.pdf', 'Photosynthesis and rotation of crops.')
    group = lib.client.post('/api/v1/collections', json={'name': 'Physics'}).json()['collection_id']
    lib.client.put(f'/api/v1/documents/{physics}/collections', json={'collection_ids': [group]})
    by_collection = lib.client.post('/api/v1/search', json={'query': 'rotation', 'collection_id': group}).json()
    assert {r['document_id'] for r in by_collection['results']} == {physics}
    by_document = lib.client.post('/api/v1/search', json={'query': 'rotation', 'document_ids': [physics]}).json()
    assert by_document['document_ids'] == [physics]


def test_scope_errors(lib):
    ready = lib.add('mechanics.pdf', 'Rotation.')
    pending = lib.add('draft.pdf', 'Entropy.', process=False)
    post = lambda body: lib.client.post('/api/v1/search', json={'query': 'rotation', **body})
    assert post({'document_ids': [pending]}).status_code == 400
    assert post({'document_ids': [str(uuid4())]}).status_code == 404
    assert post({'collection_id': str(uuid4())}).status_code == 404
    assert post({'document_ids': [ready], 'collection_id': str(uuid4())}).status_code == 400
    assert post({'document_ids': []}).status_code == 422
    assert lib.client.post('/api/v1/search', json={'query': '   '}).status_code == 400


def test_grounded_library_answer_cites_across_documents(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    lib.add('plants.pdf', 'Photosynthesis converts light energy.')
    data = lib.client.post('/api/v1/ask', json={'question': 'What is photosynthesis?'}).json()
    assert data['mode'] == 'grounded'
    assert data['segments'] == [{'source_type': 'library', 'text': 'Grounded answer [1].'}]
    assert data['invalid_citations'] == []
    assert data['sources'][0]['document_name'] == 'plants.pdf'
    assert lib.answerer.calls[0].mode == 'grounded'


def test_grounded_answer_without_documents_skips_the_model(lib):
    data = lib.client.post('/api/v1/ask', json={'question': 'Anything?'}).json()
    assert data['sources'] == [] and data['segments'][0]['source_type'] == 'library'
    assert '没有可检索的文档' in data['answer']
    assert not lib.answerer.calls


def test_open_mode_labels_model_knowledge_and_flags_invalid_citations(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    lib.answerer.reply = '[LIBRARY]\nThe book defines angular velocity [1].\n[MODEL]\nFinite rotations do not commute [7].'
    data = lib.client.post('/api/v1/ask', json={'question': 'Why is rotation not a vector?', 'mode': 'open'}).json()
    assert [s['source_type'] for s in data['segments']] == ['library', 'model']
    assert data['segments'][1]['text'] == 'Finite rotations do not commute [7].'
    assert '[MODEL]' not in data['answer']
    assert data['invalid_citations'] == [7]


def test_open_mode_answers_from_model_when_library_is_empty(lib):
    lib.answerer.reply = '[MODEL]\nBackground explanation.'
    data = lib.client.post('/api/v1/ask', json={'question': 'Explain entropy', 'mode': 'open'}).json()
    assert data['segments'] == [{'source_type': 'model', 'text': 'Background explanation.'}]
    assert lib.answerer.calls[0].sources == []


def test_selected_text_guides_retrieval_and_reaches_the_model(lib):
    physics = lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    lib.add('thermo.pdf', 'Entropy always increases in isolated systems.')
    response = lib.client.post('/api/v1/ask', json={
        'question': 'Explain this', 'document_ids': [physics],
        'selection': {'text': 'angular velocity is a vector', 'document_id': physics, 'page_number': 4}})
    assert response.status_code == 200
    call = lib.answerer.calls[0]
    assert call.selection.text == 'angular velocity is a vector' and call.selection.page_number == 4
    assert call.question == 'Explain this'
    assert {s.document_id for s in call.sources} == {UUID(physics)}
    too_long = lib.client.post('/api/v1/ask', json={'question': 'q', 'selection': {'text': 'x' * 2001}})
    assert too_long.status_code == 422


def test_document_ask_endpoint_keeps_its_contract(lib):
    physics = lib.add('mechanics.pdf', 'Rigid body rotation.')
    data = lib.client.post('/api/v1/documents/ask', json={'document_id': physics, 'question': 'rotation?'}).json()
    assert data['document_id'] == physics and data['answer'] == 'Grounded answer [1].'


@pytest.mark.parametrize('raw, mode, expected', [
    ('Plain answer [1].', 'grounded', [('library', 'Plain answer [1].')]),
    ('Plain answer.', 'open', [('unlabeled', 'Plain answer.')]),
    ('Intro.\n[LIBRARY]\nA [1]\n[MODEL]: B\n[MODEL]\nC', 'open',
     [('unlabeled', 'Intro.'), ('library', 'A [1]'), ('model', 'B\n\nC')]),
    ('  [LIBRARY]  \n\n[MODEL]\nOnly model', 'open', [('model', 'Only model')]),
    ('', 'open', []),
])
def test_split_segments(raw, mode, expected):
    assert [(s.source_type, s.text) for s in split_segments(raw, mode)] == expected


def test_invalid_citations():
    assert invalid_citations('See [1], [2, 5] and [0]. Not a citation: [x].', 3) == [0, 5]
