"""Citation verification: claims, local checks, quote-checked model verdicts, and the API."""
import json
from io import BytesIO
from types import SimpleNamespace as N
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aviatlas.answering.generator import AnswerConfigurationError
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.main import app
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.models import Block, BlockType, BoundingBox, DocumentMetadata, ParsedDocument, ProvenanceItem
from aviatlas.retrieval.dense import DenseRetriever
from aviatlas.retrieval.embedding import SentenceTransformerEmbedder
from aviatlas.retrieval.hybrid import HybridRetriever
from aviatlas.retrieval.keyword import KeywordRetriever
from aviatlas.services.collections import CollectionService
from aviatlas.services.conversations import ConversationService
from aviatlas.services.documents import DocumentService
from aviatlas.services.qa import QuestionAnsweringService, SearchService
from aviatlas.services.scope import ScopeResolver
from aviatlas.services.verification import VerificationService
from aviatlas.storage.collection_repository import SQLiteCollectionRepository
from aviatlas.storage.conversation_repository import SQLiteConversationRepository
from aviatlas.storage.keyword_index import SQLiteKeywordIndex
from aviatlas.storage.memory_vector_index import InMemoryVectorIndex
from aviatlas.storage.sqlite_repository import SQLiteDocumentRepository
from aviatlas.storage.verification_repository import SQLiteVerificationRepository
from aviatlas.verification.claims import extract_claims, local_checks
from aviatlas.verification.support import VerificationFailed, judge, parse, quoted

EXCERPT = 'The big Transformer reaches a BLEU score of 28.4 on WMT 2014 English-to-German. Training took 3.5 days.'


def test_claims_come_from_document_segments_only():
    segments = [N(source_type='library', text='- The model reaches 28.4 BLEU [1]. Training took 3.5 days [1, 2].\nOk.'),
                N(source_type='model', text='Transformers are popular in industry.'),
                N(source_type='unlabeled', text='大模型在英法任务上达到 41.8 分 [1]。')]
    claims = extract_claims(segments)
    assert [(c.text, c.citations, c.source_type) for c in claims] == [
        ('The model reaches 28.4 BLEU [1].', [1], 'library'), ('Training took 3.5 days [1, 2].', [1, 2], 'library'),
        ('大模型在英法任务上达到 41.8 分 [1]。', [1], 'unlabeled')]
    hinted = local_checks(claims + extract_claims([N(source_type='library', text='An uncited sentence here.')]),
                          {1: EXCERPT})
    assert [c.hints for c in hinted] == [[], ['unknown_citation:2'], ['number_not_in_source:41.8'], ['uncited']]


def test_quotes_must_occur_in_the_excerpt():
    assert quoted('a BLEU score of 28.4', EXCERPT)
    assert quoted('the BIG transformer   reaches ... took 3.5 days', EXCERPT)
    assert not quoted('a BLEU score of 41.0', EXCERPT)
    assert not quoted('...', EXCERPT) and not quoted('', EXCERPT)


def claim(index, citations):
    return N(index=index, text=f'claim {index}', source_type='library', citations=citations, hints=[])


