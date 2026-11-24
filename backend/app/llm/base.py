"""One interface for every model call (master document, sections 3 and 4).

The rest of the application never imports a provider SDK. It asks for a tier
("fast" or "strong"); settings map tiers to model names.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UsageDaily


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMResult:
    text: str = ""
    data: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: Any = None  # provider message, passed back unchanged in tool conversations


log = logging.getLogger(__name__)


class LLMClient(Protocol):
    def generate(self, prompt: str, *, system: str | None = None, tier: str = "fast", task: str = "") -> LLMResult: ...

    def generate_structured(
        self, prompt: str, schema: dict, *, system: str | None = None, tier: str = "fast", task: str = ""
    ) -> LLMResult: ...

    def generate_with_tools(
        self, messages: list[dict], tools: list[ToolSpec], *, system: str | None = None,
        tier: str = "strong", task: str = "",
    ) -> LLMResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SpendCapExceeded(Exception):
    """The user's daily model-spend cap is reached (master document, section 10)."""


SEARCH_FEE_USD = 0.035      # Google Search grounding, per search the model runs (an estimate; check the bill)
EMBED_USD_PER_MILLION = 0.15


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = get_settings().llm_prices.get(model)
    if not prices:
        return 0.0
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


class MeteredLLM:
    """Wraps a client for one user: records tokens and cost, and enforces the daily cap."""

    def __init__(self, client: LLMClient, session: Session, user_id: uuid.UUID, cap_usd: float | None = None):
        self.client = client
        self.session = session
        self.user_id = user_id
        self.cap_usd = self._default_cap() if cap_usd is None else cap_usd
        self.run_cost = 0.0

    def _default_cap(self) -> float:
        """The user's cap for the key their work runs on (admin default, per-user override, or their own)."""
        from app.llm import access
        from app.models import User

        user = self.session.get(User, self.user_id)
        if user is None:
            return get_settings().llm_daily_spend_cap_usd
        try:
            return access.cap_for(user)
        except Exception:  # noqa: BLE001 - settings store unreachable: fall back to the configured cap
            return get_settings().llm_daily_spend_cap_usd

    def _usage_row(self) -> UsageDaily:
        today = datetime.now(UTC).date()
        row = self.session.execute(
            select(UsageDaily).where(UsageDaily.user_id == self.user_id, UsageDaily.day == today)
        ).scalar_one_or_none()
        if row is None:
            row = UsageDaily(user_id=self.user_id, day=today, input_tokens=0, output_tokens=0, cost_usd=0.0, calls=0)
            self.session.add(row)
            self.session.flush()
        return row

    def _check_cap(self) -> None:
        if self.cap_usd and self._usage_row().cost_usd >= self.cap_usd:
            raise SpendCapExceeded(f"daily model spend cap of ${self.cap_usd:.2f} reached")

    def _record(self, result: LLMResult, task: str = "", extra_cost: float = 0.0) -> LLMResult:
        row = self._usage_row()
        cost = cost_of(result.model, result.input_tokens, result.output_tokens) + extra_cost
        row.input_tokens += result.input_tokens
        row.output_tokens += result.output_tokens
        row.cost_usd += cost
        row.calls += 1
        self.run_cost += cost
        self.session.flush()
        # One line per call, so the logs show which task spends what (read with the Staging logs workflow).
        log.info("llm_call task=%s model=%s in=%s out=%s cost=%.5f", task or "?", result.model, result.input_tokens,
                 result.output_tokens, cost)
        return result

    def generate(self, prompt, **kw) -> LLMResult:
        self._check_cap()
        return self._record(self.client.generate(prompt, **kw), kw.get("task", ""))

    def generate_structured(self, prompt, schema, **kw) -> LLMResult:
        self._check_cap()
        return self._record(self.client.generate_structured(prompt, schema, **kw), kw.get("task", ""))

    def generate_with_tools(self, messages, tools, **kw) -> LLMResult:
        self._check_cap()
        return self._record(self.client.generate_with_tools(messages, tools, **kw), kw.get("task", ""))

    def search_web(self, prompt, **kw) -> LLMResult:
        self._check_cap()
        result = self.client.search_web(prompt, **kw)
        # Google charges for each search the model runs, on top of the tokens; counted here so the cap sees it.
        searches = len((result.data or {}).get("queries", [])) or 1
        return self._record(result, "web_search", extra_cost=searches * SEARCH_FEE_USD)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = self.client.embed(texts)
        tokens = sum(len(t) for t in texts) // 4
        cost = tokens * EMBED_USD_PER_MILLION / 1_000_000
        row = self._usage_row()
        row.input_tokens += tokens
        row.cost_usd += cost
        row.calls += 1
        self.run_cost += cost
        log.info("llm_call task=embed model=embedding in=%s out=0 cost=%.5f", tokens, cost)
        return out
