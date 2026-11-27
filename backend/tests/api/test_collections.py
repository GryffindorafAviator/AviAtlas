"""Collection workflows with real SQLite, without parsers or model calls."""
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from aviatlas.api.main import app
from aviatlas.api.dependencies import Services, get_services
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.services.collections import CollectionService
from aviatlas.services.documents import DocumentService
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex


@pytest.fixture
def client(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    docs = DocumentService(repo, InMemoryVectorIndex(), tmp_path / 'uploads',
                           lambda: None, GreedyChunker(), None)
    services = Services(docs, SimpleNamespace(), collections)
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as client:
        client.repo = repo
        yield client
    app.dependency_overrides.pop(get_services, None)


def create(client, name='Physics'):
    response = client.post('/api/v1/collections', json={'name': name})
    assert response.status_code == 201
    return response.json()['collection_id']


def upload(client, collection=None):
    params = {'auto_process': False}
    if collection:
        params['collection_id'] = collection
    response = client.post('/api/v1/documents/upload', params=params,
                           files={'file': ('book.pdf', b'%PDF-test')})
    assert response.status_code == 200
    return response.json()['document_id']


def test_memberships_filter_pagination_and_no_document_mutation(client):
    a, b = create(client), create(client, 'Mathematics')
    first, second, outside = upload(client, a), upload(client, a), upload(client)
    snapshot = client.repo.get(first).model_dump_json()
    response = client.put(f'/api/v1/documents/{first}/collections', json={'collection_ids': [a, b, b]})
    assert response.status_code == 200
    assert set(response.json()['collection_ids']) == {a, b}
    assert client.repo.get(first).model_dump_json() == snapshot
    assert set(client.get(f'/api/v1/documents/{first}').json()['collection_ids']) == {a, b}
    result = client.get('/api/v1/documents', params={'collection_id': a, 'limit': 1, 'offset': 1}).json()
    assert result['total'] == 2
    assert len(result['items']) == 1
    assert result['items'][0]['document_id'] == first
    assert client.get('/api/v1/documents', params={'collection_id': b}).json()['total'] == 1
    assert {c['name']: c['document_count'] for c in client.get('/api/v1/collections').json()['items']} == {'Physics': 2, 'Mathematics': 1}
    assert client.put(f'/api/v1/documents/{first}/collections', json={'collection_ids': []}).json() == {'collection_ids': []}
    assert client.get('/api/v1/documents', params={'collection_id': b}).json()['total'] == 0
    assert client.get('/api/v1/documents').json()['total'] == 3


def test_rename_restart_and_delete_preserve_files(client):
    collection = create(client, '  Physics  ')
    doc_id = upload(client, collection)
    path = client.repo.get(doc_id).filepath
    assert client.patch(f'/api/v1/collections/{collection}', json={'name': 'Mechanics'}).status_code == 200
    reopened = SQLiteCollectionRepository(SQLiteDocumentRepository(client.repo.database_path))
    assert reopened.get(UUID(collection)).name == 'Mechanics'
    assert reopened.collection_ids(UUID(doc_id)) == [UUID(collection)]
    assert client.delete(f'/api/v1/collections/{collection}').status_code == 204
    assert client.get(f'/api/v1/documents/{doc_id}/file').content == b'%PDF-test'
    assert path.exists()
    assert reopened.collection_ids(UUID(doc_id)) == []
    assert client.get('/api/v1/documents').json()['total'] == 1
    assert client.get('/api/v1/documents', params={'collection_id': collection}).status_code == 404


def test_document_delete_cleans_memberships(client):
    collection = create(client)
    doc_id = upload(client, collection)
    assert client.delete(f'/api/v1/documents/{doc_id}').status_code == 204
    assert client.get('/api/v1/collections').json()['items'][0]['document_count'] == 0
    with client.repo.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM document_collections').fetchone()[0] == 0


def test_invalid_changes_are_atomic_and_unknown_upload_does_not_create_document(client):
    collection = create(client)
    doc_id = upload(client, collection)
    missing = str(uuid4())
    assert client.put(f'/api/v1/documents/{doc_id}/collections', json={'collection_ids': [missing]}).status_code == 404
    assert client.get(f'/api/v1/documents/{doc_id}/collections').json()['collection_ids'] == [collection]
    assert client.put(f'/api/v1/documents/{missing}/collections', json={'collection_ids': [collection]}).status_code == 404
    response = client.post('/api/v1/documents/upload', params={'collection_id': missing}, files={'file': ('bad.pdf', b'%PDF')})
    assert response.status_code == 404
    assert client.get('/api/v1/documents').json()['total'] == 1
    assert client.post('/api/v1/collections', json={'name': 'physics'}).status_code == 400
    assert client.post('/api/v1/collections', json={'name': '   '}).status_code == 400
    assert client.patch(f'/api/v1/collections/{missing}', json={'name': 'New'}).status_code == 404
    assert client.delete(f'/api/v1/collections/{missing}').status_code == 404
    assert client.get('/api/v1/documents', params={'collection_id': 'invalid'}).status_code == 422
    other = create(client, 'Other')
    assert client.patch(f'/api/v1/collections/{other}', json={'name': 'Physics'}).status_code == 400
    assert client.get('/api/v1/collections').json()['items'][1]['name'] == 'Other'
