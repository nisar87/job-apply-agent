"""Gemini implementation of the LLM interface, using the Google Gen AI SDK."""

import json

from google import genai
from google.genai import types

from app.config import get_settings
from app.llm.base import LLMResult, ToolCall, ToolSpec


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        s = get_settings()
        key = api_key or s.gemini_api_key
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=key)
        self._models = {"fast": s.gemini_model_fast, "strong": s.gemini_model_strong}
        self._embed_model = s.gemini_model_embed

    def _model(self, tier: str) -> str:
        return self._models.get(tier, self._models["fast"])

    @staticmethod
    def _usage(resp) -> tuple[int, int]:
        usage = getattr(resp, "usage_metadata", None)
        if usage is None:
            return 0, 0
        return int(usage.prompt_token_count or 0), int(usage.candidates_token_count or 0)

    def generate(self, prompt: str, *, system: str | None = None, tier: str = "fast", task: str = "") -> LLMResult:
        model = self._model(tier)
        resp = self._client.models.generate_content(
            model=model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system)
        )
        i, o = self._usage(resp)
        return LLMResult(text=resp.text or "", input_tokens=i, output_tokens=o, model=model)

    def generate_structured(
        self, prompt: str, schema: dict, *, system: str | None = None, tier: str = "fast", task: str = ""
    ) -> LLMResult:
        model = self._model(tier)
        resp = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        i, o = self._usage(resp)
        text = resp.text or "{}"
        return LLMResult(text=text, data=json.loads(text), input_tokens=i, output_tokens=o, model=model)

    def generate_with_tools(
        self, messages: list[dict], tools: list[ToolSpec], *, system: str | None = None,
        tier: str = "strong", task: str = "",
    ) -> LLMResult:
        model = self._model(tier)
        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["text"])]))
            elif m["role"] == "model":
                # Pass the provider's own message back unchanged so any reasoning
                # signatures attached to tool calls are preserved.
                if m.get("raw") is not None:
                    contents.append(m["raw"])
                else:
                    contents.append(types.Content(role="model", parts=[types.Part.from_text(text=m.get("text", ""))]))
            elif m["role"] == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=m["name"], response={"result": m["result"]})],
                    )
                )
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
            for t in tools
        ]
        resp = self._client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=declarations)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        i, o = self._usage(resp)
        calls = [ToolCall(name=fc.name, args=dict(fc.args or {})) for fc in (resp.function_calls or [])]
        raw = resp.candidates[0].content if resp.candidates else None
        text = ""
        try:
            text = resp.text or ""
        except Exception:
            text = ""
        return LLMResult(text=text, tool_calls=calls, input_tokens=i, output_tokens=o, model=model, raw=raw)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.models.embed_content(model=self._embed_model, contents=texts)
        return [list(e.values) for e in resp.embeddings]
