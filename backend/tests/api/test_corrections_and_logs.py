"""Chunk corrections (FR-LIB-11, System Design §7.3) and Q&A logs."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.answering.generator import AnswerGenerationError
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.main import app
from aviatlas.chunking.structured_chunker import StructuredChunker
from aviatlas.parsing.router import FormatRouter
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.services.collections import CollectionService
from aviatlas.services.conversations import ConversationService
from aviatlas.services.corrections import CorrectionService
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.conversation_repository import SQLiteConversationRepository
from aviatlas.storage.correction_repository import SQLiteCorrectionRepository
from aviatlas.storage.keyword_index import SQLiteKeywordIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.qa_log_repository import SQLiteQALogRepository
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository

pytestmark = pytest.mark.real_formats
TOPICS = ['rotation', 'entropy', 'photosynthesis', 'torque']


class Encoder:
    def __init__(self):
        self.encoded = []

    def encode(self, texts, **kwargs):
        self.encoded.extend(texts)
        rows = [[float(t in x.lower()) for t in TOPICS] + [.01] for x in texts]
        return np.array([np.array(r) / np.linalg.norm(r) for r in rows])


class Answerer:
    def __init__(self):
        self.fail = False

    def model_label(self):
        return 'test/model'

    def generate(self, question, sources, **kwargs):
        if self.fail:
            raise AnswerGenerationError('Answer generation failed. Check provider configuration and retry.')
        return 'Answer [1].'

    def stream(self, question, sources, **kwargs):
        yield 'Answer '
        yield '[1].'

    def condense(self, question, history):
        return 'standalone ' + question


# Two sections, each its own chunk (min_chars is reached before the second heading).
NOTES = ('# Mechanics\n\n## Rotation\n\n' + 'Rotation of rigid bodies about a fixed axis. ' * 6 +
         '\n\n## Heat\n\n' + 'Entropy always increases in an isolated system. ' * 6 + '\n')


@pytest.fixture
def lib(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    encoder = Encoder()
    vectors, embedder, keywords = InMemoryVectorIndex(), SentenceTransformerEmbedder('test', encoder), SQLiteKeywordIndex(repo)
    corrections, logs = SQLiteCorrectionRepository(repo), SQLiteQALogRepository(repo, keep=5)
    router = FormatRouter(lambda: pytest.fail('no Docling for Markdown'))
    docs = DocumentService(repo, vectors, tmp_path / 'uploads', lambda: router, StructuredChunker(), embedder,
                           keywords, corrections=corrections)
    retriever = HybridRetriever(DenseRetriever(repo, embedder, vectors), KeywordRetriever(repo, keywords))
    resolver = ScopeResolver(repo, collections)
    answerer = Answerer()
    services = Services(docs, QuestionAnsweringService(repo, retriever, answerer, resolver, logs=logs), collections,
                        SearchService(retriever, resolver),
                        ConversationService(SQLiteConversationRepository(repo), retriever, answerer, resolver, logs=logs),
                        CorrectionService(docs, corrections), logs)
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as http:
        def add(text=NOTES, name='notes.md'):
            response = http.post('/api/v1/documents/upload', files={'file': (name, text.encode())})
            assert response.status_code == 200, response.text
            return response.json()['document_id']

        def chunks(doc):
            return http.get(f'/api/v1/documents/{doc}/chunks').json()

        def patch(doc, chunk, **body):
            return http.patch(f'/api/v1/documents/{doc}/chunks/{chunk}', json=body)

        def search(doc, query, method='bm25'):
            return http.post('/api/v1/search', json={'query': query, 'method': method, 'document_ids': [doc]}).json()['results']

        def reprocess(doc, text=None):
            if text is not None:
                repo.get(doc).filepath.write_text(text)
            docs.parse(UUID(doc))

        yield SimpleNamespace(http=http, add=add, chunks=chunks, patch=patch, search=search, reprocess=reprocess,
                              docs=docs, encoder=encoder, answerer=answerer, vectors=vectors, logs=logs, repo=repo)
    app.dependency_overrides.pop(get_services, None)


def heat_chunk(view):
    return next(c for c in view['chunks'] if 'Entropy' in c['content'])


def test_edits_and_exclusions_change_retrieval_and_reset_restores_it(lib):
    doc = lib.add()
    view = lib.chunks(doc)
    assert len(view['chunks']) == 2 and view['orphaned'] == [] and not any(c['edited'] for c in view['chunks'])
    heat = heat_chunk(view)
    lib.encoder.encoded.clear()

    edited = lib.patch(doc, heat['chunk_id'], text='## Heat\nTorque and entropy, corrected by hand.').json()
    assert edited['edited'] and edited['original'] == heat['original'] and edited['content'].startswith('## Heat\nTorque')
    # Only the edited chunk was embedded again.
    assert lib.encoder.encoded == ['## Heat\nTorque and entropy, corrected by hand.']
    assert lib.search(doc, 'torque')[0]['chunk_id'] == heat['chunk_id']
    assert lib.search(doc, 'isolated') == []
    assert lib.search(doc, 'torque', method='dense')[0]['content'].startswith('## Heat\nTorque')

    excluded = lib.patch(doc, heat['chunk_id'], excluded=True).json()
    assert excluded['excluded'] and excluded['edited']  # exclusion keeps the edit
    assert lib.search(doc, 'torque') == [] and all(r['chunk_id'] != heat['chunk_id'] for r in lib.search(doc, 'entropy', 'dense'))
    summary = lib.http.get(f'/api/v1/documents/{doc}').json()
    assert (summary['edited_chunks'], summary['excluded_chunks'], summary['pending_corrections']) == (1, 1, 0)

    lib.patch(doc, heat['chunk_id'], excluded=False, text=None)
    restored = heat_chunk(lib.chunks(doc))
    assert not restored['edited'] and not restored['excluded'] and restored['correction_id'] is None
    assert lib.search(doc, 'isolated')[0]['chunk_id'] == heat['chunk_id']


def test_corrections_survive_reprocessing(lib):
    doc = lib.add()
    heat = heat_chunk(lib.chunks(doc))
    lib.patch(doc, heat['chunk_id'], text='Corrected torque text.')
    lib.reprocess(doc)
    after = lib.chunks(doc)
    moved = next(c for c in after['chunks'] if c['edited'])
    assert moved['chunk_id'] != heat['chunk_id'] and moved['content'] == 'Corrected torque text.'
    assert after['orphaned'] == [] and lib.search(doc, 'torque')[0]['chunk_id'] == moved['chunk_id']


def test_corrections_whose_text_changed_are_surfaced_and_can_be_attached_or_dismissed(lib):
    doc = lib.add()
    heat = heat_chunk(lib.chunks(doc))
    rotation = next(c for c in lib.chunks(doc)['chunks'] if 'Rotation of' in c['content'])
    lib.patch(doc, heat['chunk_id'], text='Corrected torque text.')
    lib.patch(doc, rotation['chunk_id'], excluded=True)
    # The source now says something else in both sections; the headings are unchanged.
    lib.reprocess(doc, NOTES.replace('always increases', 'never decreases').replace('fixed axis', 'moving axis'))
    view = lib.chunks(doc)
    assert not any(c['edited'] or c['excluded'] for c in view['chunks'])
    orphans = {o['text'] or 'excluded': o for o in view['orphaned']}
    assert set(orphans) == {'Corrected torque text.', 'excluded'}
    new_heat = heat_chunk(view)
    # The heading block is shared, so the new chunk is offered as the place to reapply.
    assert orphans['Corrected torque text.']['candidate_chunk_ids'] == [new_heat['chunk_id']]
    assert orphans['Corrected torque text.']['context']['section_title'] == 'Heat'
    assert lib.http.get(f'/api/v1/documents/{doc}').json()['pending_corrections'] == 2
    assert lib.search(doc, 'torque') == []

    attached = lib.http.post(f"/api/v1/documents/{doc}/corrections/{orphans['Corrected torque text.']['correction_id']}/attach",
                             json={'chunk_id': new_heat['chunk_id']})
    assert attached.status_code == 200 and attached.json()['content'] == 'Corrected torque text.'
    assert lib.search(doc, 'torque')[0]['chunk_id'] == new_heat['chunk_id']
    assert lib.http.delete(f"/api/v1/documents/{doc}/corrections/{orphans['excluded']['correction_id']}").status_code == 204
    assert lib.chunks(doc)['orphaned'] == []
    # Attached corrections follow the next reprocess like any other.
    lib.reprocess(doc)
    assert next(c for c in lib.chunks(doc)['chunks'] if c['edited'])['content'] == 'Corrected torque text.'


def test_edit_validation_and_busy_documents(lib):
    doc = lib.add()
    heat = heat_chunk(lib.chunks(doc))
    assert lib.patch(doc, heat['chunk_id'], text='   ').status_code == 400
    assert lib.patch(doc, heat['chunk_id']).status_code == 422
    assert lib.patch(doc, str(uuid4()), excluded=True).status_code == 404
    # Saving the generated text back is not an edit.
    assert not lib.patch(doc, heat['chunk_id'], text=heat['original'] + '  ').json()['edited']
    with lib.docs._lock_for(UUID(doc)):
        assert lib.patch(doc, heat['chunk_id'], excluded=True).status_code == 409


def test_failed_index_write_leaves_the_chunk_unchanged(lib, monkeypatch):
    doc = lib.add()
    heat = heat_chunk(lib.chunks(doc))
    original_replace = lib.vectors.replace
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('disk full')
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(lib.vectors, 'replace', flaky)
    assert lib.patch(doc, heat['chunk_id'], text='Torque.').status_code == 500
    unchanged = heat_chunk(lib.chunks(doc))
    assert not unchanged['edited'] and unchanged['correction_id'] is None
    assert lib.search(doc, 'isolated')[0]['chunk_id'] == heat['chunk_id']


def test_answers_are_logged_with_references_not_excerpts(lib):
    doc = lib.add()
    assert lib.http.post('/api/v1/ask', json={'question': 'What is entropy?'}).status_code == 200
    lib.answerer.fail = True
    assert lib.http.post('/api/v1/ask', json={'question': 'Broken?'}).status_code == 502
    lib.answerer.fail = False
    empty = lib.http.post('/api/v1/collections', json={'name': 'Empty'}).json()['collection_id']
    lib.http.post('/api/v1/ask', json={'question': 'Nothing?', 'collection_id': empty})
    cid = lib.http.post('/api/v1/conversations', json={'document_ids': [doc]}).json()['conversation_id']
    lib.http.post(f'/api/v1/conversations/{cid}/messages', json={'question': 'What is rotation?'})
    lib.http.post(f'/api/v1/conversations/{cid}/messages', json={'question': 'And torque?'})

    listing = lib.http.get('/api/v1/logs').json()
    assert listing['total'] == 5
    assert [(i['kind'], i['status']) for i in listing['items']] == [
        ('conversation', 'complete'), ('conversation', 'complete'), ('ask', 'no_evidence'), ('ask', 'failed'), ('ask', 'complete')]
    failed = next(i for i in listing['items'] if i['status'] == 'failed')
    assert 'Answer generation failed' in failed['error'] and failed['model'] == 'test/model'

    ask = lib.http.get(f"/api/v1/logs/{listing['items'][-1]['log_id']}").json()
    assert ask['answer'] == 'Answer [1].' and ask['search_query'] == 'What is entropy?'
    assert set(ask['timings_ms']) == {'retrieval', 'generation', 'total'}
    source = ask['sources'][0]
    assert source['citation'] == 1 and source['document_name'] == 'notes.md' and 'ranks' in source
    assert 'content' not in source
    follow_up = lib.http.get(f"/api/v1/logs/{listing['items'][0]['log_id']}").json()
    assert follow_up['search_query'] == 'standalone And torque?' and follow_up['conversation_id'] == cid
    assert {'retrieval', 'first_token', 'total'} <= set(follow_up['timings_ms'])

    assert [i['status'] for i in lib.http.get('/api/v1/logs?status=failed').json()['items']] == ['failed']
    assert lib.http.delete(f"/api/v1/logs/{failed['log_id']}").status_code == 204
    lib.http.post('/api/v1/ask', json={'question': 'Again?'})
    lib.http.post('/api/v1/ask', json={'question': 'Once more?'})
    assert lib.http.get('/api/v1/logs').json()['total'] == 5  # keep=5: oldest dropped
    assert lib.http.delete('/api/v1/logs').status_code == 204
    assert lib.http.get('/api/v1/logs').json()['total'] == 0


def test_searches_during_an_index_switch_wait_and_see_one_consistent_version(lib, monkeypatch):
    import threading
    doc = lib.add()
    lib.repo.get(doc).filepath.write_text(NOTES.replace('Entropy always increases', 'Photosynthesis stores energy'))
    switched, release = threading.Event(), threading.Event()
    original = lib.docs._write_keywords

    def slow_keywords(document_id, chunks):
        switched.set()            # vectors already point at the new chunks
        assert release.wait(5)
        original(document_id, chunks)

    monkeypatch.setattr(lib.docs, '_write_keywords', slow_keywords)
    rebuild = threading.Thread(target=lib.reprocess, args=(doc,))
    rebuild.start()
    assert switched.wait(5)
    results = {}
    search = threading.Thread(target=lambda: results.update(r=lib.http.post(
        '/api/v1/search', json={'query': 'photosynthesis', 'method': 'hybrid', 'document_ids': [doc]})))
    search.start()
    search.join(0.3)
    assert search.is_alive()      # waiting for the switch, not failing on a half-written index
    release.set()
    rebuild.join(5)
    search.join(5)
    assert results['r'].status_code == 200
    assert 'Photosynthesis stores energy' in results['r'].json()['results'][0]['content']


def test_index_guard_lets_readers_share_and_writers_go_first():
    import threading
    import time
    from aviatlas.retrieval.guard import IndexGuard
    guard, order, release_writer = IndexGuard(), [], threading.Event()

    def writer():
        with guard.write():
            order.append('write')
            assert release_writer.wait(5)

    def late_reader():
        with guard.read():
            order.append('read')

    threads = [threading.Thread(target=writer, daemon=True), threading.Thread(target=late_reader, daemon=True)]
    with guard.read():
        with guard.read():  # readers share the guard
            pass
        threads[0].start()
        time.sleep(0.05)
        threads[1].start()
        time.sleep(0.05)
        assert order == []  # the writer waits for the active reader; the late reader queues behind the writer
    time.sleep(0.05)
    assert order == ['write']
    release_writer.set()
    for t in threads:
        t.join(1)
    assert order == ['write', 'read']
