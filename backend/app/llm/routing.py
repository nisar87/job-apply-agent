"""Which model does which task, and what happens when one fails (specification section 8).

Strong tasks (judging, writing, reading replies) go to Claude when a Claude key is set; fast tasks and
embeddings go to Gemini. If the first provider fails, the other answers the same call, so one provider's outage or
empty credit never stops the agent. A Claude key or credit problem pauses Claude for a few minutes, so the next
calls do not each wait for a refusal.
"""

import logging
import time

from app.llm.base import LLMResult, SpendCapExceeded, ToolSpec
from app.llm.claude import ClaudeClient, ClaudeUnavailable

log = logging.getLogger(__name__)
CLAUDE_PAUSE_SECONDS = 300
_ANNOUNCED: set[str] = set()


class RoutedLLM:
    def __init__(self, gemini, claude: ClaudeClient | None, clock=time.monotonic):
        self.gemini, self.claude, self._clock = gemini, claude, clock
        self._claude_paused_until = 0.0
        self.last_provider = ""

    def _claude_ready(self) -> bool:
        return self.claude is not None and self._clock() >= self._claude_paused_until

    def _order(self, tier: str) -> list[tuple[str, object]]:
        pair = [("gemini", self.gemini)]
        if self._claude_ready():
            pair.insert(0, ("claude", self.claude)) if tier == "strong" else pair.append(("claude", self.claude))
        return pair

    def _run(self, tier: str, call):
        last: Exception | None = None
        for name, client in self._order(tier):
            try:
                result = call(client)
                self.last_provider = name
                if name not in _ANNOUNCED:          # once per provider and process, so the log shows which model really answers
                    _ANNOUNCED.add(name)
                    log.warning("first answer from %s (model %s)", name, getattr(result, "model", ""))
                return result
            except SpendCapExceeded:
                raise
            except ClaudeUnavailable as exc:
                self._claude_paused_until = self._clock() + CLAUDE_PAUSE_SECONDS
                log.warning("Claude unavailable (%s); using Gemini for the next %s s", exc, CLAUDE_PAUSE_SECONDS)
                last = exc
            except Exception as exc:  # noqa: BLE001 - any provider failure: try the other one
                log.warning("%s failed (%s); trying the other provider", name, type(exc).__name__)
                last = exc
        raise last or RuntimeError("no model provider available")

    def generate(self, prompt: str, *, system: str | None = None, tier: str = "fast", task: str = "") -> LLMResult:
        return self._run(tier, lambda c: c.generate(prompt, system=system, tier=tier, task=task))

    def generate_structured(self, prompt: str, schema: dict, *, system: str | None = None, tier: str = "fast",
                            task: str = "") -> LLMResult:
        return self._run(tier, lambda c: c.generate_structured(prompt, schema, system=system, tier=tier, task=task))

    def generate_with_tools(self, messages: list[dict], tools: list[ToolSpec], *, system: str | None = None,
                            tier: str = "strong", task: str = "") -> LLMResult:
        return self._run(tier, lambda c: c.generate_with_tools(messages, tools, system=system, tier=tier, task=task))

    def search_web(self, prompt: str, *, tier: str = "fast", task: str = "") -> LLMResult:
        return self.gemini.search_web(prompt, tier=tier, task=task)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.gemini.embed(texts)


def with_claude(gemini_client):
    """The platform's client: Gemini alone, or routed with Claude when a Claude key is configured."""
    from app.config import get_settings

    if not get_settings().anthropic_api_key:
        return gemini_client
    return RoutedLLM(gemini_client, ClaudeClient())
