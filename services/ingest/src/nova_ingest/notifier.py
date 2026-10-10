"""Outbox notifier: the mock delivery sink (docs/04 W3 acceptance). Debezium's EventRouter publishes every
outbox row to `nova.outbox.<aggregate>`; this consumer takes the `email.*` ones, validates each against
definitions/events/outbox_event.v1.json and stores it in `notifications` (RLS), once per outbox id
(`unique (tenant_id, outbox_id)`), so a replay is a no-op. A real mailer would send here."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from sqlalchemy import text

from nova_core import db

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
PATTERN = re.compile(r"^nova\.outbox\..+")
log = structlog.get_logger()
_check = Draft202012Validator(
    json.loads((DEFINITIONS / "events" / "outbox_event.v1.json").read_text(encoding="utf-8"))
)


def header(headers: list[tuple[str, bytes]] | tuple[tuple[str, bytes], ...], name: str) -> str | None:
    for k, v in headers or ():
        if k == name and v is not None:
            return v.decode().strip('"')  # Connect's header converter may JSON-quote strings
    return None


async def store(topic: str, headers: Any, value: bytes | None) -> bool:
    """Record one outbox message. Returns False when it was invalid or already stored."""
    tenant, oid, kind = header(headers, "tenant_id"), header(headers, "id"), header(headers, "type")
    if not (value and tenant and oid):
        log.warning("outbox_skipped", topic=topic, reason="missing tenant/id header or body")
        return False
    if not (kind or "").startswith("email."):
        return False  # other outbox events (e.g. nova.outbox.run) aren't messages for the sink
    payload = json.loads(value)
    if isinstance(payload, str):  # payload not expanded: a JSON string holding the object
        payload = json.loads(payload)
    errors = [e.message for e in _check.iter_errors(payload)]
    if errors:
        log.warning("outbox_invalid", topic=topic, outbox_id=oid, errors=errors[:3])
        return False
    refs = payload.get("refs") or {k: payload[k] for k in ("clause_ids",) if k in payload}
    async with db.tenant_session(uuid.UUID(tenant)) as s:
        row = (
            await s.execute(
                text("""insert into notifications
                          (tenant_id, outbox_id, topic, type, recipient, subject, body, refs)
                        values (:t, :o, :topic, :type, :to, :subj, :body, cast(:refs as jsonb))
                        on conflict (tenant_id, outbox_id) do nothing returning id"""),
                {
                    "t": tenant,
                    "o": oid,
                    "topic": topic,
                    "type": kind or "",
                    "to": payload["to"],
                    "subj": payload["subject"],
                    "body": payload.get("body") or "",
                    "refs": json.dumps({**refs, "run_id": payload.get("run_id")}),
                },
            )
        ).scalar_one_or_none()
    if row:
        log.info("notified", topic=topic, type=kind, to=payload["to"])
    return row is not None


async def run() -> None:
    from aiokafka import AIOKafkaConsumer

    from nova_core.settings import get_settings

    consumer = AIOKafkaConsumer(
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id="nova-notifier",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        metadata_max_age_ms=30_000,  # pick up nova.outbox.<new aggregate> topics within 30 s
    )
    consumer.subscribe(pattern=PATTERN.pattern)
    await consumer.start()
    try:
        async for msg in consumer:
            await store(msg.topic, msg.headers, msg.value)
            await consumer.commit()
    finally:
        await consumer.stop()
