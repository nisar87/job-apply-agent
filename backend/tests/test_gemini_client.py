"""The Gemini client's handling of transient failures, without calling Google."""

import json
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.llm import gemini


class StubModels:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def generate_content(self, **kw):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=7)
        return SimpleNamespace(text=out, usage_metadata=usage)


def client_with(outcomes, monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    client = gemini.GeminiClient(api_key="test-key")
    client._client = SimpleNamespace(models=StubModels(outcomes))
    return client


def api_error(code):
    return genai_errors.APIError(code, {"error": {"message": "x", "status": "X"}})


def test_overload_is_retried_and_reasoning_tokens_count_as_output(monkeypatch):
    c = client_with([api_error(503), api_error(429), "pong"], monkeypatch)
    r = c.generate("ping")
    assert r.text == "pong" and c._client.models.calls == 3 and (r.input_tokens, r.output_tokens) == (10, 12)


def test_a_rejected_request_is_not_retried(monkeypatch):
    c = client_with([api_error(400), "never"], monkeypatch)
    with pytest.raises(genai_errors.APIError):
        c.generate("ping")
    assert c._client.models.calls == 1


def test_invalid_json_is_asked_for_once_more(monkeypatch):
    c = client_with(['{"a": ', '{"a": 1}'], monkeypatch)
    r = c.generate_structured("x", {"type": "object"})
    assert r.data == {"a": 1} and r.input_tokens == 20
    c = client_with(["nope", "still nope"], monkeypatch)
    with pytest.raises(json.JSONDecodeError):
        c.generate_structured("x", {"type": "object"})
