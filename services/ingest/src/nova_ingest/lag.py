"""Consumer lag (M6 exit: lag visible, lag alert configured). Every 15 s: per group and topic, the end offset
minus the committed offset, summed over partitions, upserted into `kafka_lag` (ops data, no tenant). A
group stays `alerting` while lag > KAFKA_LAG_ALERT for 60 s; the alert is a log event (`kafka_lag_alert`)
plus the flag the Admin screen shows. ponytail: no pager; a real deployment alerts from the same table."""

import asyncio
import time
from typing import Any

import structlog
from sqlalchemy import text

from nova_core import db
from nova_core.settings import get_settings

GROUPS = ("nova-router", "nova-notifier", "ch-shipment-events")
EVERY_S, SUSTAIN_S = 15, 60
log = structlog.get_logger()


def alerting(lag: int, threshold: int, since: float | None, now: float) -> tuple[bool, float | None]:
    """(alerting?, over-threshold since). Alert once lag has stayed over the threshold for SUSTAIN_S."""
    if lag <= threshold:
        return False, None
    since = since if since is not None else now
    return now - since >= SUSTAIN_S, since


async def measure(admin: Any, consumer: Any) -> list[tuple[str, str, int]]:
    out = []
    for group in GROUPS:
        try:
            committed = await admin.list_consumer_group_offsets(group)
        except Exception:  # noqa: BLE001 (group not created yet)
            log.debug("kafka_lag_no_group", group=group)
            continue
        if not committed:
            continue
        ends = await consumer.end_offsets(list(committed))
        per_topic: dict[str, int] = {}
        for tp, meta in committed.items():
            per_topic[tp.topic] = per_topic.get(tp.topic, 0) + max(0, ends.get(tp, 0) - max(meta.offset, 0))
        out += [(group, topic, lag) for topic, lag in per_topic.items()]
    return out


async def run() -> None:
    from aiokafka import AIOKafkaConsumer
    from aiokafka.admin import AIOKafkaAdminClient

    s = get_settings()
    admin = AIOKafkaAdminClient(bootstrap_servers=s.kafka_bootstrap)
    probe = AIOKafkaConsumer(bootstrap_servers=s.kafka_bootstrap)  # end offsets only; joins no group
    await admin.start()
    await probe.start()
    over: dict[tuple[str, str], float | None] = {}
    try:
        while True:
            now = time.monotonic()
            for group, topic, lag in await measure(admin, probe):
                alert, over[(group, topic)] = alerting(lag, s.kafka_lag_alert, over.get((group, topic)), now)
                if alert:
                    log.warning("kafka_lag_alert", group=group, topic=topic, lag=lag)
                async with db.session() as sess:
                    await sess.execute(
                        text("""insert into kafka_lag (group_id, topic, lag, alerting, measured_at)
                                values (:g, :t, :l, :a, now())
                                on conflict (group_id, topic) do update set lag = excluded.lag,
                                  alerting = excluded.alerting, measured_at = excluded.measured_at"""),
                        {"g": group, "t": topic, "l": lag, "a": alert},
                    )
            await asyncio.sleep(EVERY_S)
    finally:
        await probe.stop()
        await admin.close()