def test_verdicts_count_only_with_verified_quotes():
    excerpts = {1: {'text': EXCERPT}, 2: {'text': 'Label smoothing hurts perplexity.'}}
    claims = [claim(1, [1]), claim(2, [1]), claim(3, [1]), claim(4, [1]), claim(5, [1]), claim(6, [1]), claim(7, []), claim(8, [3])]
    results = {
        1: {'verdict': 'supported', 'evidence': [{'citation': 1, 'quote': 'BLEU score of 28.4'}], 'reason': 'stated'},
        2: {'verdict': 'supported', 'evidence': [{'citation': 1, 'quote': 'BLEU score of 30'}]},       # invented quote
        3: {'verdict': 'contradicted', 'evidence': [{'citation': 1, 'quote': 'Training took 3.5 days'}]},
        4: {'verdict': 'partial', 'evidence': [{'citation': 1, 'quote': 'WMT 2014'}]},
        5: {'verdict': 'supported', 'evidence': [{'citation': 2, 'quote': 'Label smoothing'}]},       # not the cited source
        7: {'verdict': 'supported', 'evidence': [{'citation': 2, 'quote': 'hurts perplexity'}]},      # uncited claim
        8: {'verdict': 'supported', 'evidence': []},
    }
    out = {r.claim.index: r for r in judge(claims, excerpts, results)}
    assert out[1].status == 'supported' and out[1].evidence[0].quote == 'BLEU score of 28.4' and out[1].reason == 'stated'
    assert (out[2].status, out[2].notes) == ('insufficient_evidence', ['quote_not_found', 'no_verified_quote'])
    assert out[3].status == 'conflicting'
    assert (out[4].status, out[4].model_verdict) == ('insufficient_evidence', 'partial')
    assert (out[5].status, out[5].suggested_citations) == ('insufficient_evidence', [2])
    assert out[6].status == 'unverified' and out[6].notes == ['not_judged']
    assert (out[7].status, out[7].notes) == ('supported', ['uncited'])
    assert out[8].status == 'unverified' and 'source_removed' in out[8].notes


def test_model_output_must_be_readable_json():
    assert parse('```json\n{"results": [{"claim": 1, "verdict": "supported"}]}\n```')[1]['verdict'] == 'supported'
    for raw in ['I think it is supported.', '{"results": "yes"}', '{"answer": []}']:
        with pytest.raises(VerificationFailed):
            parse(raw)


class Encoder:
    def encode(self, texts, **kwargs):
        rows = [[float('bleu' in t.lower()), .1] for t in texts]
        return np.array([np.array(r) / np.linalg.norm(r) for r in rows])


class Answerer:
    def __init__(self):
        self.reply = 'The big Transformer reaches 28.4 BLEU [1]. It needs 41.0 days [1].'
        self.verdicts = None
        self.judged = []

    def model_label(self):
        return 'test/judge'

    def generate(self, question, sources, **kwargs):
        return self.reply

    def stream(self, question, sources, **kwargs):
        yield self.reply

    def condense(self, question, history):
        return question

    def judge(self, instructions, payload):
        self.judged.append(json.loads(payload))
        if isinstance(self.verdicts, Exception):
            raise self.verdicts
        return self.verdicts


@pytest.fixture
def lib(tmp_path):
    repo = SQLiteDocumentRepository(tmp_path / 'documents.sqlite3')
    collections = CollectionService(SQLiteCollectionRepository(repo))
    vectors, embedder, keywords = InMemoryVectorIndex(), SentenceTransformerEmbedder('test', Encoder()), SQLiteKeywordIndex(repo)

    def parse_file(path):
        return ParsedDocument(metadata=DocumentMetadata(page_count=8), blocks=[Block(
            id=uuid4(), type=BlockType.PARAGRAPH, order=0, content=path.read_text(),
            provenance=[ProvenanceItem(page_number=8, bbox=BoundingBox(x0=0, y0=0, x1=1, y1=1))])])

    docs = DocumentService(repo, vectors, tmp_path / 'uploads', lambda: N(parse=parse_file), GreedyChunker(), embedder, keywords)
    retriever = HybridRetriever(DenseRetriever(repo, embedder, vectors), KeywordRetriever(repo, keywords))
    resolver = ScopeResolver(repo, collections)
    answerer = Answerer()
    conversations = ConversationService(SQLiteConversationRepository(repo), retriever, answerer, resolver)
    services = Services(docs, QuestionAnsweringService(repo, retriever, answerer, resolver), collections,
                        SearchService(retriever, resolver), conversations,
                        verification=VerificationService(conversations, SQLiteVerificationRepository(repo), answerer))
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as http:
        record = docs.upload('paper.pdf', BytesIO(EXCERPT.encode()))
        docs.parse(record.id)
        yield N(http=http, answerer=answerer, docs=docs, doc=str(record.id))
    app.dependency_overrides.pop(get_services, None)


