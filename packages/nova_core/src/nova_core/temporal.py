"""Temporal names shared by the API (client) and the engine (worker). Services can't import each
other, so the contract lives here and the API addresses the workflow by name."""

from typing import Any

import structlog
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from nova_core.settings import get_settings

ENGINE_QUEUE = "nova-engine"
AGENTS_QUEUE = "nova-agents"  # agent / decide activities (agents-worker, M2)
WORKFLOW = "NovaWorkflow"
CLAIM_TASK, COMPLETE_TASK, EVENT = "claim_task", "complete_task", "event"

_client: Client | None = None


async def client() -> Client:
    global _client
    if _client is None:
        # TracingInterceptor also applies to workers built on this client: one trace spans
        # API -> workflow -> activities. No-op without a tracer provider.
        _client = await Client.connect(
            get_settings().temporal_host, namespace="default", interceptors=[TracingInterceptor()]
        )
    return _client


class _BindLogContext(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        # every activity payload is a dataclass carrying tenant_id / run_id (contracts.py)
        arg = input.args[0] if input.args else None
        ids = {k: str(v) for k in ("tenant_id", "run_id", "node_id") if (v := getattr(arg, k, None))}
        with structlog.contextvars.bound_contextvars(**ids):
            return await super().execute_activity(input)


class LogContext(Interceptor):
    """Worker interceptor: activity logs carry tenant_id / run_id / node_id."""

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _BindLogContext(next)
