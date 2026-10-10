"""Keeps each tenant's Weaviate shard in step with its contract clauses in Postgres (ADR-030) and the SOP
corpus in definitions/sops (W3, ADR-036: one chunk per `##` section). Runs in the API's startup task
(retrying while Weaviate / the embedder come up) and as `make reindex`. Ids are deterministic, so a re-run
is an upsert.

Run: uv run python -m nova_api.clauses
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml
from sqlalchemy import text

from nova_core import db, vectors

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))

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


def sop_chunks(sops_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every SOP section as one chunk: {chunk_id: "<SOP id>#<n>", sop_id, exception_type, carrier, title,
    section, text}. ponytail: the same corpus for every tenant; a tenant-specific SOP is a file with a
    `tenants:` list when someone needs one."""
    chunks = []
    for path in sorted((sops_dir or DEFINITIONS / "sops").glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---", 2)
        meta = yaml.safe_load(front)
        for n, part in enumerate(re.split(r"^## ", body, flags=re.M)[1:], 1):
            section, _, content = part.partition("\n")
            chunks.append(
                {
                    "chunk_id": f"{meta['id']}#{n}",
                    "sop_id": meta["id"],
                    "exception_type": meta["exception_type"],
                    "carrier": meta.get("carrier") or "",
                    "title": meta["title"],
                    "section": section.strip(),
                    "text": content.strip(),
                }
            )
    return chunks


async def index_sops(tenant_id: object) -> int:
    return await vectors.index(str(tenant_id), sop_chunks(), vectors.SOPS)


async def index_all(attempts: int = 30) -> None:
    async with db.session() as s:
        tenants = (await s.execute(text("select id, slug from tenants"))).all()
    for t in tenants:
        for n in range(attempts):
            try:
                count = await index_tenant(t.id)
                sops = await index_sops(t.id)
                log.info("clauses_indexed", tenant=t.slug, count=count, sop_chunks=sops)
                break
            except (httpx.HTTPError, RuntimeError) as e:
                if n == attempts - 1:
                    log.warning("clauses_index_failed", tenant=t.slug, error=str(e))
                await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(index_all(attempts=1))
