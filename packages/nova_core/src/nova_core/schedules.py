"""Workflows with `trigger: {type: schedule, cron}` run on a Temporal Schedule (ADR-036). One schedule per
tenant + workflow key; each firing starts the engine's generic SCHEDULED_RUN workflow, which creates a
normal run on the latest published version and config (so a publish changes the next firing, never the
current one). The API syncs a schedule on publish and reconciles all of them at startup."""

import uuid
from typing import Any

import structlog
from sqlalchemy import text
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError, RPCStatusCode

from nova_core import db
from nova_core.temporal import ENGINE_QUEUE, client

SCHEDULED_RUN = "ScheduledRun"
log = structlog.get_logger()


def schedule_id(tenant_id: uuid.UUID | str, key: str) -> str:
    return f"sched-{tenant_id}-{key}"


def build(tenant_id: uuid.UUID | str, key: str, cron: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            SCHEDULED_RUN,
            {"tenant_id": str(tenant_id), "key": key},
            id=f"{schedule_id(tenant_id, key)}-fire",  # Temporal suffixes the firing time
            task_queue=ENGINE_QUEUE,
        ),
        spec=ScheduleSpec(cron_expressions=[cron]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),  # a slow cycle never stacks up
    )


async def sync(tenant_id: uuid.UUID | str, key: str, cron: str | None, c: Client | None = None) -> str:
    """Create, update or delete one schedule to match the definition. Returns what it did."""
    c = c or await client()
    handle = c.get_schedule_handle(schedule_id(tenant_id, key))
    if cron is None:
        try:
            await handle.delete()
            return "deleted"
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
            return "none"
    new = build(tenant_id, key, cron)

    def _update(_: ScheduleUpdateInput) -> ScheduleUpdate:
        return ScheduleUpdate(schedule=new)

    try:
        await handle.update(_update)
        return "updated"
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    await c.create_schedule(schedule_id(tenant_id, key), new)
    return "created"


def cron_of(compiled: dict[str, Any]) -> str | None:
    t = compiled.get("trigger") or {}
    return t.get("cron") if t.get("type") == "schedule" else None


async def reconcile() -> int:
    """Every tenant's latest published definitions -> schedules. Returns how many are scheduled."""
    async with db.session() as s:
        tenants = (await s.execute(text("select id from tenants"))).scalars().all()
    n = 0
    for tid in tenants:
        async with db.tenant_session(tid) as s:
            rows = (
                await s.execute(
                    text("""select distinct on (key) key, compiled from workflow_definitions
                            where status = 'published' order by key, version desc""")
                )
            ).all()
        for r in rows:  # a definition that dropped its schedule trigger loses its schedule
            cron = cron_of(r.compiled)
            await sync(tid, r.key, cron)
            n += cron is not None
    log.info("schedules_reconciled", count=n)
    return n
