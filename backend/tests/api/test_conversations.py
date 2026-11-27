"""Conversations: streamed turns, follow-up context, cancellation, and deletion policy."""
import json
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.answering.generator import AnswerGenerationError
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.main import app
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.orchestration.turn_graph import _fit
from aviatlas.parsing.models import Block, BlockType, BoundingBox, DocumentMetadata, ParsedDocument, ProvenanceItem
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.services.collections import CollectionService
from aviatlas.services.conversations import INTERRUPTED, ConversationService
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import NO_DOCUMENTS, QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.conversation_repository import SQLiteConversationRepository
from aviatlas.storage.keyword_index import SQLiteKeywordIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository

TOPICS = ['rotation', 'photosynthesis', 'entropy']


class Encoder:
    def encode(self, texts, **kwargs):
        rows = [[float(topic in t.lower()) for topic in TOPICS] + [.01] for t in texts]
        return np.array([np.array(r) / np.linalg.norm(r) for r in rows])


class Answerer:
    def __init__(self):
        self.streams, self.condensed = [], []
        self.chunks = ['Rotation is ', 'described [1].']
        self.rewrite = None          # str, or an exception to raise
        self.fail = False
        self.gate = None             # Event: block after the first chunk until set

    def generate(self, question, sources, **kwargs):
        return ''.join(self.chunks)

    def stream(self, question, sources, **kwargs):
        self.streams.append(SimpleNamespace(question=question, sources=sources, **kwargs))
        for i, chunk in enumerate(self.chunks):
            if self.fail and i == 1:
                raise AnswerGenerationError('Answer generation was interrupted. Please retry.')
            yield chunk
            if self.gate is not None and i == 0:
                assert self.gate.wait(5)

    def condense(self, question, history):
        self.condensed.append((question, list(history)))
        if isinstance(self.rewrite, Exception):
            raise self.rewrite
        return self.rewrite or question


def events(response):
    assert response.headers['content-type'].startswith('text/event-stream')
    out = []
    for block in response.text.strip().split('\n\n'):
        name, data = block.split('\n', 1)
        out.append((name.removeprefix('event: '), json.loads(data.removeprefix('data: '))))
    return out


