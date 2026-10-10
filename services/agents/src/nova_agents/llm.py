"""LiteLLM proxy client. Code names aliases only (rule 6); the proxy maps them per LLM_MODE.
Tests pass a transport that replays recorded responses (no live calls in unit tests)."""

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from opentelemetry import trace
from temporalio import activity

from nova_core.llm_keys import tenant_key
from nova_core.settings import get_settings

tracer = trace.get_tracer("nova.llm")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    """The tenant's LiteLLM budget is spent. Not retryable: the engine opens a needs_attention task."""


def _fail(what: str, r: httpx.Response) -> LLMError:
    # LiteLLM has answered a spent budget with 400, 429 and (current) 422: match the message, not the code
    if 400 <= r.status_code < 500 and "budget" in r.text.lower() and "exceed" in r.text.lower():
        return BudgetExceeded(f"{what}: LLM budget exhausted ({r.text[:200]})")
    return LLMError(f"{what}: HTTP {r.status_code} {r.text[:300]}")


def _auth(tenant_id: str | None) -> dict[str, str]:
    """The tenant's virtual key when there is one, so spend and budget are per tenant (ADR-028)."""
    key = tenant_key(tenant_id) if tenant_id else None
    return {"Authorization": f"Bearer {key}"} if key else {}


@dataclass
class Completion:
    content: str
    model: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    ms: int

    def json(self) -> Any:
        return parse_json(self.content)


def parse_json(text: str) -> Any:
    """Models wrap JSON in fences or prose now and then; take the outermost object."""
    body = _FENCE.sub("", text.strip())
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"no JSON object in model output: {text[:200]!r}") from None
        try:
            return json.loads(body[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"unparseable JSON from model: {e}") from e


class LLM:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        self._http = httpx.AsyncClient(
            base_url=s.llm_base_url,
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            timeout=httpx.Timeout(180, connect=10),
            transport=transport,
        )

    async def chat(
        self,
        alias: str,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = True,
        max_tokens: int = 2000,
        metadata: dict[str, str] | None = None,
        fresh: bool = False,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": alias,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "metadata": metadata or {},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if fresh or _retrying():  # a cached bad answer must not be replayed to every retry
            body["cache"] = {"no-cache": True}
        with tracer.start_as_current_span(f"llm {alias}") as span:
            t0 = time.perf_counter()
            r = await self._http.post(
                "/chat/completions", json=body, headers=_auth((metadata or {}).get("tenant_id"))
            )
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                span.set_attribute("error", True)
                raise _fail(alias, r)
            data = r.json()
            usage = data.get("usage") or {}
            c = Completion(
                content=data["choices"][0]["message"].get("content") or "",
                model=data.get("model", alias),
                cost_usd=float(r.headers.get("x-litellm-response-cost") or 0),
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                ms=ms,
            )
            attrs: dict[str, str | float | int] = {
                "llm.alias": alias,
                "llm.model": c.model,
                "llm.cost_usd": c.cost_usd,
                "llm.tokens_in": c.tokens_in,
                "llm.tokens_out": c.tokens_out,
                # OTel GenAI conventions: Langfuse (ai profile) renders these as a generation (ADR-029).
                # Ids, model, tokens and cost only; prompts and document content stay out of traces.
                "langfuse.observation.type": "generation",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": alias,
                "gen_ai.response.model": c.model,
                "gen_ai.usage.input_tokens": c.tokens_in,
                "gen_ai.usage.output_tokens": c.tokens_out,
                "gen_ai.usage.cost": c.cost_usd,
            }
            for k in ("tenant_id", "run_id"):
                if (metadata or {}).get(k):
                    attrs[f"nova.{k}"] = str((metadata or {})[k])
            for k, v in attrs.items():
                span.set_attribute(k, v)
            return c

    async def decisions(self, body: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
        """Typed decisions (Jev) via the gateway's pass-through; returns the provider's JSON."""
        with tracer.start_as_current_span("llm jev decisions") as span:
            t0 = time.perf_counter()
            r = await self._http.post("/jev/decisions", json=body, timeout=30, headers=_auth(tenant_id))
            span.set_attribute("llm.ms", int((time.perf_counter() - t0) * 1000))
            if r.status_code >= 400:
                span.set_attribute("error", True)
                raise _fail("jev decisions", r)
            out: dict[str, Any] = r.json()
            span.set_attribute("llm.cost_usd", float((out.get("usage") or {}).get("cost") or 0))
            return out


async def chat_json(alias: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[Completion, Any]:
    """chat + parse. Providers now and then return a cut-off body that the gateway caches; on an
    unparseable answer ask once more past the cache before giving up (retryable LLMError)."""
    c = await client().chat(alias, messages, **kw)
    try:
        return c, c.json()
    except LLMError:
        c2 = await client().chat(alias, messages, **{**kw, "fresh": True})
        c2.cost_usd += c.cost_usd
        return c2, c2.json()


def _retrying() -> bool:
    try:
        return activity.info().attempt > 1
    except RuntimeError:  # not inside an activity (tests, scripts)
        return False


_client: LLM | None = None


def client() -> LLM:
    global _client
    if _client is None:
        _client = LLM()
    return _client


def use(llm: LLM | None) -> None:
    """Swap the process-wide client (tests replay recorded responses through a transport)."""
    global _client
    _client = llm
