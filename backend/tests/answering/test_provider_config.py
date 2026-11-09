from types import SimpleNamespace
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
