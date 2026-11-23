"""Claude as a model provider behind the same interface as Gemini.

Used for the strong tasks (judging a job against the CV, writing the application, reading a reply). Embeddings
stay on Gemini. The router in `routing.py` falls back to Gemini when Claude fails or its credit runs out.
"""

import json
import logging
import time

import anthropic

from app.config import get_settings
from app.llm.base import LLMResult, ToolCall, ToolSpec

log = logging.getLogger(__name__)
RETRY_CODES = {408, 429, 500, 502, 503, 504, 529}
RETRY_WAITS = (2, 6, 15)
MAX_TOKENS = 8000


class ClaudeUnavailable(Exception):
    """Claude cannot serve this call now: bad or expired key, no credit, or still failing after retries.
    The router answers it by using Gemini."""


class ClaudeClient:
    def __init__(self, api_key: str | None = None):
        s = get_settings()
        key = api_key or s.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=anthropic.Timeout(60.0, connect=10.0))
        self._models = {"fast": s.claude_model_fast, "strong": s.claude_model_strong}

    def _model(self, tier: str) -> str:
        return self._models.get(tier, self._models["strong"])

    def _call(self, **kwargs):
        for wait in (*RETRY_WAITS, None):
            try:
                return self._client.messages.create(**kwargs)
            except anthropic.APIStatusError as exc:
                if exc.status_code in (401, 402, 403) or (exc.status_code == 400 and "credit" in str(exc).lower()):
                    raise ClaudeUnavailable(f"Claude refused the key or credit ({exc.status_code})") from exc
                if wait is None or exc.status_code not in RETRY_CODES:
                    raise ClaudeUnavailable(f"Claude answered {exc.status_code}: {str(getattr(exc, 'message', exc))[:300]}") from exc
                log.warning("Claude answered %s, retrying in %ss", exc.status_code, wait)
            except anthropic.APITimeoutError as exc:       # a call that stalls is not repeated: Gemini answers instead
                raise ClaudeUnavailable("Claude did not answer in time") from exc
            except anthropic.APIConnectionError as exc:
                if wait is None:
                    raise ClaudeUnavailable(type(exc).__name__) from exc
                log.warning("Claude call failed (%s), retrying in %ss", type(exc).__name__, wait)
            time.sleep(wait)

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    @staticmethod
    def _usage(resp) -> tuple[int, int]:
        return int(resp.usage.input_tokens or 0), int(resp.usage.output_tokens or 0)

    def generate(self, prompt: str, *, system: str | None = None, tier: str = "strong", task: str = "") -> LLMResult:
        model = self._model(tier)
        kw = {"system": system} if system else {}
        resp = self._call(model=model, max_tokens=MAX_TOKENS, messages=[{"role": "user", "content": prompt}], **kw)
        i, o = self._usage(resp)
        return LLMResult(text=self._text(resp), input_tokens=i, output_tokens=o, model=model)

    def generate_structured(
        self, prompt: str, schema: dict, *, system: str | None = None, tier: str = "strong", task: str = "",
    ) -> LLMResult:
        """Structured output through one forced tool call whose input schema is the wanted shape."""
        model = self._model(tier)
        tool = {"name": "answer", "description": "Return the answer in exactly this shape.", "input_schema": schema}
        kw = {"system": system} if system else {}
        resp = self._call(model=model, max_tokens=MAX_TOKENS, tools=[tool], tool_choice={"type": "tool", "name": "answer"},
                          messages=[{"role": "user", "content": prompt}], **kw)
        i, o = self._usage(resp)
        data = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if data is None:
            raise ClaudeUnavailable("Claude returned no structured answer")
        return LLMResult(text=json.dumps(data), data=data, input_tokens=i, output_tokens=o, model=model)

    def generate_with_tools(
        self, messages: list[dict], tools: list[ToolSpec], *, system: str | None = None,
        tier: str = "strong", task: str = "",
    ) -> LLMResult:
        model = self._model(tier)
        out: list[dict] = []
        last_ids: dict[str, list[str]] = {}
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "model":
                raw = m.get("raw")
                if isinstance(raw, list):          # our own earlier message: content blocks, passed back unchanged
                    out.append({"role": "assistant", "content": raw})
                    last_ids = {}
                    for b in raw:
                        if b.get("type") == "tool_use":
                            last_ids.setdefault(b["name"], []).append(b["id"])
                else:                               # a message from another provider: its words only
                    out.append({"role": "assistant", "content": m.get("text") or "(continuing)"})
                    last_ids = {}
            elif m["role"] == "tool":
                ids = last_ids.get(m["name"]) or []
                block = ({"type": "tool_result", "tool_use_id": ids.pop(0), "content": json.dumps(m["result"], default=str)}
                         if ids else {"type": "text", "text": f"Result of {m['name']}: {json.dumps(m['result'], default=str)}"})
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        specs = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        kw = {"system": system} if system else {}
        resp = self._call(model=model, max_tokens=MAX_TOKENS, tools=specs, messages=out, **kw)
        i, o = self._usage(resp)
        blocks = [b.model_dump(exclude_none=True) for b in resp.content]
        calls = [ToolCall(name=b["name"], args=dict(b["input"])) for b in blocks if b.get("type") == "tool_use"]
        return LLMResult(text=self._text(resp), tool_calls=calls, input_tokens=i, output_tokens=o, model=model, raw=blocks)

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("embeddings stay on Gemini")
