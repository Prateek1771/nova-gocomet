"""Idempotent dev seed: tenants + TenantConfig v1 (docs/04 W2 approval limits).

Run: uv run python -m nova_api.seed
"""

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from nova_core.settings import get_settings

TENANTS = {
    "acme": ("Acme Logistics", {"ops_lead": 10_000, "finance": 50_000, "controller": None}),
    "bolt": ("Bolt Freight", {"ops_lead": 5_000, "finance": 25_000, "controller": None}),
}


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
            # owner is subject to RLS too (FORCE), so scope the config insert to the tenant
            await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
            config = {"currency": "USD", "approval_limits": limits}
            await c.execute(
                text("""
                insert into tenant_configs (tenant_id, version, config, published_by)
                values (:t, 1, cast(:c as jsonb), 'seed') on conflict do nothing"""),
                {"t": tid, "c": json.dumps(config)},
            )
            print(f"seeded {slug} ({tid})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
