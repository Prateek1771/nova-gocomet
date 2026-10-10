"""Temporal activities on the nova-agents queue (contract: nova_core.temporal.AgentRequest).
Deterministic failures are non-retryable so the engine routes them to a human (needs_attention);
transport errors (LLM gateway down, timeouts) stay retryable."""

import uuid
from dataclasses import asdict
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nova_agents import exceptions, extractor, invoices, validator
from nova_agents.decide import DecideError
from nova_agents.decide import decide as run_decide
from nova_agents.llm import BudgetExceeded
from nova_agents.pipeline import AgentError, book_cost, build
from nova_core.temporal import AgentRequest

SPECS = (
    extractor.SPEC,
    validator.SPEC,
    invoices.DEDUPE,
    invoices.SPEC,
    exceptions.ANALYST_SPEC,
    exceptions.RECOMMENDER_SPEC,
)
AGENTS = {spec.key: build(spec) for spec in SPECS}


@activity.defn(name="run_agent")
async def run_agent(req: AgentRequest) -> dict[str, Any]:
    graph = AGENTS.get(req.name)
    if graph is None:
        raise ApplicationError(f"unknown agent {req.name!r}", non_retryable=True)
    try:
        state = await graph.ainvoke({"req": asdict(req), "meta": {"cost_usd": 0.0, "calls": []}})
    except AgentError as e:
        raise ApplicationError(str(e), type="AgentError", non_retryable=True) from e
    except BudgetExceeded as e:  # retrying can't help; a human raises the budget, then retries the step
        raise ApplicationError(str(e), type="BudgetExceeded", non_retryable=True) from e
    return dict(state["output"])


@activity.defn(name="decide")
async def decide(req: AgentRequest) -> dict[str, Any]:
    try:
        out = await run_decide(
            req.params.get("questions") or [], {"tenant_id": req.tenant_id, "run_id": req.run_id}
        )
    except DecideError as e:
        raise ApplicationError(str(e), type="DecideError", non_retryable=True) from e
    except BudgetExceeded as e:
        raise ApplicationError(str(e), type="BudgetExceeded", non_retryable=True) from e
    await book_cost(uuid.UUID(req.tenant_id), req.run_id, out["meta"]["cost_usd"])
    return out


ALL = [run_agent, decide]
