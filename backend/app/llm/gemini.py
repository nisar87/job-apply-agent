"""Gemini implementation of the LLM interface, using the Google Gen AI SDK."""

import json
import logging
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import get_settings
from app.llm.base import LLMResult, ToolCall, ToolSpec

log = logging.getLogger(__name__)
RETRY_CODES = {408, 429, 500, 502, 503, 504}
RETRY_WAITS = (2, 6, 15)


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        s = get_settings()
        key = api_key or s.gemini_api_key
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120_000))   # a stalled call fails after 2 minutes
        self._models = {"fast": s.gemini_model_fast, "strong": s.gemini_model_strong}
        self._embed_model = s.gemini_model_embed

    def _model(self, tier: str) -> str:
        return self._models.get(tier, self._models["fast"])

    @staticmethod
    def _usage(resp) -> tuple[int, int]:
        usage = getattr(resp, "usage_metadata", None)
        if usage is None:
            return 0, 0
        # Reasoning tokens are billed as output.
        out = int(usage.candidates_token_count or 0) + int(getattr(usage, "thoughts_token_count", 0) or 0)
        return int(usage.prompt_token_count or 0), out

    def _call(self, fn):
        """Run one API call, retrying the errors that pass on their own (rate limit, overload, network)."""
        for wait in (*RETRY_WAITS, None):
            try:
                return fn()
            except genai_errors.APIError as exc:
                if wait is None or getattr(exc, "code", None) not in RETRY_CODES:
                    raise
                log.warning("Gemini answered %s, retrying in %ss", getattr(exc, "code", "?"), wait)
            except (httpx.TransportError, OSError) as exc:
                if wait is None:
                    raise
                log.warning("Gemini call failed (%s), retrying in %ss", type(exc).__name__, wait)
            time.sleep(wait)

    def generate(self, prompt: str, *, system: str | None = None, tier: str = "fast", task: str = "") -> LLMResult:
        model = self._model(tier)
        resp = self._call(lambda: self._client.models.generate_content(
            model=model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system)
        ))
        i, o = self._usage(resp)
        return LLMResult(text=resp.text or "", input_tokens=i, output_tokens=o, model=model)

    def generate_structured(
        self, prompt: str, schema: dict, *, system: str | None = None, tier: str = "fast", task: str = ""
    ) -> LLMResult:
        model = self._model(tier)
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json", response_json_schema=schema,
        )
        i = o = 0
        for attempt in (1, 2):
            resp = self._call(lambda: self._client.models.generate_content(model=model, contents=prompt, config=config))
            ti, to = self._usage(resp)
            i, o = i + ti, o + to
            text = resp.text or "{}"
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # A cut-off or malformed answer: ask once more, then give up.
                if attempt == 2:
                    raise
                log.warning("Gemini returned invalid JSON for task %s, asking again", task)
                continue
            return LLMResult(text=text, data=data, input_tokens=i, output_tokens=o, model=model)
        raise RuntimeError("unreachable")

    def generate_with_tools(
        self, messages: list[dict], tools: list[ToolSpec], *, system: str | None = None,
        tier: str = "strong", task: str = "",
    ) -> LLMResult:
        model = self._model(tier)
        contents = []
        answering_tools = False
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["text"])]))
            elif m["role"] == "model":
                # Pass the provider's own message back unchanged so any reasoning
                # signatures attached to tool calls are preserved.
                if m.get("raw") is not None and not isinstance(m["raw"], list):   # a list is Claude's own message
                    contents.append(m["raw"])
                else:
                    contents.append(types.Content(role="model", parts=[types.Part.from_text(text=m.get("text", ""))]))
            elif m["role"] == "tool":
                part = types.Part.from_function_response(name=m["name"], response={"result": m["result"]})
                # Gemini wants the answers to all tool calls of one model turn together in one message.
                if answering_tools:
                    contents[-1].parts.append(part)
                else:
                    contents.append(types.Content(role="user", parts=[part]))
            answering_tools = m["role"] == "tool"
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
            for t in tools
        ]
        resp = self._call(lambda: self._client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=declarations)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        ))
        i, o = self._usage(resp)
        calls = [ToolCall(name=fc.name, args=dict(fc.args or {})) for fc in (resp.function_calls or [])]
        raw = resp.candidates[0].content if resp.candidates else None
        # Join the text parts by hand: resp.text logs a warning when the reply holds tool calls.
        parts = (raw.parts or []) if raw is not None else []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        return LLMResult(text=text, tool_calls=calls, input_tokens=i, output_tokens=o, model=model, raw=raw)

    def search_web(self, prompt: str, *, tier: str = "fast", task: str = "") -> LLMResult:
        """Answer with Google Search, the way a chat assistant looks things up. data holds the queries the model
        ran and the pages it read, so callers can tell a found link from a made-up one."""
        model = self._model(tier)
        resp = self._call(lambda: self._client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        ))
        i, o = self._usage(resp)
        meta = resp.candidates[0].grounding_metadata if resp.candidates else None
        pages = [{"title": c.web.title or "", "uri": c.web.uri or ""}
                 for c in ((meta.grounding_chunks or []) if meta else []) if getattr(c, "web", None)]
        data = {"queries": list((meta.web_search_queries or []) if meta else []), "pages": pages}
        return LLMResult(text=resp.text or "", data=data, input_tokens=i, output_tokens=o, model=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._call(lambda: self._client.models.embed_content(model=self._embed_model, contents=texts))
        return [list(e.values) for e in resp.embeddings]
