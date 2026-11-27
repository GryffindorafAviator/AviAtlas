"""Uploading and reading Markdown/TXT end to end, with real parsers and chunker."""
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.main import app
from aviatlas.chunking.structured_chunker import StructuredChunker
from aviatlas.parsing.router import FormatRouter
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.services.collections import CollectionService
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.keyword_index import SQLiteKeywordIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository

pytestmark = pytest.mark.real_formats


class Encoder:
    def encode(self, texts, **kwargs):
        return np.array([[1.0, 0.0] for _ in texts])


@pytest.fixture
def client(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    vectors, embedder, keywords = InMemoryVectorIndex(), SentenceTransformerEmbedder('test', Encoder()), SQLiteKeywordIndex(repo)
    router = FormatRouter(lambda: pytest.fail('Docling must not load for text formats'))
    docs = DocumentService(repo, vectors, tmp_path / 'uploads', lambda: router, StructuredChunker(), embedder, keywords)
    retriever = HybridRetriever(DenseRetriever(repo, embedder, vectors), KeywordRetriever(repo, keywords))
    resolver = ScopeResolver(repo, collections)
    services = Services(docs, QuestionAnsweringService(repo, retriever, SimpleNamespace(), resolver), collections,
                        SearchService(retriever, resolver))
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as c:
        yield SimpleNamespace(http=c, docs=docs, repo=repo)
    app.dependency_overrides.pop(get_services, None)


def upload(client, name, data):
    return client.http.post('/api/v1/documents/upload', files={'file': (name, data)})


NOTES = '# 刚体\n\n## 角速度\n\n角速度描述转动快慢。\n\n$$\n\\omega = d\\theta / dt\n$$\n\n| 量 | 单位 |\n|---|---|\n| 角速度 | rad/s |\n'


def test_markdown_upload_is_searchable_and_readable_by_block(client):
    response = upload(client, 'notes.md', NOTES.encode())
    assert response.status_code == 200, response.text
    doc = response.json()['document_id']
    summary = client.http.get(f'/api/v1/documents/{doc}').json()
    assert (summary['status'], summary['source_format'], summary['title'], summary['page_count']) == ('indexed', 'markdown', '刚体', None)
    assert summary['needs_reprocess'] is False and summary['ocr_page_count'] == 0

    content = client.http.get(f'/api/v1/documents/{doc}/content').json()
    assert [b['type'] for b in content['blocks']] == ['heading', 'heading', 'paragraph', 'equation', 'table']
    assert content['blocks'][3]['content'] == '$$\n\\omega = d\\theta / dt\n$$' and content['blocks'][0]['page_number'] is None

    hits = client.http.post('/api/v1/search', json={'query': 'rad/s 单位', 'method': 'bm25'}).json()['results']
    table = next(h for h in hits if '| 角速度 | rad/s |' in h['content'])
    assert table['page_range'] is None and table['section_title'] == '角速度'
    assert table['block_ids'] == [content['blocks'][4]['block_id']]

    original = client.http.get(f'/api/v1/documents/{doc}/file')
    assert original.headers['content-type'].startswith('text/markdown')
    assert original.headers['content-disposition'].startswith('attachment') and original.content == NOTES.encode()


def test_gbk_text_upload(client):
    doc = upload(client, '笔记.txt', '第一段。\n\n第二段讲力矩。'.encode('gbk')).json()['document_id']
    blocks = client.http.get(f'/api/v1/documents/{doc}/content').json()['blocks']
    assert [b['content'] for b in blocks] == ['第一段。', '第二段讲力矩。']


@pytest.mark.parametrize('name,data,message', [
    ('fake.pdf', b'not a pdf', 'not a valid PDF'),
    ('fake.docx', b'not a zip', 'not a valid DOCX'),
    ('old.doc', b'\xd0\xcf\x11\xe0', 'Supported formats'),
    ('binary.txt', b'\x00\x01\x02', 'not a text file'),
])
def test_mismatched_content_is_rejected_and_not_kept(client, name, data, message):
    response = upload(client, name, data)
    assert response.status_code == 400 and message in response.json()['detail']
    assert client.repo.all_records() == []
    assert not any(client.docs.upload_dir.glob('*'))


def test_documents_from_an_older_chunker_are_flagged(client):
    doc = upload(client, 'a.txt', b'alpha').json()['document_id']
    record = client.repo.get(doc)
    record.metadata.pop('chunker')
    client.repo.save(record)
    assert client.http.get(f'/api/v1/documents/{doc}').json()['needs_reprocess'] is True
