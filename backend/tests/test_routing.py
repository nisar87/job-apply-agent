"""Routing between Claude and Gemini, and the Claude client's message handling (no network)."""

from types import SimpleNamespace

import pytest

from app.llm.base import LLMResult, SpendCapExceeded, ToolSpec
from app.llm.claude import ClaudeClient, ClaudeUnavailable
from app.llm.routing import RoutedLLM


class Stub:
    def __init__(self, name, fail=None):
        self.name, self.fail, self.calls = name, fail, 0

    def generate(self, prompt, *, system=None, tier="fast", task=""):
        self.calls += 1
        if self.fail:
            raise self.fail
        return LLMResult(text=self.name, model=self.name)

    generate_structured = generate_with_tools = None

    def embed(self, texts):
        return [[1.0]]


def test_strong_tasks_go_to_claude_and_fast_tasks_to_gemini():
    g, c = Stub("gemini"), Stub("claude")
    r = RoutedLLM(g, c)
    assert r.generate("x", tier="strong").text == "claude"
    assert r.generate("x", tier="fast").text == "gemini"
    assert r.embed(["a"]) == [[1.0]]


def test_claude_failing_hands_the_call_to_gemini_and_pauses_claude():
    now = [0.0]
    g, c = Stub("gemini"), Stub("claude", fail=ClaudeUnavailable("no credit"))
    r = RoutedLLM(g, c, clock=lambda: now[0])
    assert r.generate("x", tier="strong").text == "gemini" and c.calls == 1
    assert r.generate("x", tier="strong").text == "gemini" and c.calls == 1        # paused: not asked again
    now[0] = 400.0
    c.fail = None
    assert r.generate("x", tier="strong").text == "claude"                          # tried again after the pause


def test_gemini_failing_hands_a_fast_call_to_claude():
    g, c = Stub("gemini", fail=RuntimeError("down")), Stub("claude")
    assert RoutedLLM(g, c).generate("x", tier="fast").text == "claude"


def test_both_failing_raises_and_a_spend_cap_is_never_swallowed():
    with pytest.raises(RuntimeError):
        RoutedLLM(Stub("gemini", fail=RuntimeError("a")), Stub("claude", fail=RuntimeError("b"))).generate("x")
    g = Stub("gemini", fail=SpendCapExceeded("cap"))
    with pytest.raises(SpendCapExceeded):
        RoutedLLM(g, Stub("claude")).generate("x", tier="fast")


def test_without_a_claude_client_gemini_does_everything():
    assert RoutedLLM(Stub("gemini"), None).generate("x", tier="strong").text == "gemini"


# ---- the Claude client ----

class FakeMessages:
    def __init__(self, blocks):
        self.blocks, self.sent = blocks, []

    def create(self, **kw):
        self.sent.append(kw)
        content = [SimpleNamespace(type=b["type"], model_dump=lambda exclude_none=True, b=b: b, **{k: v for k, v in b.items() if k != "type"}) for b in self.blocks]
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def claude_with(blocks):
    c = ClaudeClient.__new__(ClaudeClient)
    c._models = {"fast": "f", "strong": "s"}
    c._client = SimpleNamespace(messages=FakeMessages(blocks))
    return c


def test_structured_answers_come_from_the_forced_tool_call():
    c = claude_with([{"type": "tool_use", "id": "t1", "name": "answer", "input": {"score": 80}}])
    out = c.generate_structured("judge", {"type": "object"}, tier="strong")
    assert out.data == {"score": 80} and (out.input_tokens, out.output_tokens) == (10, 5) and out.model == "s"
    sent = c._client.messages.sent[0]
    assert sent["tool_choice"] == {"type": "auto"}


def test_tool_conversations_pass_ids_back_and_group_results():
    c = claude_with([{"type": "text", "text": "done"}])
    own = [{"type": "tool_use", "id": "a", "name": "look", "input": {}}, {"type": "tool_use", "id": "b", "name": "look", "input": {}}]
    messages = [{"role": "user", "text": "go"}, {"role": "model", "raw": own},
                {"role": "tool", "name": "look", "result": {"n": 1}}, {"role": "tool", "name": "look", "result": {"n": 2}}]
    c.generate_with_tools(messages, [ToolSpec("look", "d", {"type": "object"})])
    sent = c._client.messages.sent[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["a", "b"]


def test_a_history_from_gemini_is_passed_on_as_words():
    c = claude_with([{"type": "text", "text": "ok"}])
    messages = [{"role": "user", "text": "go"}, {"role": "model", "raw": object(), "text": "I searched"},
                {"role": "tool", "name": "look", "result": 1}]
    c.generate_with_tools(messages, [])
    sent = c._client.messages.sent[0]["messages"]
    assert sent[1] == {"role": "assistant", "content": "I searched"} and sent[2]["content"][0]["type"] == "text"


def test_a_stalled_claude_call_is_given_up_at_once_so_gemini_can_answer():
    import anthropic
    import httpx
    import pytest

    from app.llm.claude import ClaudeClient, ClaudeUnavailable
    c = ClaudeClient(api_key="k")
    calls = []

    def stalled(**kw):
        calls.append(1)
        raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
    c._client.messages.create = stalled
    with pytest.raises(ClaudeUnavailable):
        c.generate("hello")
    assert len(calls) == 1             # no repeated waits


def test_the_keys_a_person_gives_decide_the_models(client, llm, monkeypatch):
    """Both: routed. Only one: that one. None: the service's pair. The one rule."""
    import app.llm as llm_pkg
    from app.config import get_settings
    from app.llm import access
    from app.llm.claude import ClaudeClient
    from app.llm.gemini import GeminiClient
    from app.llm.routing import RoutedLLM
    from app.models import User
    from tests.helpers import make_world
    monkeypatch.setattr(get_settings(), "llm_provider", "gemini")
    monkeypatch.setattr(get_settings(), "gemini_api_key", "g-platform")
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "c-platform")
    w = make_world(client, llm)
    access.save_platform_settings(key_policy="either")      # the admin lets the service's keys back some people
    monkeypatch.setattr(llm_pkg, "_override", None)      # the real client choice, not the test double
    with w.session() as s:
        user = s.get(User, w.user_id)

        def pick(gemini="", claude="", allowed=True):
            access.set_own_key(user, gemini)
            access.set_own_claude_key(user, claude)
            user.platform_key_allowed = allowed
            s.flush()
            return access.client_for(s, w.user_id)
        assert isinstance(pick("AIza-own", "sk-ant-own"), RoutedLLM)
        assert isinstance(pick("AIza-own", ""), GeminiClient)
        only_claude = pick("", "sk-ant-own")
        assert isinstance(only_claude, RoutedLLM) and only_claude.gemini is not None      # the service's Gemini for embeddings
        assert isinstance(pick("", "", True), RoutedLLM)                                  # the service's pair
        assert isinstance(only_claude.claude, ClaudeClient)
        import pytest
        with pytest.raises(access.NoModelKey):
            pick("", "", False)
