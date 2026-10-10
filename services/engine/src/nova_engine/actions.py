"""Registered side effects (`action` nodes). Business integrations (tms.*, erp.*) register here too.
Each action gets its params plus the request (tenant, run, node, idempotency key), never a raw session:
anything it writes goes through a tenant-scoped transaction."""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text

from nova_core import clickhouse, db
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


@ACTIONS.register("exceptions.detect")
async def exceptions_detect(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """W3 detection (docs/04, docs/09 C): each rule is a governed metric + comparison from TenantConfig
    `exceptions.rules`. Every breaching row becomes one exception per shipment + type (`exceptions_once`, so
    a breach that lasts several cycles is raised once); the metric's SQL and row are its evidence. The
    trigger router starts triage from the insert (CDC). Deterministic: no model."""
    rules = [r for r in params.get("rules") or [] if isinstance(r, dict)]
    severity = params.get("severity") or {}
    tenant = uuid.UUID(ctx.tenant_id)
    found, new = 0, []
    for r in rules:
        res = await clickhouse.query_metric(
            tenant,
            r["metric"],
            op=r.get("op", ">"),
            threshold=float(r["threshold"]),
            limit=clickhouse.MAX_ROWS,
        )
        found += len(res["rows"])
        async with db.tenant_session(tenant) as s:
            for row in res["rows"]:
                facts = {
                    "metric": r["metric"],
                    "rule": r,
                    "sql": res["sql"],
                    "params": res["params"],
                    "row": row,
                }
                eid = (
                    await s.execute(
                        # a breach for a shipment we don't track (no shipments row) is skipped, not an error
                        text("""insert into exceptions (tenant_id, shipment_id, type, severity, facts)
                                select :t, id, :ty, :sev, cast(:f as jsonb) from shipments where id = :s
                                on conflict (tenant_id, shipment_id, type) do nothing returning id"""),
                        {
                            "t": ctx.tenant_id,
                            "s": str(row["shipment_id"]),
                            "ty": r["type"],
                            "sev": severity.get(r["type"], "medium"),
                            "f": json.dumps(facts, default=str),
                        },
                    )
                ).scalar_one_or_none()
                if eid:
                    new.append(
                        {"exception_id": str(eid), "type": r["type"], "shipment_id": str(row["shipment_id"])}
                    )
    return {"rules": len(rules), "breaches": found, "new": new}


@ACTIONS.register("exceptions.close")
async def exceptions_close(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """Resolve an exception with `note: reason` (auto-close, dismiss, or after the customer was told)."""
    eid = params.get("exception_id")
    if not eid:
        raise ValueError("exceptions.close needs exception_id")
    async with db.tenant_session(uuid.UUID(ctx.tenant_id)) as s:
        await s.execute(
            text("""update exceptions set status = 'resolved', resolved_at = coalesce(resolved_at, now()),
                      note = :n, run_id = coalesce(run_id, cast(:r as uuid))
                    where id = cast(:e as uuid)"""),
            {
                "e": str(eid),
                "n": ": ".join(str(x) for x in (params.get("note"), params.get("reason")) if x),
                "r": ctx.run_id,
            },
        )
    return {"exception_id": str(eid), "status": "resolved"}


SUBJECT = {"ETA_SLIP": "new arrival time", "DWELL": "delay at transhipment", "MISSED_TS": "missed connection",
           "ROLLOVER": "booking moved to another vessel"}  # fmt: skip


def delay_body(
    exc: dict[str, Any], sh: dict[str, Any], recs: list[dict[str, Any]], reason: str, message: str = ""
) -> str:
    """The customer email (docs/04 W3): what happened, then the message the ops lead approved (the
    recommender's draft, or their own on override), and the SOPs it follows. Without a message a neutral
    line stands in: internal ops steps never go to the customer."""
    row = (exc.get("facts") or {}).get("row") or {}
    what = {
        "ETA_SLIP": f"the carrier now expects arrival {row.get('slip_hours', '?')} h later than planned",
        "DWELL": f"the container has waited {row.get('dwell_hours', '?')} h at {row.get('location')}",
        "MISSED_TS": f"the container missed its connecting vessel at {row.get('location')}",
        "ROLLOVER": f"the booking was rolled from {row.get('booked_first')} to {row.get('booked_now')}",
    }.get(exc["type"], exc["type"])
    lines = [
        "Dear customer,",
        "",
        f"Shipment {sh.get('bol_number')} (container {sh.get('container_no')}, "
        f"{sh.get('pol')} -> {sh.get('pod')}): {what}.",
        "",
    ]
    sops = sorted({r["sop_ref"] for r in recs if r.get("sop_ref")})
    lines += [
        message or "Our operations team is working with the carrier on the next steps.",
        "",
    ] + ([f"Handled under our procedure {', '.join(sops)}."] if sops else [])
    if reason and not message:
        lines += ["", f"Note from our operations team: {reason}"]
    lines += ["", "We will update you as soon as the carrier confirms the new schedule.", "", "Operations"]
    return "\n".join(lines)


@ACTIONS.register("customer.notify")
async def customer_notify(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
    """Queue the customer delay email in the outbox (Debezium -> nova.outbox.shipment -> the notifier's mock
    sink). The body names the SOP each step follows."""
    eid = params.get("exception_id")
    if not eid:
        raise ValueError("customer.notify needs exception_id")
    recs = [r for r in params.get("recommendations") or [] if isinstance(r, dict)]
    # override: the ops lead's own words replace the draft; the SOPs stay cited
    message = (
        str(params.get("override_action") or "").strip() or str(params.get("customer_message") or "").strip()
    )
    async with db.tenant_session(uuid.UUID(ctx.tenant_id)) as s:
        exc = (
            await s.execute(
                text("""select e.type, e.facts, s.id as shipment_id, s.bol_number, s.container_no,
                          s.pol, s.pod, s.fields from exceptions e join shipments s on s.id = e.shipment_id
                        where e.id = cast(:e as uuid)"""),
                {"e": str(eid)},
            )
        ).one()
        sh = dict(exc._mapping)
        body = delay_body(
            {"type": exc.type, "facts": exc.facts}, sh, recs, str(params.get("reason") or ""), message
        )
        sop_refs = sorted({r["sop_ref"] for r in recs if r.get("sop_ref")})
        payload = {
            "to": (exc.fields or {}).get("consignee_email") or "customer@example.com",
            "subject": f"Update on shipment {exc.bol_number}: {SUBJECT.get(exc.type, exc.type.lower())}",
            "body": body,
            "run_id": ctx.run_id,
            "refs": {"exception_id": str(eid), "shipment_id": str(exc.shipment_id), "sop_refs": sop_refs},
        }
        mid = (
            await s.execute(
                text("""insert into outbox (tenant_id, aggregate, aggregate_id, type, payload)
                        values (:t, 'shipment', :a, 'email.customer_delay', cast(:p as jsonb))
                        returning id"""),
                {"t": ctx.tenant_id, "a": str(exc.shipment_id), "p": json.dumps(payload)},
            )
        ).scalar_one()
    return {"message_id": str(mid), "subject": payload["subject"], "body": body, "sop_refs": sop_refs}
