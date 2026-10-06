"""Idempotent dev seed: tenants, TenantConfig v1 (docs/04 W2 approval limits), and every workflow in
definitions/workflows/<tenant>/ published (a new version only when the YAML changed).

Run: uv run python -m nova_api.seed
"""

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from nova_core.settings import get_settings
from nova_dsl import parse_workflow

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
TENANTS = {
    "acme": ("Acme Logistics", {"ops_lead": 10_000, "finance": 50_000, "controller": None}),
    "bolt": ("Bolt Freight", {"ops_lead": 5_000, "finance": 25_000, "controller": None}),
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


async def main() -> None:
    engine = create_async_engine(get_settings().migrations_database_url)
    async with engine.begin() as c:
        for slug, (name, limits) in TENANTS.items():
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
            config = {"currency": "USD", "approval_limits": limits, "auto_approve_below": 1000}
            # only the seed's own v1 is ever rewritten; published versions stay immutable
            await c.execute(
                text("""
                insert into tenant_configs (tenant_id, version, config, published_by)
                values (:t, 1, cast(:c as jsonb), 'seed')
                on conflict (tenant_id, version) do update set config = excluded.config
                where tenant_configs.published_by = 'seed'"""),
                {"t": tid, "c": json.dumps(config)},
            )
            print(f"seeded {slug} ({tid})")
            await publish_definitions(c, tid, slug)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
