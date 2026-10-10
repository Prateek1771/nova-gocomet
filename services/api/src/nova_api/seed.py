"""Idempotent dev seed: tenants, TenantConfig v1 (docs/04 W2 approval limits), and every workflow in
definitions/workflows/<tenant>/ published (a new version only when the YAML changed).

Run: uv run python -m nova_api.seed
"""

import asyncio
import json
import os
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from nova_core.settings import get_settings
from nova_dsl import parse_workflow

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
# TenantConfig v1 per tenant: same keys, different values (FR-X.2); approval limits from docs/04 W2
TENANTS = {
    "acme": (
        "Acme Logistics",
        {
            "approval_limits": {"ops_lead": 10_000, "finance": 50_000, "controller": None},
            "auto_approve_below": 1000,
            "bol_min_confidence": 0.85,  # W1: below this, extraction goes to human review
            "llm_budget_usd": 5.0,  # per-tenant LiteLLM virtual key budget (ADR-028)
            # W2 approval matrix (docs/04): auto-pay / L1 / L1+L2 bands, read by invoice_match's rules
            "invoice": {
                "auto_variance_pct": 2,
                "auto_limit_usd": 2000,
                "l2_limit_usd": 10000,
                "l2_variance_pct": 5,
            },
        },
    ),
    "bolt": (
        "Bolt Freight",
        {
            "approval_limits": {"ops_lead": 5_000, "finance": 25_000, "controller": None},
            "auto_approve_below": 500,
            "bol_min_confidence": 0.95,
            "dual_control_above": 20_000,  # demo_approval: controller second sign-off
            "llm_budget_usd": 2.0,
            "invoice": {
                "auto_variance_pct": 1,
                "auto_limit_usd": 1000,
                "l2_limit_usd": 5000,
                "l2_variance_pct": 3,
            },
        },
    ),
}


async def publish_definitions(c: AsyncConnection, tid: object, slug: str) -> None:
    for path in sorted((DEFINITIONS / "workflows" / slug).glob("*.yaml")):
        source = path.read_text(encoding="utf-8")
        wf, issues = parse_workflow(source)
        if wf is None or issues:
            raise SystemExit(f"{path}: {[i.message for i in issues]}")
        key = wf.metadata.key
        latest = (
            await c.execute(
                text("""select version, yaml from workflow_definitions
                        where key = :k and status = 'published' order by version desc limit 1"""),
                {"k": key},
            )
        ).one_or_none()
        if latest and latest.yaml == source:
            continue
        wf.metadata.version = (latest.version if latest else 0) + 1
        await c.execute(
            text("""insert into workflow_definitions
                      (tenant_id, key, version, status, yaml, compiled, published_by, published_at)
                    values (:t, :k, :v, 'published', :y, cast(:c as jsonb), 'seed', now())"""),
            {
                "t": tid,
                "k": key,
                "v": wf.metadata.version,
                "y": source,
                "c": json.dumps(wf.model_dump(mode="json", by_alias=True)),
            },
        )
        print(f"  published {slug}/{key} v{wf.metadata.version}")


async def seed_bookings(c: AsyncConnection, tid: object) -> None:
    """Booking master data the BoL validator checks against (BOOKING_MISMATCH, DATE_ORDER)."""
    cases = json.loads((DEFINITIONS / "seed" / "bol_cases.json").read_text(encoding="utf-8"))["cases"]
    for b in (case["booking"] for case in cases):
        await c.execute(
            text("""insert into bookings
                      (tenant_id, booking_ref, container_count, pod, consignee, booking_date)
                    values (:t, :r, :n, :pod, :c, :d)
                    on conflict (tenant_id, booking_ref) do update set
                      container_count = excluded.container_count, pod = excluded.pod,
                      consignee = excluded.consignee, booking_date = excluded.booking_date"""),
            {
                "t": tid,
                "r": b["booking_ref"],
                "n": b["container_count"],
                "pod": b["pod"],
                "c": b["consignee"],
                "d": date.fromisoformat(b["booking_date"]),
            },
        )


