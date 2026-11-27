from types import SimpleNamespace
import json
import sys

import pytest

from aviatlas.answering.generator import AnswerGenerator


@pytest.mark.parametrize('router,override,expected_model', [
    (True, None, 'openrouter/free'),
    (True, 'example/model:free', 'example/model:free'),
    (False, None, 'direct-model'),
])
def test_routes_key_and_model_to_correct_provider(monkeypatch, router, override, expected_model):
    for name in ['OPENROUTER_API_KEY', 'OPENROUTER_MODEL', 'OPENAI_BASE_URL']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'direct-test-key')
    monkeypatch.setenv('OPENAI_MODEL', 'direct-model')
    if router:
        monkeypatch.setenv('OPENROUTER_API_KEY', 'router-test-key')
        # Must not send the router credential to a direct-provider override.
        monkeypatch.setenv('OPENAI_BASE_URL', 'https://other.example/v1')
    if override:
        monkeypatch.setenv('OPENROUTER_MODEL', override)
    captured = {}
    class Provider:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='测试回答')
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=Provider))
    assert AnswerGenerator().generate('问题', []) == '测试回答'
    assert captured['model'] == expected_model
    assert captured['base_url'] == ('https://openrouter.ai/api/v1' if router else 'https://api.openai.com/v1')
    assert captured['api_key'] == ('router-test-key' if router else 'direct-test-key')


class Events:
    def __init__(self, events):
        self.events, self.closed = events, False
    def __iter__(self): return iter(self.events)
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True


def fake_provider(monkeypatch, reply):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'router-test-key')
    captured = {}
    class Provider:
        def __init__(self, **kwargs):
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): captured['client_closed'] = True
        def create(self, **kwargs):
            captured.update(kwargs)
            return reply
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=Provider))
    return captured


def delta(text):
    return SimpleNamespace(type='response.output_text.delta', delta=text)


def test_stream_yields_text_deltas_and_sends_history(monkeypatch):
    events = Events([SimpleNamespace(type='response.created'), delta('第一'), delta('部分'),
                     SimpleNamespace(type='response.completed')])
    captured = fake_provider(monkeypatch, events)
    history = [{'role': 'user', 'content': 'earlier'}, {'role': 'assistant', 'content': 'reply [1]'}]
    assert list(AnswerGenerator().stream('问题', [], history=history)) == ['第一', '部分']
    assert captured['stream'] is True and events.closed and captured['client_closed']
    assert 'not to the current evidence' in captured['instructions']
    assert json.loads(captured['input'])['history'] == history


def test_stream_failure_and_early_close(monkeypatch):
    from aviatlas.answering.generator import AnswerGenerationError
    fake_provider(monkeypatch, Events([delta('a'), SimpleNamespace(type='response.failed')]))
    with pytest.raises(AnswerGenerationError):
        list(AnswerGenerator().stream('q', []))
    events = Events([delta('a'), delta('b')])
    fake_provider(monkeypatch, events)
    stream = AnswerGenerator().stream('q', [])
    assert next(stream) == 'a'
    stream.close()
    assert events.closed


def test_condense_returns_the_rewritten_query(monkeypatch):
    captured = fake_provider(monkeypatch, SimpleNamespace(output_text='  standalone query \n'))
    assert AnswerGenerator().condense('and that?', [{'role': 'user', 'content': 'x'}]) == 'standalone query'
    assert 'standalone search query' in captured['instructions'] and 'stream' not in captured
