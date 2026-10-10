"""Audit chain verification job (07 M4): recompute every tenant's hash chain; exit 1 on the first break.
Schedule it (cron / CI nightly); `GET /audit/verify` is the same check for one tenant on demand.

Run: uv run python scripts/verify_audit.py
"""

import asyncio
import sys

from sqlalchemy import text

from nova_core import db


async def main() -> int:
    async with db.session() as s:
        tenants = (await s.execute(text("select id, slug from tenants order by slug"))).all()
    failed = 0
    for t in tenants:
        async with db.tenant_session(t.id) as s:
            n = (await s.execute(text("select count(*) from audit_log"))).scalar_one()
            broken = (await s.execute(text("select seq, id, reason from audit_verify() limit 5"))).all()
        status = "ok" if not broken else "BROKEN " + ", ".join(f"seq {b.seq}: {b.reason}" for b in broken)
        print(f"{t.slug:<12} {n:>7} entries  {status}")
        failed += bool(broken)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
