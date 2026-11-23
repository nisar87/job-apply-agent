from app.config import get_settings
from app.llm.base import LLMClient, LLMResult, MeteredLLM, SpendCapExceeded, ToolCall, ToolSpec

_override: LLMClient | None = None


def set_llm_override(client: LLMClient | None) -> None:
    """Tests install a fake client here."""
    global _override
    _override = client


def get_llm() -> LLMClient:
    if _override is not None:
        return _override
    s = get_settings()
    if s.llm_provider == "fake":
        from app.llm.demo import demo_llm

        return demo_llm()
    from app.llm.gemini import GeminiClient
    from app.llm.routing import with_claude

    return with_claude(GeminiClient())


__all__ = ["LLMClient", "LLMResult", "MeteredLLM", "SpendCapExceeded", "ToolCall", "ToolSpec", "get_llm", "set_llm_override"]
