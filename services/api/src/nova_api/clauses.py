"""Keeps each tenant's Weaviate shard in step with its contract clauses in Postgres (ADR-030). Runs in
the API's startup task (retrying while Weaviate / the embedder come up) and as `make reindex`. Ids are
deterministic, so a re-run is an upsert.

Run: uv run python -m nova_api.clauses
"""

import asyncio

import httpx
import structlog
from sqlalchemy import text

from nova_core import db, vectors

log = structlog.get_logger()


async def index_tenant(tenant_id: object) -> int:
    async with db.tenant_session(tenant_id) as s:  # type: ignore[arg-type]
        rows = await s.execute(
            text("""select c.clause_id, k.contract_no, k.carrier_scac, c.charge_code, c.title, c.text,
                      c.rate, c.unit
                    from contract_clauses c join rate_contracts k on k.id = c.contract_id""")
        )
        clauses = [dict(r._mapping) for r in rows]
    return await vectors.index_clauses(str(tenant_id), clauses)


async def index_all(attempts: int = 30) -> None:
    async with db.session() as s:
        tenants = (await s.execute(text("select id, slug from tenants"))).all()
    for t in tenants:
        for n in range(attempts):
            try:
                count = await index_tenant(t.id)
                log.info("clauses_indexed", tenant=t.slug, count=count)
                break
            except (httpx.HTTPError, RuntimeError) as e:
                if n == attempts - 1:
                    log.warning("clauses_index_failed", tenant=t.slug, error=str(e))
                await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(index_all(attempts=1))
