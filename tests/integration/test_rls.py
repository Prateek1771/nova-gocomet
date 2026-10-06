"""RLS smoke (07 M0): as nova_app, tenant A never sees tenant B rows; no tenant set → no rows."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def _seed(owner_url: str) -> dict[str, uuid.UUID]:
    eng = create_async_engine(owner_url)
    ids = {}
    async with eng.begin() as c:
        for slug in ("acme", "bolt"):
            tid = (
                await c.execute(
                    text("insert into tenants (slug, name) values (:s, :s) returning id"), {"s": slug}
                )
            ).scalar_one()
            await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
            await c.execute(
                text("insert into tenant_configs values (:t, 1, '{}', 'test', now())"), {"t": tid}
            )
            await c.execute(
                text("insert into shipments (tenant_id, container_no, status) values (:t, :n, 'x')"),
                {"t": tid, "n": f"{slug}-1"},
            )
            ids[slug] = tid
    await eng.dispose()
    return ids


async def _visible(app_url: str, tenant: uuid.UUID | None) -> list[str]:
    eng = create_async_engine(app_url)
    async with eng.begin() as c:
        if tenant:
            await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
        rows = (await c.execute(text("select container_no from shipments"))).scalars().all()
    await eng.dispose()
    return list(rows)


def test_rls_isolates_tenants(pg: tuple[str, str]) -> None:
    owner, app = pg
    ids = asyncio.run(_seed(owner))
    assert asyncio.run(_visible(app, ids["acme"])) == ["acme-1"]
    assert asyncio.run(_visible(app, ids["bolt"])) == ["bolt-1"]
    assert asyncio.run(_visible(app, None)) == []


def test_app_cannot_write_other_tenant(pg: tuple[str, str]) -> None:
    owner, app = pg

    async def attempt() -> None:
        eng = create_async_engine(app)
        try:
            async with eng.begin() as c:
                acme, bolt = (await c.execute(text("select id from tenants order by slug"))).scalars().all()
                await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(acme)})
                await c.execute(
                    text("insert into shipments (tenant_id, container_no, status) values (:t, 'x', 'x')"),
                    {"t": bolt},
                )
        finally:
            await eng.dispose()

    with pytest.raises(Exception, match="row-level security"):
        asyncio.run(attempt())