async def seed_w2(c: AsyncConnection, tid: object) -> None:
    """W2 master data (definitions/seed/invoice_cases.json): contracts + clauses, POs, container events,
    FX. Idempotent upserts keyed on the business ids."""
    w2 = json.loads((DEFINITIONS / "seed" / "invoice_cases.json").read_text(encoding="utf-8"))
    for cur, rate in w2["fx"].items():
        await c.execute(
            text("""insert into fx_rates (tenant_id, currency, usd_rate) values (:t, :c, :r)
                    on conflict (tenant_id, currency) do update set usd_rate = excluded.usd_rate"""),
            {"t": tid, "c": cur, "r": rate},
        )
    for k in w2["contracts"]:
        cid = (
            await c.execute(
                text("""insert into rate_contracts (tenant_id, contract_no, carrier_scac, currency,
                          free_time_days, valid_from, valid_to)
                        values (:t, :n, :s, :cur, :ft, :vf, :vt)
                        on conflict (tenant_id, contract_no)
                          do update set free_time_days = excluded.free_time_days
                        returning id"""),
                {
                    "t": tid,
                    "n": k["contract_no"],
                    "s": k["carrier_scac"],
                    "cur": k["currency"],
                    "ft": k["free_time_days"],
                    "vf": date.fromisoformat(k["valid_from"]),
                    "vt": date.fromisoformat(k["valid_to"]),
                },
            )
        ).scalar_one()
        for cl in k["clauses"]:
            await c.execute(
                text("""insert into contract_clauses (tenant_id, contract_id, clause_id, charge_code, title,
                          text, rate, unit)
                        values (:t, :k, :id, :cc, :ti, :tx, :r, :u)
                        on conflict (tenant_id, clause_id) do update set text = excluded.text,
                          rate = excluded.rate, title = excluded.title"""),
                {
                    "t": tid,
                    "k": cid,
                    "id": cl["clause_id"],
                    "cc": cl["charge_code"],
                    "ti": cl["title"],
                    "tx": cl["text"],
                    "r": cl["rate"],
                    "u": cl["unit"],
                },
            )
    for po in w2["purchase_orders"]:
        pid = (
            await c.execute(
                text("""insert into purchase_orders (tenant_id, po_number, carrier_scac, currency, bol_number)
                        values (:t, :n, :s, :cur, :b)
                        on conflict (tenant_id, po_number) do update set currency = excluded.currency
                        returning id"""),
                {
                    "t": tid,
                    "n": po["po_number"],
                    "s": po["carrier_scac"],
                    "cur": po["currency"],
                    "b": po["bol_number"],
                },
            )
        ).scalar_one()
        for ln in po["lines"]:
            await c.execute(
                text("""insert into po_lines (tenant_id, po_id, charge_code, qty, unit_rate)
                        values (:t, :p, :cc, :q, :r)
                        on conflict (po_id, charge_code) do update set qty = excluded.qty,
                          unit_rate = excluded.unit_rate"""),
                {"t": tid, "p": pid, "cc": ln["charge_code"], "q": ln["qty"], "r": ln["unit_rate"]},
            )
    for ev in w2["container_events"]:
        await c.execute(
            text("""insert into container_events (tenant_id, container_no, event_type, location, event_time)
                    values (:t, :c, :e, :l, :at) on conflict do nothing"""),
            {
                "t": tid,
                "c": ev["container_no"],
                "e": ev["event_type"],
                "l": ev["location"],
                "at": datetime.fromisoformat(ev["event_time"]),
            },
        )


async def main() -> None:
    engine = create_async_engine(get_settings().migrations_database_url)
    async with engine.begin() as c:
        for slug, (name, settings) in TENANTS.items():
            tid = (
                await c.execute(
                    text("""
                insert into tenants (slug, name) values (:s, :n)
                on conflict (slug) do update set name = excluded.name returning id"""),
                    {"s": slug, "n": name},
                )
            ).scalar_one()
            # owner is subject to RLS too (FORCE), so scope the rest to the tenant
            await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
            config = {"currency": "USD", **settings}
            # only the seed's own v1 is ever rewritten; published versions stay immutable
            await c.execute(
                text("""
                insert into tenant_configs (tenant_id, version, config, published_by)
                values (:t, 1, cast(:c as jsonb), 'seed')
                on conflict (tenant_id, version) do update set config = excluded.config
                where tenant_configs.published_by = 'seed'"""),
                {"t": tid, "c": json.dumps(config)},
            )
            # config keys a new milestone introduces (e.g. M5's `invoice`) reach tenants whose admins
            # already published later versions: a new version adds only the missing keys, never overwrites
            latest = (
                await c.execute(
                    text("select version, config from tenant_configs order by version desc limit 1")
                )
            ).one()
            missing = {k: v for k, v in config.items() if k not in latest.config}
            if missing:
                await c.execute(
                    text("""insert into tenant_configs (tenant_id, version, config, published_by)
                            values (:t, :v, cast(:c as jsonb), 'seed')"""),
                    {"t": tid, "v": latest.version + 1, "c": json.dumps({**latest.config, **missing})},
                )
                print(f"  config v{latest.version + 1}: added {sorted(missing)}")
            print(f"seeded {slug} ({tid})")
            await seed_bookings(c, tid)
            await seed_w2(c, tid)
            await publish_definitions(c, tid, slug)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
