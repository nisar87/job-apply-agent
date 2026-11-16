"""A deterministic stand-in for the model, used in tests and local dry runs.

Structured calls are dispatched on the `task` name. Tests register a handler per task;
anything unregistered falls back to a minimal object built from the schema.
"""

import hashlib
import math
import re
from collections.abc import Callable

from app.llm.base import LLMResult, ToolCall, ToolSpec


def _default_for(schema: dict):
    t = schema.get("type")
    if t == "object":
        return {k: _default_for(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    if t == "string":
        return (schema.get("enum") or [""])[0]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    return None


def hashed_embedding(text: str, dim: int = 64) -> list[float]:
    """Bag-of-words hashing: texts that share words get similar vectors."""
    vec = [0.0] * dim
    for word in re.findall(r"[a-zA-ZäöüÄÖÜß0-9+#]+", text.lower()):
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeLLM:
    def __init__(self):
        self.handlers: dict[str, Callable[[str, dict], object]] = {}
        self.tool_script: list[list[ToolCall]] = []
        self.calls: list[dict] = []
        self.model = "fake-model"

    def on(self, task: str, handler: Callable[[str, dict], object]) -> None:
        self.handlers[task] = handler

    def _result(self, prompt: str, text: str = "", data=None, tool_calls=None) -> LLMResult:
        return LLMResult(
            text=text, data=data, tool_calls=tool_calls or [], input_tokens=len(prompt) // 4,
            output_tokens=max(1, len(text) // 4), model=self.model,
        )

    def generate(self, prompt, *, system=None, tier="fast", task="") -> LLMResult:
        self.calls.append({"kind": "generate", "task": task, "prompt": prompt, "system": system})
        handler = self.handlers.get(task)
        text = str(handler(prompt, {})) if handler else "ok"
        return self._result(prompt, text=text)

    def generate_structured(self, prompt, schema, *, system=None, tier="fast", task="") -> LLMResult:
        self.calls.append({"kind": "structured", "task": task, "prompt": prompt, "system": system})
        handler = self.handlers.get(task)
        data = handler(prompt, schema) if handler else _default_for(schema)
        return self._result(prompt, text=str(data), data=data)

    def generate_with_tools(self, messages, tools: list[ToolSpec], *, system=None, tier="strong", task="") -> LLMResult:
        self.calls.append({"kind": "tools", "task": task, "messages": list(messages), "system": system})
        handler = self.handlers.get(task)
        if handler:
            out = handler(messages, {"tools": tools})
            if isinstance(out, list):
                return self._result("", tool_calls=out)
            return self._result("", text=str(out))
        if self.tool_script:
            return self._result("", tool_calls=self.tool_script.pop(0))
        return self._result("", text="done")

    def search_web(self, prompt, *, tier="fast", task="") -> LLMResult:
        """Tests register a handler under "web_search" returning the answer text (pages default to none)."""
        self.calls.append({"kind": "search", "task": task, "prompt": prompt})
        handler = self.handlers.get("web_search")
        out = handler(prompt, {}) if handler else ""
        text, pages = (out if isinstance(out, tuple) else (out, []))
        return self._result(prompt, text=str(text), data={"queries": [], "pages": pages})

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(t) for t in texts]
