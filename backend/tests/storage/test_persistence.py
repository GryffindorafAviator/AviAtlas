"""Real SQLite/Chroma integration with deterministic, network-free embeddings."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import subprocess
import sys

import numpy as np
import pytest

from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.models import (
    Block, BlockType, BoundingBox, DocumentMetadata, ParsedDocument, ProvenanceItem,
)
from aviatlas.retrieval.semantic_retriever import SemanticRetriever
from aviatlas.services.documents import DocumentService
from aviatlas.services.errors import DocumentNotReady, DocumentProcessingFailed
from aviatlas.services.qa import QuestionAnsweringService
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.persistent_indexes import PersistentIndexStore


class Encoder:
    def encode(self, texts, **kwargs):
        return np.array([[1., 0.] if 'plant' in text else [0., 1.] for text in texts])


def setup_services(directory, monkeypatch):
    monkeypatch.setenv('EMBEDDING_MODEL', 'test-model-v1')
    repo = SQLiteDocumentRepository(directory / 'documents.sqlite3')
    indexes = PersistentIndexStore(repo, directory / 'chroma', encoder_factory=lambda model: Encoder())
    def parse(path):
        block = Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0,
                      content=path.read_text(), provenance=[ProvenanceItem(
                          page_number=3, bbox=BoundingBox(x0=.1,y0=.2,x1=.8,y1=.7))])
        return ParsedDocument(metadata=DocumentMetadata(page_count=3), blocks=[block])
    parser = SimpleNamespace(parse=parse)
    docs = DocumentService(repo, indexes, directory / 'uploads', lambda: parser,
                           GreedyChunker(), lambda chunks, blocks: SemanticRetriever(chunks, blocks, Encoder()))
    qa = QuestionAnsweringService(repo, indexes, SimpleNamespace(generate=lambda query, sources: sources[0].content+' [1]'))
    return docs, qa, parser


def test_documents_citations_and_vectors_reopen_without_reembedding(tmp_path, monkeypatch):
    docs, qa, _ = setup_services(tmp_path, monkeypatch)
    first = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    second = docs.upload('second.pdf', BytesIO(b'rock evidence'))
    for record in [first, second]: docs.parse(record.id)
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    calls = []
    class QueryEncoder(Encoder):
        def encode(self, texts, **kwargs):
            calls.append(texts)
            return super().encode(texts, **kwargs)
    indexes = PersistentIndexStore(repo, tmp_path / 'chroma', encoder_factory=lambda model: QueryEncoder())
    restarted = QuestionAnsweringService(repo, indexes, qa.answerer)
    for record in [first, second]:
        result = restarted.ask(record.id, 'plant question')
        assert result.sources[0].document_id == record.id
        assert result.sources[0].content == record.filepath.read_text()
        assert result.sources[0].page_range == (3,3)
        assert result.sources[0].provenance[0].bbox.y0 == .2
    assert calls == [['plant question'], ['plant question']]
    assert repo.get(first.id).indexed_at is not None
    assert repo.get(first.id).metadata['page_count'] == 3


def test_reparse_replaces_index_and_failure_survives_restart(tmp_path, monkeypatch):
    docs, qa, parser = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.parse(record.id)
    old_hit = qa.ask(record.id, 'plant').sources[0]
    record.filepath.write_text('new rock evidence')
    docs.parse(record.id)
    new_hit = qa.ask(record.id, 'rock').sources[0]
    assert new_hit.chunk_id != old_hit.chunk_id
    assert new_hit.content == 'new rock evidence'
    assert len(docs.indexes.client.list_collections()) == 1
    def fail(path): raise ValueError('broken')
    parser.parse = fail
    with pytest.raises(DocumentProcessingFailed): docs.parse(record.id)
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    assert repo.get(record.id).status == 'failed'
    assert PersistentIndexStore(repo, tmp_path / 'chroma').get(record.id) is None
    assert len(docs.indexes.client.list_collections()) == 0


def test_changed_model_and_missing_collection_require_reparse(tmp_path, monkeypatch):
    docs, qa, _ = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.parse(record.id)
    changed = PersistentIndexStore(docs.repository, tmp_path / 'chroma', model_name='test-model-v2')
    with pytest.raises(DocumentNotReady, match='model changed'): changed.get(record.id)
    name = docs.indexes.client.list_collections()[0].name
    docs.indexes.client.delete_collection(name)
    with pytest.raises(DocumentNotReady, match='missing'): qa.ask(record.id, 'plant')


def test_interrupted_indexing_is_failed_on_recovery(tmp_path, monkeypatch):
    docs, _, _ = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.parse(record.id)
    saved = docs.repository.get(record.id)
    saved.set_status('indexing')
    docs.repository.save(saved)
    docs.indexes.recover_interrupted()
    assert docs.repository.get(record.id).status == 'failed'
    assert docs.indexes.get(record.id) is None


def test_upload_save_failure_is_durable(tmp_path, monkeypatch):
    docs, _, _ = setup_services(tmp_path, monkeypatch)
    docs.upload_dir = tmp_path / 'not-a-directory'
    docs.upload_dir.write_text('occupied')
    with pytest.raises(DocumentProcessingFailed): docs.upload('fail.pdf', BytesIO(b'PDF'))
    reopened = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    assert reopened.all_records()[0].status == 'failed'


def test_real_process_restart(tmp_path):
    # Each invocation has a fresh interpreter and fresh Chroma client.
    script = tmp_path / 'worker.py'
    script.write_text('''
import sys, json, os
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4, UUID
import numpy as np
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.persistent_indexes import PersistentIndexStore
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.retrieval.semantic_retriever import SemanticRetriever
from aviatlas.parsing.models import Block, BlockType, DocumentMetadata, ParsedDocument, ProvenanceItem, BoundingBox
os.environ['EMBEDDING_MODEL']='test-process-model'
class Encoder:
 def encode(self, texts, **kwargs): return np.array([[1.,0.] for text in texts])
root=Path(sys.argv[1]); mode=sys.argv[2]
repo=SQLiteDocumentRepository(root/'documents.sqlite3')
indexes=PersistentIndexStore(repo,root/'chroma',encoder_factory=lambda model: Encoder())
if mode=='write':
 block=Block(id=uuid4(),type=BlockType.PARAGRAPH,order=0,content='persistent evidence',provenance=[ProvenanceItem(page_number=2,bbox=BoundingBox(x0=0,y0=0,x1=1,y1=1))])
 parser=SimpleNamespace(parse=lambda path:ParsedDocument(metadata=DocumentMetadata(page_count=2),blocks=[block]))
 docs=DocumentService(repo,indexes,root/'uploads',lambda:parser,GreedyChunker(),lambda chunks,blocks:SemanticRetriever(chunks,blocks,Encoder()))
 record=docs.upload('book.pdf',BytesIO(b'%PDF-synthetic'))
 docs.parse(record.id)
 print(json.dumps({'id':str(record.id)}))
else:
 os.environ['AVIATLAS_DATA_DIR']=str(root)
 from aviatlas.api.dependencies import get_services
 services=get_services()
 services.qa.indexes.encoder_factory=lambda model: Encoder()
 services.qa.answerer=SimpleNamespace(generate=lambda q,s:'answer [1]')
 result=services.qa.ask(UUID(sys.argv[3]),'question')
 print(json.dumps({'answer':result.answer,'pages':result.sources[0].page_range,'content':result.sources[0].content}))
''')
    def run(*args):
        result = subprocess.run([sys.executable, str(script), str(tmp_path), *args],
                                capture_output=True, text=True, timeout=60, check=True)
        return json.loads(result.stdout.strip().splitlines()[-1])
    created = run('write')
    restored = run('read', created['id'])
    assert restored == {'answer': 'answer [1]', 'pages': [2,2], 'content': 'persistent evidence'}


def test_delete_removes_pdf_record_and_vectors_after_restart(tmp_path, monkeypatch):
    docs, _, _ = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.parse(record.id)
    docs.delete(record.id)
    reopened = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    indexes = PersistentIndexStore(reopened, tmp_path / 'chroma')
    assert reopened.get(record.id) is None
    assert indexes.get(record.id) is None
    assert indexes.client.list_collections() == []
    assert not record.filepath.exists()


def test_queued_recovery_and_interrupted_deletion(tmp_path, monkeypatch):
    docs, _, _ = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.enqueue(record.id)
    docs.indexes.recover_interrupted()
    assert docs.get(record.id).status == 'failed'
    docs.parse(record.id)
    saved = docs.get(record.id)
    saved.set_status('deleting')
    docs.repository.save(saved)
    reopened, _, _ = setup_services(tmp_path, monkeypatch)
    reopened.resume_deletions()
    assert reopened.repository.get(record.id) is None
    assert reopened.indexes.get(record.id) is None
    assert not record.filepath.exists()


def test_delete_failure_preserves_manifest_for_retry(tmp_path, monkeypatch):
    docs, _, _ = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    docs.parse(record.id)
    original = docs.indexes.client.delete_collection
    def fail(name): raise RuntimeError('temporarily unavailable')
    monkeypatch.setattr(docs.indexes.client, 'delete_collection', fail)
    with pytest.raises(DocumentProcessingFailed): docs.delete(record.id)
    assert docs.get(record.id).status == 'deleting'
    assert docs.indexes._manifest(record.id) is not None
    monkeypatch.setattr(docs.indexes.client, 'delete_collection', original)
    docs.delete(record.id)
    assert docs.repository.get(record.id) is None
    assert docs.indexes.client.list_collections() == []


def test_processing_blocks_delete_and_duplicate_submission(tmp_path, monkeypatch):
    from threading import Event, Thread
    from aviatlas.services.errors import DocumentBusy
    docs, _, parser = setup_services(tmp_path, monkeypatch)
    record = docs.upload('first.pdf', BytesIO(b'plant evidence'))
    started, release = Event(), Event()
    original = parser.parse
    def wait_parse(path):
        started.set()
        assert release.wait(timeout=10)
        return original(path)
    parser.parse = wait_parse
    docs.enqueue(record.id)
    worker = Thread(target=docs.process_queued, args=(record.id,))
    worker.start()
    try:
        assert started.wait(timeout=10)
        assert docs.get(record.id).status == 'parsing'
        with pytest.raises(DocumentBusy): docs.delete(record.id)
        with pytest.raises(DocumentBusy): docs.enqueue(record.id)
        with pytest.raises(DocumentBusy): docs.parse(record.id)
    finally:
        release.set()
        worker.join(timeout=15)
    assert not worker.is_alive()
    assert docs.get(record.id).status == 'indexed'