def answer(lib):
    cid = lib.http.post('/api/v1/conversations', json={}).json()['conversation_id']
    body = lib.http.post(f'/api/v1/conversations/{cid}/messages', json={'question': 'What BLEU?'}).text
    done = json.loads(body.strip().split('\n\n')[-1].split('\n', 1)[1].removeprefix('data: '))['message']
    return cid, done


def test_answers_carry_local_checks(lib):
    _, message = answer(lib)
    assert message['checks'] == [{'claim': 2, 'text': 'It needs 41.0 days [1].', 'hints': ['number_not_in_source:41.0']}]
    asked = lib.http.post('/api/v1/ask', json={'question': 'What BLEU?'}).json()
    assert asked['checks'][0]['hints'] == ['number_not_in_source:41.0']


def test_verification_report_is_stored_and_returned_with_the_conversation(lib):
    cid, message = answer(lib)
    lib.answerer.verdicts = json.dumps({'results': [
        {'claim': 1, 'verdict': 'supported', 'evidence': [{'citation': 1, 'quote': 'reaches a BLEU score of 28.4'}], 'reason': '原文写明 28.4。'},
        {'claim': 2, 'verdict': 'contradicted', 'evidence': [{'citation': 1, 'quote': 'Training took 3.5 days'}], 'reason': '原文是 3.5 天。'}]})
    url = f"/api/v1/conversations/{cid}/messages/{message['message_id']}/verification"
    report = lib.http.post(url).json()
    sent = lib.answerer.judged[0]
    assert [c['citations'] for c in sent['claims']] == [[1], [1]] and sent['excerpts'][0]['text'] == EXCERPT
    assert report['summary'] == {'supported': 1, 'conflicting': 1, 'insufficient_evidence': 0, 'unverified': 0}
    first = report['claims'][0]
    assert first['evidence'] == [{'citation': 1, 'quote': 'reaches a BLEU score of 28.4', 'document_name': 'paper.pdf', 'page_range': [8, 8]}]
    assert report['claims'][1]['hints'] == ['number_not_in_source:41.0'] and report['model'] == 'test/judge'
    assert 'model_judgement' in report['limitations']
    detail = lib.http.get(f'/api/v1/conversations/{cid}').json()['messages'][1]
    assert detail['verification']['verification_id'] == report['verification_id']
    # Re-running keeps history; the conversation shows the newest report.
    lib.answerer.verdicts = json.dumps({'results': [{'claim': 1, 'verdict': 'not_supported'}]})
    newer = lib.http.post(url).json()
    assert newer['summary']['unverified'] == 1 and newer['summary']['insufficient_evidence'] == 1
    assert lib.http.get(f'/api/v1/conversations/{cid}').json()['messages'][1]['verification']['verification_id'] == newer['verification_id']


def test_failed_verification_is_an_error_not_a_report(lib):
    cid, message = answer(lib)
    url = f"/api/v1/conversations/{cid}/messages/{message['message_id']}/verification"
    lib.answerer.verdicts = 'Looks fine to me.'
    assert lib.http.post(url).status_code == 502
    lib.answerer.verdicts = AnswerConfigurationError('Set OPENROUTER_API_KEY.')
    assert lib.http.post(url).status_code == 503
    assert lib.http.get(f'/api/v1/conversations/{cid}').json()['messages'][1]['verification'] is None
    user_message = lib.http.get(f'/api/v1/conversations/{cid}').json()['messages'][0]['message_id']
    assert lib.http.post(f'/api/v1/conversations/{cid}/messages/{user_message}/verification').status_code == 404


def test_deleted_sources_leave_claims_unverified_without_a_model_call(lib):
    cid, message = answer(lib)
    assert lib.http.delete(f'/api/v1/documents/{lib.doc}').status_code == 204
    report = lib.http.post(f"/api/v1/conversations/{cid}/messages/{message['message_id']}/verification").json()
    assert lib.answerer.judged == [] and report['model'] is None
    assert report['summary']['unverified'] == 2 and 'no_sources' in report['limitations']
