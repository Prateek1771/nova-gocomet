"""Registered side effects (`action` nodes). Business integrations (tms.*, erp.*) register here too.
Each action gets its params plus the request (tenant, run, node, idempotency key), never a raw session:
anything it writes goes through a tenant-scoped transaction."""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text

from nova_core import db
from nova_core.registry import Registry
from nova_engine.contracts import ActionRequest

Action = Callable[[dict[str, Any], ActionRequest], Awaitable[Any]]
ACTIONS: Registry[Action] = Registry("action")
log = structlog.get_logger()


@ACTIONS.register("noop")
async def noop(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    return {}


@ACTIONS.register("notify.log")
async def notify_log(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    # ponytail: logs instead of email/Slack; a real notifier registers under notify.email etc.
    log.info("notify", **params)
    return {"delivered": True}


@ACTIONS.register("tms.upsert_shipment")
async def tms_upsert_shipment(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """Mock TMS: upsert one shipment per BoL into our own table. A real adapter would call the client's
    TMS API with ctx.idempotency_key as its idempotency header."""
    f = params.get("fields") or {}
    if not f.get("bol_number"):
        raise ValueError("tms.upsert_shipment needs fields.bol_number")
    containers = f.get("container_numbers") or []
    async with db.tenant_session(uuid.UUID(ctx.tenant_id)) as s:
        sid = (
            await s.execute(
                text("""insert into shipments (tenant_id, bol_number, container_no, pol, pod, status, fields)
                        values (:t, :b, :c, :pol, :pod, 'booked', cast(:f as jsonb))
                        on conflict (tenant_id, bol_number) do update set
                          container_no = excluded.container_no, pol = excluded.pol, pod = excluded.pod,
                          fields = excluded.fields
                        returning id"""),
                {
                    "t": ctx.tenant_id,
                    "b": f["bol_number"],
                    "c": containers[0] if containers else "",
                    "pol": f.get("pol"),
                    "pod": f.get("pod"),
                    "f": json.dumps(f),
                },
            )
        ).scalar_one()
    return {"shipment_id": str(sid), "bol_number": f["bol_number"]}
