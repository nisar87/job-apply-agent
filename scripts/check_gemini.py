"""Check the real Gemini connection with your own key. Run from the `backend` folder:

    python ../scripts/check_gemini.py

It makes four small calls (text, structured output, tool calling, embeddings) and
prints what came back. Cost: a fraction of a cent.
"""

import sys

from app.config import get_settings
from app.llm.base import ToolSpec
from app.llm.gemini import GeminiClient


def main() -> int:
    s = get_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY is empty. Put your key in the .env file in the project folder.")
        return 1
    print(f"Models: fast={s.gemini_model_fast}, strong={s.gemini_model_strong}, embed={s.gemini_model_embed}")
    client = GeminiClient()
    failures = 0

    def step(name, fn):
        nonlocal failures
        try:
            print(f"[ok]   {name}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {str(exc)[:300]}")

    step("text", lambda: client.generate("Reply with the single word: pong").text.strip()[:40])
    step("structured output", lambda: client.generate_structured(
        "Extract: Maria is 31 years old.",
        {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}, "required": ["name", "age"]},
    ).data)
    step("tool calling", lambda: [(c.name, c.args) for c in client.generate_with_tools(
        [{"role": "user", "text": "What is the status? Use the tool."}],
        [ToolSpec("get_status", "Returns today's counters.", {"type": "object", "properties": {}})],
    ).tool_calls])
    step("embeddings", lambda: f"{len(client.embed(['python developer'])[0])} dimensions")
    if failures:
        print("\nIf a model name is rejected, set GEMINI_MODEL_FAST / GEMINI_MODEL_STRONG / GEMINI_MODEL_EMBED in .env "
              "to names your key can use (see https://ai.google.dev/gemini-api/docs/models).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
