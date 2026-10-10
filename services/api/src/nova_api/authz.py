"""The one place every route asks "may this caller do this?" (CLAUDE.md rule 10).

Every answer is an OpenFGA check against `infra/openfga/model.fga`. The caller's Keycloak roles and the
object's structure (task → run → workflow → tenant, the task's assignee role, approver limits from the
run's pinned TenantConfig) go in as contextual tuples, read from the tenant-scoped row (ADR-019,
ADR-026). Never branch on JWT roles here. Objects in another tenant don't resolve under RLS → 404.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from sqlalchemy import text

from nova_api import fga
from nova_api.deps import Caller, CallerDep
from nova_api.errors import ApiError
from nova_core import db

TENANT_ROLES = frozenset(
    {"tenant_admin", "process_designer", "ops_exec", "ops_lead", "finance", "controller", "auditor", "viewer"}
)
ASSIGNABLE = frozenset({"tenant_admin", "ops_exec", "ops_lead", "finance", "controller"})  # task#assignee
APPROVERS = frozenset({"ops_lead", "finance", "controller"})  # task#approver (within_limit)
UNLIMITED = 1e18  # approval_limits[role] = null


@dataclass(frozen=True)
class TenantCaller:
    caller: Caller
    tenant_id: uuid.UUID

    @property
    def sub(self) -> str:
        return self.caller.principal.sub


async def tenant_caller(c: CallerDep) -> TenantCaller:
    if c.tenant_id is None:
        raise HTTPException(403, "this route needs a tenant (log in through an organization)")
    return TenantCaller(c, c.tenant_id)


TenantDep = Annotated[TenantCaller, Depends(tenant_caller)]


def role_tuples(c: TenantCaller) -> list[fga.Tuple]:
    return [
        fga.Tuple(f"user:{c.sub}", r, f"tenant:{c.tenant_id}")
        for r in sorted(c.caller.principal.roles & TENANT_ROLES)
    ]


def _number(v: Any) -> float | None:
    """Rendered `with:` values may arrive as numbers or numeric strings."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def task_facts(tenant: uuid.UUID, row: Any) -> tuple[list[fga.Tuple], dict[str, Any] | None]:
    """`row` has id, run_id, assignee_role, payload, limits. A task carrying an `amount` is an approval:
    the assigned role and every role with an equal or higher limit (the approval hierarchy) become
    approvers, each bounded by its own pinned limit. No plain assignee tuple, so nobody bypasses a limit.
    A task assigned to the top of the hierarchy (controller, unlimited) stays with it: dual control."""
    task, t = f"task:{row.id}", f"tenant:{tenant}"
    tuples = [fga.Tuple(f"run:{row.run_id}", "run", task)]
    amount = _number((row.payload or {}).get("amount"))
    if amount is not None:
        limits = {
            r: UNLIMITED if v is None else float(v) for r, v in (row.limits or {}).items() if r in APPROVERS
        }
        floor = limits.get(row.assignee_role, 0.0)  # assigned to a non-approver: any approver may act
        for role, limit in limits.items():
            if limit >= floor:
                cond = {"name": "within_limit", "context": {"limit": limit}}
                tuples.append(fga.Tuple(f"{t}#{role}", "approver", task, cond))
        return tuples, {"amount": amount}
    if row.assignee_role in ASSIGNABLE:
        tuples.append(fga.Tuple(f"{t}#{row.assignee_role}", "assignee", task))
    return tuples, None


TASK_FACTS = """d.key, c.config -> 'approval_limits' as limits
    from human_tasks h join workflow_runs r on r.id = h.run_id
    join workflow_definitions d on d.id = r.definition_id
    join tenant_configs c on c.version = r.config_version"""  # select h.<cols>, + this


def workflow_tuples(tenant: uuid.UUID, key: str, run_id: Any = None) -> list[fga.Tuple]:
    wf = f"workflow:{tenant}/{key}"
    out = [fga.Tuple(f"tenant:{tenant}", "tenant", wf)]
    if run_id is not None:
        out.append(fga.Tuple(wf, "workflow", f"run:{run_id}"))
    return out


async def _facts(c: TenantCaller, obj: str) -> tuple[list[fga.Tuple], dict[str, Any] | None]:
    kind, _, oid = obj.partition(":")
    if kind == "tenant":
        return [], None
    if kind == "workflow":
        return workflow_tuples(c.tenant_id, oid.partition("/")[2]), None
    async with db.tenant_session(c.tenant_id) as s:
        if kind == "run":
            key = (
                await s.execute(
                    text("""select d.key from workflow_runs r join workflow_definitions d
                            on d.id = r.definition_id where r.id = cast(:id as uuid)"""),
                    {"id": oid},
                )
            ).scalar()
            if key is None:
                raise HTTPException(404, "run not found")
            return workflow_tuples(c.tenant_id, key, oid), None
        if kind == "task":
            row = (
                await s.execute(
                    text(
                        f"select h.id, h.run_id, h.assignee_role, h.payload, {TASK_FACTS}"
                        " where h.id = cast(:id as uuid)"
                    ),
                    {"id": oid},
                )
            ).one_or_none()
            if row is None:
                raise HTTPException(404, "task not found")
            tuples, ctx = task_facts(c.tenant_id, row)
            return workflow_tuples(c.tenant_id, row.key, row.run_id) + tuples, ctx
    raise ValueError(f"unknown object type {kind!r}")


async def authorize(c: TenantCaller, relation: str, obj: str) -> None:
    """`relation`/`obj` use the FGA model's names (LLD §7): ("can_publish", "workflow:<tenant>/<key>")."""
    tuples, ctx = await _facts(c, obj)
    try:
        ok = await fga.check(fga.Check(f"user:{c.sub}", relation, obj, role_tuples(c) + tuples, ctx))
    except fga.FGAError as e:
        raise HTTPException(503, "authorization service unavailable") from e  # fail closed
    if not ok:
        if ctx is not None and relation in ("can_complete", "can_claim"):
            raise ApiError(
                403, "above_approval_limit", f"amount {ctx['amount']:g} is above your approval limit"
            )
        raise HTTPException(403, f"not allowed: {relation} on {obj.partition(':')[0]}")


Fact = tuple[str, str, list[fga.Tuple], dict[str, Any] | None]  # relation, object, tuples, context


async def allowed(c: TenantCaller, facts: list[Fact]) -> list[bool]:
    """Batch form for lists (inbox) and /me capabilities; the caller supplies each object's facts."""
    roles = role_tuples(c)
    checks = [fga.Check(f"user:{c.sub}", rel, o, roles + t, ctx) for rel, o, t, ctx in facts]
    try:
        return await fga.batch_check(checks)
    except fga.FGAError as e:
        raise HTTPException(503, "authorization service unavailable") from e
