"""Audit log (FR-X.1): read it, and prove it hasn't been edited. Rows are hash-chained per tenant by a DB
trigger (migration 0004); `/audit/verify` recomputes the chain in Postgres."""

import json
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from nova_api.authz import TenantCaller, TenantDep, authorize
from nova_core import db

router = APIRouter(prefix="/audit", tags=["audit"])


async def record(
    s: Any,
    c: TenantCaller,
    action: str,
    subject: str,
    evidence: dict[str, Any] | None = None,
    definition_version: int | None = None,
    config_version: int | None = None,
) -> None:
    """A user action, written in the caller's transaction so it commits (or not) with the change."""
    await s.execute(
        text("""insert into audit_log (tenant_id, actor_type, actor_id, action, subject, evidence,
                  definition_version, config_version)
                values (:t, 'user', :a, :ac, :s, cast(:e as jsonb), :dv, :cv)"""),
        {
            "t": c.tenant_id,
            "a": c.sub,
            "ac": action,
            "s": subject,
            "e": None if evidence is None else json.dumps(evidence),
            "dv": definition_version,
            "cv": config_version,
        },
    )


class AuditEntry(BaseModel):
    seq: int
    at: str
    actor_type: str
    actor_id: str
    action: str
    subject: str
    evidence: Any
    definition_version: int | None
    config_version: int | None
    hash: str


class Verification(BaseModel):
    valid: bool
    entries: int
    broken: list[dict[str, Any]]


@router.get("")
async def list_audit(
    c: TenantDep,
    before: int | None = Query(None, description="seq to page back from"),
    limit: int = Query(100, le=500),
    subject: str | None = Query(None, description="prefix, e.g. task:<id> or run:<id>"),
) -> list[AuditEntry]:
    await authorize(c, "can_read_audit", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text("""select seq, at, actor_type, actor_id, action, subject, evidence, definition_version,
                      config_version, hash from audit_log
                    where (cast(:b as bigint) is null or seq < :b)
                      and (cast(:p as text) is null or subject like :p || '%')
                    order by seq desc limit :n"""),
            {"b": before, "p": subject, "n": limit},
        )
        return [AuditEntry(**{**r._asdict(), "at": r.at.isoformat()}) for r in rows]


@router.get("/verify")
async def verify(c: TenantDep) -> Verification:
    await authorize(c, "can_read_audit", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        broken = [r._asdict() for r in await s.execute(text("select * from audit_verify() limit 20"))]
        n = (await s.execute(text("select count(*) from audit_log"))).scalar_one()
    return Verification(valid=not broken, entries=n, broken=broken)
