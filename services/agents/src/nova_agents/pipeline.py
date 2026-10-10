"""The governed-agent template (FR-2.1, LLD §3.1): scope → context → route → execute → deliver, as one
linear LangGraph. Every agent is an AgentSpec; the stages around it are the governance:

- scope:   who/what this run may touch (tenant, run, node). ponytail: the OpenFGA ListObjects filter
           joins in M4; until then the tenant-scoped session (RLS) is the boundary.
- context: only the sources the spec asks for, loaded under that tenant.
- route:   model alias + schema version from the spec's tier (aliases only, rule 6).
- execute: the agent's own work (tools / LLM calls).
- deliver: output validated against the spec's schema, one repair, else a non-retryable failure that the
           engine turns into a needs_attention human task (09 §4). Cost is booked on the run.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text

from nova_agents.llm import Completion
from nova_core import db

ALIASES = {
    "extract": "nova-extract-text",
    "vision": "nova-extract-vision",
    "scan": "nova-extract-scan",  # LandingAI DPT-2 Parse + Extract behind the gateway (ADR-034)
    "reason": "nova-reason",
}


class AgentError(Exception):
    """Deterministic failure (bad output after repair, missing input): retrying won't help."""


class State(TypedDict, total=False):
    req: dict[str, Any]  # AgentRequest as a dict: tenant_id, run_id, node_id, name, params
    scope: dict[str, Any]
    ctx: dict[str, Any]
    route: dict[str, Any]
    output: dict[str, Any]
    meta: dict[str, Any]


Step = Callable[[State], Awaitable[dict[str, Any]]]


@dataclass
class AgentSpec:
    key: str
    tier: str  # extract | reason
    output_schema: dict[str, Any]
    context: Step
    execute: Step
    repair: Callable[[State, list[str]], Awaitable[dict[str, Any]]] | None = None
    persist: Callable[[State], Awaitable[None]] | None = None


def record(state: State, c: Completion) -> None:
    """Book one LLM call on the run's meta (cost per run, FR-2.4)."""
    meta = state.setdefault("meta", {"cost_usd": 0.0, "calls": []})
    meta["cost_usd"] = round(meta.get("cost_usd", 0.0) + c.cost_usd, 6)
    meta.setdefault("calls", []).append(
        {
            "model": c.model,
            "ms": c.ms,
            "tokens_in": c.tokens_in,
            "tokens_out": c.tokens_out,
            "cost_usd": c.cost_usd,
        }
    )


async def book_cost(tenant_id: uuid.UUID, run_id: str, cost: float) -> None:
    if cost:
        async with db.tenant_session(tenant_id) as sess:
            await sess.execute(
                text("""update workflow_runs set cost_usd = coalesce(cost_usd, 0) + :c
                        where id = cast(:r as uuid)"""),
                {"c": cost, "r": run_id},
            )


def schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in sorted(Draft202012Validator(schema).iter_errors(value), key=str)
    ][:20]


def build(spec: AgentSpec) -> Any:
    async def scope(s: State) -> dict[str, Any]:
        req = s["req"]
        try:
            tenant = uuid.UUID(req["tenant_id"])
            uuid.UUID(req["run_id"])
        except (KeyError, ValueError) as e:
            raise AgentError(f"bad scope: {e}") from e
        return {"scope": {"tenant_id": tenant, "run_id": req["run_id"], "node_id": req["node_id"]}}

    async def context(s: State) -> dict[str, Any]:
        return {"ctx": await spec.context(s)}

    async def route(s: State) -> dict[str, Any]:
        params = s["req"].get("params") or {}
        return {
            "route": {
                "alias": ALIASES[spec.tier],
                "vision_alias": ALIASES["vision"],
                "schema": params.get("schema"),
            }
        }

    async def execute(s: State) -> dict[str, Any]:
        out = await spec.execute(s)
        return {"output": out, "meta": s.get("meta", {"cost_usd": 0.0, "calls": []})}

    async def deliver(s: State) -> dict[str, Any]:
        out = s["output"]
        errors = schema_errors(spec.output_schema, out)
        if errors and spec.repair:
            out = await spec.repair(s, errors)
            errors = schema_errors(spec.output_schema, out)
        if errors:
            raise AgentError(f"{spec.key} output invalid after repair: {errors[:3]}")
        s["output"] = out
        if spec.persist:
            await spec.persist(s)
        meta = s.get("meta") or {"cost_usd": 0.0, "calls": []}
        await book_cost(s["scope"]["tenant_id"], s["scope"]["run_id"], meta.get("cost_usd", 0.0))
        return {"output": {**out, "meta": meta}}

    g = StateGraph(State)
    for name, fn in (
        ("scope", scope),
        ("context", context),
        ("route", route),
        ("execute", execute),
        ("deliver", deliver),
    ):
        g.add_node(name, fn)  # type: ignore[call-overload]  # overloads miss async TypedDict nodes
    g.add_edge(START, "scope")
    g.add_edge("scope", "context")
    g.add_edge("context", "route")
    g.add_edge("route", "execute")
    g.add_edge("execute", "deliver")
    g.add_edge("deliver", END)
    return g.compile()
