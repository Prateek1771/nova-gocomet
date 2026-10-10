"""W3 event simulator (docs/04): plays definitions/seed/shipments.json onto Kafka `shipment.events`, keyed
`tenant_id:shipment_id`, in sim time (1 sim-day = SIM_SECONDS_PER_DAY real seconds, default 60). Event times
are sim times (sim_epoch + day offset), so the metrics' clock is the latest event, not the wall clock.
Every payload is validated against definitions/events/shipment_event.v1.json before it is sent.

  python -m nova_ingest.simulator            # real pace
  python -m nova_ingest.simulator --fast     # everything at once (tests, demos)
  python -m nova_ingest.simulator --reset    # first clear ClickHouse events + the tenants' W3 exceptions
  python -m nova_ingest.simulator --fast --until 11   # sim days (since, until]: a transient incident (the
                                                       # missed connection, days 10.5-12.2) stays visible

Replays are safe: event ids are deterministic (uuid5) and shipment_events is a ReplacingMergeTree.
"""

import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text

from nova_core import db
from nova_core.logging import configure_logging
from nova_core.settings import get_settings

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
TOPIC = "shipment.events"
log = structlog.get_logger()


def validator() -> Draft202012Validator:
    schema = json.loads((DEFINITIONS / "events" / "shipment_event.v1.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def events(seed: dict[str, Any], tenants: dict[str, str]) -> list[tuple[float, dict[str, Any]]]:
    """(sim day, payload) for every event of every tenant we know, in play order."""
    epoch = datetime.fromisoformat(seed["sim_epoch"])

    def at(day: float) -> str:
        return (epoch + timedelta(days=day)).isoformat().replace("+00:00", "Z")

    out = []
    for sh in seed["shipments"]:
        tid = tenants.get(sh["tenant"])
        if tid is None:
            continue
        for e in sh["events"]:
            out.append(
                (
                    e["day"],
                    {
                        "event_id": e["event_id"],
                        "tenant_id": tid,
                        "shipment_id": sh["id"],
                        "container_no": sh["container_no"],
                        "event_type": e["event_type"],
                        "location": e["location"],
                        "port_role": e["port_role"],
                        "vessel": e.get("vessel"),
                        "voyage": e.get("voyage"),
                        "booked_vessel": e.get("booked_vessel"),
                        "event_time": at(e["day"]),
                        "eta": at(e["eta"]) if e.get("eta") is not None else None,
                    },
                )
            )
    return sorted(out, key=lambda x: (x[0], x[1]["event_id"]))


async def tenant_ids() -> dict[str, str]:
    async with db.session() as s:
        return {r.slug: str(r.id) for r in (await s.execute(text("select id, slug from tenants"))).all()}


async def reset(tenants: dict[str, str]) -> None:
    """Demo reset: ClickHouse events go, and so do the tenants' W3 exceptions and their notifications, so
    the next replay raises them again (exceptions are once per shipment + type). ponytail: open triage
    runs are left to finish or be cancelled from the run page."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            s.clickhouse_url,
            headers={
                "X-ClickHouse-User": s.clickhouse_admin,
                "X-ClickHouse-Key": s.clickhouse_admin_password,
            },
            content="TRUNCATE TABLE IF EXISTS nova.shipment_events",
        )
        r.raise_for_status()
    for tid in tenants.values():
        async with db.tenant_session(uuid.UUID(tid)) as sess:
            await sess.execute(text("delete from notifications where topic = 'nova.outbox.shipment'"))
            await sess.execute(text("delete from exceptions"))
    log.info("sim_reset", tenants=sorted(tenants))


async def play(fast: bool, pace: float, since: float = -1.0, until: float = float("inf")) -> int:
    from aiokafka import AIOKafkaProducer  # imported here so the pure helpers above need no Kafka client

    seed = json.loads((DEFINITIONS / "seed" / "shipments.json").read_text(encoding="utf-8"))
    tenants = await tenant_ids()
    plan = [(d, e) for d, e in events(seed, tenants) if since < d <= until]
    check = validator()
    producer = AIOKafkaProducer(bootstrap_servers=get_settings().kafka_bootstrap, acks="all")
    await producer.start()
    try:
        prev = plan[0][0] if plan else 0.0
        for day, ev in plan:
            if not fast and day > prev:
                await asyncio.sleep((day - prev) * pace)
            prev = day
            errors = [e.message for e in check.iter_errors(ev)]
            if errors:
                raise ValueError(f"event {ev['event_id']} breaks shipment_event.v1: {errors[:3]}")
            await producer.send_and_wait(
                TOPIC, json.dumps(ev).encode(), key=f"{ev['tenant_id']}:{ev['shipment_id']}".encode()
            )
            log.info("sim_event", day=day, type=ev["event_type"], container=ev["container_no"])
    finally:
        await producer.stop()
    return len(plan)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="no pacing: send everything at once")
    ap.add_argument("--reset", action="store_true", help="clear events + W3 exceptions first")
    ap.add_argument("--since", type=float, default=-1.0, help="only events after this sim day")
    ap.add_argument("--until", type=float, default=float("inf"), help="only events up to this sim day")
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level)
    if a.reset:
        await reset(await tenant_ids())
    n = await play(a.fast, s.sim_seconds_per_day, a.since, a.until)
    log.info("sim_done", events=n)


if __name__ == "__main__":
    asyncio.run(main())
