"""Trigger router (docs/04 W3, ADR-036): Postgres CDC -> workflow runs. A new `exceptions` row (Debezium,
`cdc.nova.public.exceptions`) starts every published workflow of that tenant whose trigger is
`{type: event, event: exception.opened}`, with input {exception_id} and subject ("exception", id).

Exactly one run per exception, however often the record is delivered (replays, rebalances, snapshot
re-reads): the unique index `workflow_runs_one_per_exception` rejects a second run row, and the run row,
the exceptions.run_id link and the Temporal start commit together, so the offset is committed only after
all three happened. Generic: the router knows event names, never a process."""

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from nova_core import db
from nova_core import temporal as t
from nova_core.runs import create_run

TOPIC = "cdc.nova.public.exceptions"
EVENT = "exception.opened"
log = structlog.get_logger()


def opened(rec: dict[str, Any]) -> bool:
    """A record that should trigger: created (or snapshot-read) and not yet linked to a run."""
    return rec.get("__op") in ("c", "r") and rec.get("status") == "open" and not rec.get("run_id")


async def subscribers(tenant_id: uuid.UUID, event: str) -> list[str]:
    async with db.tenant_session(tenant_id) as s:
        rows = (
            await s.execute(
                text("""select distinct on (key) key, compiled from workflow_definitions
                        where status = 'published' order by key, version desc""")
            )
        ).all()
    return [
        r.key
        for r in rows
        if (r.compiled.get("trigger") or {}).get("type") == "event"
        and (r.compiled.get("trigger") or {}).get("event") == event
    ]


async def handle(rec: dict[str, Any], client: Client) -> list[str]:
    """Start the subscribed workflows for one CDC record. Returns the started run ids (empty = no-op)."""
    if not opened(rec):
        return []
    tenant, eid = uuid.UUID(rec["tenant_id"]), str(rec["id"])
    started = []
    for key in await subscribers(tenant, EVENT):
        try:
            async with db.tenant_session(tenant) as s:
                run = await create_run(s, tenant, key, {"exception_id": eid}, subject=("exception", eid))
                await s.execute(
                    text("update exceptions set run_id = :r where id = cast(:e as uuid) and run_id is null"),
                    {"r": run.run_id, "e": eid},
                )
                await s.flush()  # the unique subject index fires here, before Temporal hears of the run
                req = {
                    "run_id": str(run.run_id),
                    "tenant_id": str(tenant),
                    "definition_id": str(run.definition_id),
                    "config_version": run.config_version,
                    "input": {"exception_id": eid},
                }
                await client.start_workflow(t.WORKFLOW, req, id=run.workflow_id, task_queue=t.ENGINE_QUEUE)
            started.append(str(run.run_id))
            log.info("triggered", on=EVENT, workflow=key, exception_id=eid, run_id=str(run.run_id))
        except IntegrityError:
            log.info("trigger_duplicate", workflow=key, exception_id=eid)  # already triaged: replay
        except WorkflowAlreadyStartedError:
            log.info("trigger_duplicate", workflow=key, exception_id=eid)
    return started


def decode(value: bytes | None) -> dict[str, Any] | None:
    if not value:
        return None  # tombstone
    rec = json.loads(value)
    return rec if isinstance(rec, dict) else None


async def run() -> None:
    from aiokafka import AIOKafkaConsumer

    from nova_core.settings import get_settings

    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id="nova-router",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    client = await t.client()
    await consumer.start()
    try:
        async for msg in consumer:
            rec = decode(msg.value)
            if rec is not None:
                await handle(rec, client)  # raises on Postgres/Temporal outage: no commit, redelivered
            await consumer.commit()
    finally:
        await consumer.stop()
