"""Engine worker: `uv run python -m nova_engine.worker`."""

import asyncio

from temporalio.worker import Worker

from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.temporal import ENGINE_QUEUE, client
from nova_engine import activities
from nova_engine.interpreter import NovaWorkflow


async def main() -> None:
    configure_logging(get_settings().log_level)
    worker = Worker(
        await client(), task_queue=ENGINE_QUEUE, workflows=[NovaWorkflow], activities=activities.ALL
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