@pytest.fixture
def lib(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    vectors, embedder = InMemoryVectorIndex(), SentenceTransformerEmbedder('test', Encoder())

    def parse(path):
        return ParsedDocument(metadata=DocumentMetadata(page_count=2), blocks=[
            Block(id=uuid4(), type=BlockType.PARAGRAPH, order=0, content=path.read_text(),
                  provenance=[ProvenanceItem(page_number=2, bbox=BoundingBox(x0=0, y0=0, x1=1, y1=1))])])

    keywords = SQLiteKeywordIndex(repo)
    docs = DocumentService(repo, vectors, tmp_path / 'uploads', lambda: SimpleNamespace(parse=parse),
                           GreedyChunker(), embedder, keywords)
    retriever = HybridRetriever(DenseRetriever(repo, embedder, vectors), KeywordRetriever(repo, keywords))
    resolver = ScopeResolver(repo, collections)
    answerer = Answerer()
    conversations = ConversationService(SQLiteConversationRepository(repo), retriever, answerer, resolver)
    services = Services(docs, QuestionAnsweringService(repo, retriever, answerer, resolver), collections,
                        SearchService(retriever, resolver), conversations)

    def add(name, text):
        record = docs.upload(name, BytesIO(text.encode()))
        docs.parse(record.id)
        return str(record.id)

    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as client:
        def create(**scope):
            response = client.post('/api/v1/conversations', json=scope)
            assert response.status_code == 201, response.text
            return response.json()['conversation_id']

        def say(conversation_id, question, **body):
            return events(client.post(f'/api/v1/conversations/{conversation_id}/messages',
                                      json={'question': question, **body}))

        yield SimpleNamespace(client=client, add=add, create=create, say=say, answerer=answerer,
                              conversations=conversations, docs=docs, repo=repo)
    app.dependency_overrides.pop(get_services, None)


def test_turn_streams_events_and_saves_both_messages(lib):
    doc = lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    cid = lib.create()
    stream = lib.say(cid, 'What is rotation?')
    assert [name for name, _ in stream] == ['started', 'retrieval', 'delta', 'delta', 'done']
    started, retrieval = stream[0][1], stream[1][1]
    assert started['user']['content'] == 'What is rotation?' and started['assistant']['status'] == 'streaming'
    assert retrieval['search_query'] == 'What is rotation?'
    assert retrieval['sources'][0]['document_id'] == doc and retrieval['sources'][0]['citation'] == 1
    assert ''.join(d['text'] for n, d in stream if n == 'delta') == 'Rotation is described [1].'
    done = stream[-1][1]['message']
    assert done['status'] == 'complete' and done['segments'] == [{'source_type': 'library', 'text': 'Rotation is described [1].'}]

    detail = lib.client.get(f'/api/v1/conversations/{cid}').json()
    assert detail['title'] == 'What is rotation?' and detail['message_count'] == 2
    user, assistant = detail['messages']
    assert (user['role'], user['mode']) == ('user', 'grounded')
    assert assistant['content'] == 'Rotation is described [1].'
    assert [s['citation'] for s in assistant['sources']] == [1] and assistant['removed_source_count'] == 0
    assert assistant['search_query'] == 'What is rotation?' and not lib.answerer.condensed
    assert lib.answerer.streams[0].history is None


def test_follow_up_is_rewritten_and_sees_previous_exchange(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    cid = lib.create()
    lib.say(cid, 'What is rotation?')
    lib.answerer.rewrite = 'angular velocity of rotation'
    stream = lib.say(cid, 'And its velocity?', mode='open')
    assert lib.answerer.condensed == [('And its velocity?', [
        {'role': 'user', 'content': 'What is rotation?'},
        {'role': 'assistant', 'content': 'Rotation is described [1].'}])]
    assert stream[1][1]['search_query'] == 'angular velocity of rotation'
    call = lib.answerer.streams[-1]
    assert call.question == 'And its velocity?' and call.mode == 'open' and len(call.history) == 2
    # Title stays the first question.
    assert lib.client.get(f'/api/v1/conversations/{cid}').json()['title'] == 'What is rotation?'


def test_failed_rewrite_falls_back_to_previous_question(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation and angular velocity.')
    cid = lib.create()
    lib.say(cid, 'What is rotation?')
    lib.answerer.rewrite = AnswerGenerationError('Query rewriting failed.')
    stream = lib.say(cid, 'Why?')
    assert stream[1][1]['search_query'] == 'What is rotation?\nWhy?'
    assert stream[-1][0] == 'done'


def test_grounded_turn_without_evidence_skips_the_model(lib):
    cid = lib.create()
    stream = lib.say(cid, 'Anything?')
    assert [name for name, _ in stream] == ['started', 'retrieval', 'done']
    assert stream[-1][1]['message']['content'] == NO_DOCUMENTS
    assert not lib.answerer.streams


def test_generation_error_is_an_event_and_the_turn_is_left_out_of_history(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation.')
    cid = lib.create()
    lib.answerer.fail = True
    stream = lib.say(cid, 'What is rotation?')
    assert stream[-1][0] == 'error'
    assert stream[-1][1]['detail'] == 'Answer generation was interrupted. Please retry.'
    failed = stream[-1][1]['message']
    assert failed['status'] == 'failed' and failed['content'] == 'Rotation is ' and failed['error']
    lib.answerer.fail = False
    lib.say(cid, 'Try again')
    assert not lib.answerer.condensed  # the failed exchange is not history
    assert lib.client.get(f'/api/v1/conversations/{cid}').json()['message_count'] == 4


def test_open_mode_segments_and_selection_are_stored(lib):
    doc = lib.add('mechanics.pdf', 'Rigid body rotation.')
    cid = lib.create(document_ids=[doc])
    lib.answerer.chunks = ['[LIBRARY]\nFrom the text [1].\n', '[MODEL]\nBackground.']
    selection = {'text': 'angular velocity', 'document_id': doc, 'page_number': 2}
    stream = lib.say(cid, 'Explain', mode='open', selection=selection)
    assert stream[1][1]['search_query'] == 'Explain\nangular velocity'
    message = stream[-1][1]['message']
    assert [s['source_type'] for s in message['segments']] == ['library', 'model']
    user = lib.client.get(f'/api/v1/conversations/{cid}').json()['messages'][0]
    assert user['selection'] == selection and user['mode'] == 'open'


def test_cancel_keeps_partial_answer_as_stopped(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation.')
    cid = lib.create()
    lib.answerer.gate = Event()
    turn = lib.conversations.ask(UUID(cid), 'What is rotation?')
    seen = []
    for event in turn.events():
        seen.append(event.name)
        if event.name == 'delta':
            turn.cancel()
            lib.answerer.gate.set()
    turn.join(5)
    assert seen == ['started', 'retrieval', 'delta', 'done']
    [_, assistant] = lib.conversations.messages(turn.conversation_id)
    assert assistant.status == 'stopped' and assistant.content == 'Rotation is'


def test_busy_conversation_rejects_second_question_and_deletion(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation.')
    cid = lib.create()
    lib.answerer.gate = Event()
    turn = lib.conversations.ask(UUID(cid), 'What is rotation?')
    try:
        assert lib.client.post(f'/api/v1/conversations/{cid}/messages', json={'question': 'again'}).status_code == 409
        assert lib.client.delete(f'/api/v1/conversations/{cid}').status_code == 409
    finally:
        lib.answerer.gate.set()
        list(turn.events())
        turn.join(5)
    assert lib.client.delete(f'/api/v1/conversations/{cid}').status_code == 204
    assert lib.client.get(f'/api/v1/conversations/{cid}').status_code == 404


def test_scope_and_input_validation(lib):
    assert lib.client.post('/api/v1/conversations', json={'document_ids': [str(uuid4())]}).status_code == 404
    assert lib.client.post('/api/v1/conversations', json={'collection_id': str(uuid4())}).status_code == 404
    assert lib.client.post(f'/api/v1/conversations/{uuid4()}/messages', json={'question': 'x'}).status_code == 404
    cid = lib.create(title='  My   notes ')
    assert lib.client.get(f'/api/v1/conversations/{cid}').json()['title'] == 'My notes'
    assert lib.client.post(f'/api/v1/conversations/{cid}/messages', json={'question': '   '}).status_code == 400
    renamed = lib.client.patch(f'/api/v1/conversations/{cid}', json={'title': 'Renamed'}).json()
    assert renamed['title'] == 'Renamed'
    assert [c['conversation_id'] for c in lib.client.get('/api/v1/conversations').json()['items']] == [cid]


def test_document_deletion_removes_its_conversations_and_sources(lib):
    doc = lib.add('mechanics.pdf', 'Rigid body rotation.')
    single, library = lib.create(document_ids=[doc]), lib.create()
    lib.say(single, 'What is rotation?')
    lib.say(library, 'What is rotation?')
    assert lib.client.delete(f'/api/v1/documents/{doc}').status_code == 204
    assert lib.client.get(f'/api/v1/conversations/{single}').status_code == 404
    assistant = lib.client.get(f'/api/v1/conversations/{library}').json()['messages'][1]
    assert assistant['sources'] == [] and assistant['removed_source_count'] == 1
    assert assistant['content'] == 'Rotation is described [1].'


def test_restart_marks_unfinished_answers_failed(lib):
    lib.add('mechanics.pdf', 'Rigid body rotation.')
    cid = lib.create()
    record = lib.conversations.get(UUID(cid))
    lib.conversations.repository.add_message(record.conversation_id, 'user', 'q', 'complete', {})
    lib.conversations.repository.add_message(record.conversation_id, 'assistant', '', 'streaming', {'mode': 'grounded'})
    assert lib.conversations.recover_interrupted() == 1
    assistant = lib.client.get(f'/api/v1/conversations/{cid}').json()['messages'][1]
    assert assistant['status'] == 'failed' and assistant['error'] == INTERRUPTED


def test_history_keeps_newest_whole_exchanges_within_budget():
    history = [{'role': r, 'content': c} for r, c in
               [('user', 'a' * 10), ('assistant', 'b' * 10), ('user', 'c' * 10), ('assistant', 'd' * 10)]]
    assert _fit(history, 100) == history
    assert _fit(history, 25) == history[2:]   # 'b' would fit but would start on an assistant message
    assert _fit(history, 5) == []
