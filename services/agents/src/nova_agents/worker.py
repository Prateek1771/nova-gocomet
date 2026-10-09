"""Agents worker: `uv run python -m nova_agents.worker`."""

import asyncio

from temporalio.worker import Worker

from nova_agents.activities import ALL
from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.telemetry import configure_tracing
from nova_core.temporal import AGENTS_QUEUE, LogContext, client


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    configure_tracing("nova-agents", s.otel_exporter_otlp_endpoint)
    worker = Worker(
        await client(),
        task_queue=AGENTS_QUEUE,
        activities=ALL,
        interceptors=[LogContext()],
        max_concurrent_activities=4,  # PDF parsing + LLM calls; the box has 4 cores
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
