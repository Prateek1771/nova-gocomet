"""Engine worker: `uv run python -m nova_engine.worker`."""

import asyncio

from temporalio.worker import Worker

from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.telemetry import configure_tracing
from nova_core.temporal import ENGINE_QUEUE, LogContext, client
from nova_engine import activities
from nova_engine.interpreter import NovaWorkflow
from nova_engine.scheduled import ScheduledRun


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    configure_tracing("nova-engine", s.otel_exporter_otlp_endpoint)
    worker = Worker(
        await client(),
        task_queue=ENGINE_QUEUE,
        workflows=[NovaWorkflow, ScheduledRun],
        activities=activities.ALL,
        interceptors=[LogContext()],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
