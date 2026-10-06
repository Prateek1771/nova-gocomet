"""Temporal names shared by the API (client) and the engine (worker). Services can't import each
other, so the contract lives here and the API addresses the workflow by name."""

from temporalio.client import Client

from nova_core.settings import get_settings

ENGINE_QUEUE = "nova-engine"
AGENTS_QUEUE = "nova-agents"  # agent / decide activities (agents-worker, M2)
WORKFLOW = "NovaWorkflow"
CLAIM_TASK, COMPLETE_TASK, EVENT = "claim_task", "complete_task", "event"

_client: Client | None = None


async def client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(get_settings().temporal_host, namespace="default")
    return _client
