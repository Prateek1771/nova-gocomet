"""The ingest service: trigger router + outbox notifier + consumer-lag readings in one process.
`python -m nova_ingest`. Any loop failing stops the process; compose restarts it and Kafka redelivers
from the last committed offsets."""

import asyncio

from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.telemetry import configure_tracing
from nova_ingest import lag, notifier, router


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    configure_tracing("nova-ingest", s.otel_exporter_otlp_endpoint)
    await asyncio.gather(router.run(), notifier.run(), lag.run())


if __name__ == "__main__":
    asyncio.run(main())
