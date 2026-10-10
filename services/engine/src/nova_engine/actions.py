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


@ACTIONS.register("erp.post_payable")
async def erp_post_payable(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """Mock ERP: record the approved invoice as a posted payable (one row per carrier + invoice number, so a
    replay or a second approval can't post it twice). A real adapter calls the ERP's AP API with
    ctx.idempotency_key."""
    f = params.get("fields") or {}
    if not f.get("invoice_no") or not f.get("carrier_scac"):
        raise ValueError("erp.post_payable needs fields.invoice_no and fields.carrier_scac")
    async with db.tenant_session(uuid.UUID(ctx.tenant_id)) as s:
        pid = (
            await s.execute(
                text("""insert into invoices (tenant_id, document_id, carrier_scac, invoice_no, currency,
                          total,
                          lines, status, run_id, posted_at)
                        values (:t, cast(:d as uuid), :c, :n, :cur, :tot, cast(:l as jsonb), 'posted',
                          cast(:r as uuid), now())
                        on conflict (tenant_id, carrier_scac, invoice_no) do update set status = 'posted',
                          posted_at = coalesce(invoices.posted_at, now())
                        returning id"""),
                {
                    "t": ctx.tenant_id,
                    "d": params.get("document_id"),
                    "c": f["carrier_scac"],
                    "n": f["invoice_no"],
                    "cur": f.get("currency"),
                    "tot": f.get("total"),
                    "l": json.dumps(f.get("lines") or []),
                    "r": ctx.run_id,
                },
            )
        ).scalar_one()
    return {"payable_id": str(pid), "invoice_no": f["invoice_no"], "amount_usd": params.get("amount_usd")}


def dispute_body(
    f: dict[str, Any], issues: list[dict[str, Any]], clauses: list[dict[str, Any]], reason: str
) -> str:
    """The email a carrier gets: what's disputed, why, and the contract text it rests on (docs/04 W2)."""
    by_id = {c["clause_id"]: c for c in clauses}
    lines = [
        f"Dear {f.get('carrier_scac', 'carrier')} billing team,",
        "",
        f"We dispute invoice {f.get('invoice_no')} ({f.get('currency')} {f.get('total')}).",
        "",
    ]
    for i in issues:
        lines.append(f"- {i['message']}")
        clause = by_id.get(i.get("clause_id") or "")
        if clause:
            lines.append(f'  Contract {clause["clause_id"]} ({clause["title"]}): "{clause["text"]}"')
    if reason:
        lines += ["", f"Reviewer note: {reason}"]
    lines += [
        "",
        "Please issue a corrected invoice or credit note referencing the clause above.",
        "",
        "Accounts payable",
    ]
    return "\n".join(lines)


@ACTIONS.register("carrier.dispute_email")
async def carrier_dispute_email(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """Queue the dispute email in the outbox (a mailer, or Debezium in M6, delivers it). The body cites the
    contract clause behind every disputed line."""
    f = params.get("fields") or {}
    issues = [i for i in params.get("issues") or [] if isinstance(i, dict) and i.get("message")]
    body = dispute_body(f, issues, params.get("clauses") or [], str(params.get("reason") or ""))
    payload = {
        "to": f"billing@{str(f.get('carrier_scac', 'carrier')).lower()}.example",
        "subject": f"Dispute: invoice {f.get('invoice_no')}",
        "body": body,
        "clause_ids": sorted({i["clause_id"] for i in issues if i.get("clause_id")}),
        "run_id": ctx.run_id,
    }
    async with db.tenant_session(uuid.UUID(ctx.tenant_id)) as s:
        mid = (
            await s.execute(
                text("""insert into outbox (tenant_id, aggregate, aggregate_id, type, payload)
                        values (:t, 'invoice', :a, 'email.dispute', cast(:p as jsonb)) returning id"""),
                {"t": ctx.tenant_id, "a": str(f.get("invoice_no")), "p": json.dumps(payload)},
            )
        ).scalar_one()
    return {
        "message_id": str(mid),
        "subject": payload["subject"],
        "body": body,
        "clause_ids": payload["clause_ids"],
    }
