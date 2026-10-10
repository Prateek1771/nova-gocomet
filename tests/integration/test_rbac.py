"""07 M4: every role × capability against the allow/deny table in LLD §7, on real OpenFGA with the
committed model. Facts are built by the same helpers the API uses (authz.role_tuples / task_facts)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from nova_api import fga
from nova_api.authz import TENANT_ROLES, task_facts, workflow_tuples

T = uuid.uuid4()
OTHER = uuid.uuid4()
ROLES = sorted(TENANT_ROLES) + ["platform_admin"]

# LLD §7 matrix: capability -> roles allowed (tenant scope). platform_admin has no tenant capability.
MATRIX: dict[str, set[str]] = {
    "can_manage_users": {"tenant_admin"},
    "can_edit_config": {"tenant_admin"},
    "can_design": {"tenant_admin", "process_designer"},
    "can_operate": {"ops_exec", "ops_lead", "finance"},
    "can_view": TENANT_ROLES - set(),
    "can_view_analytics": {
        "tenant_admin",
        "process_designer",
        "ops_lead",
        "finance",
        "controller",
        "auditor",
        "viewer",
    },
    "can_read_audit": {"tenant_admin", "auditor"},
    "can_manage_budgets": {"tenant_admin"},
}


def roles_of(sub: str, role: str, tenant: uuid.UUID = T) -> list[fga.Tuple]:
    if role == "platform_admin":
        return [fga.Tuple(f"user:{sub}", "admin", "platform:nova")]
    return [fga.Tuple(f"user:{sub}", role, f"tenant:{tenant}")]


async def can(
    role: str, relation: str, obj: str, facts: list[fga.Tuple] | None = None, ctx: Any = None
) -> bool:
    sub = str(uuid.uuid4())
    return await fga.check(fga.Check(f"user:{sub}", relation, obj, roles_of(sub, role) + (facts or []), ctx))


@pytest.fixture(autouse=True)
def fresh_client() -> None:
    fga._http = fga._store = None


@pytest.mark.parametrize("cap", sorted(MATRIX))
async def test_tenant_capabilities(cap: str) -> None:
    for role in ROLES:
        assert await can(role, cap, f"tenant:{T}") == (role in MATRIX[cap]), (role, cap)


async def test_platform_admin_manages_tenants_only() -> None:
    assert await can("platform_admin", "can_manage_tenants", "platform:nova")
    assert not await can("tenant_admin", "can_manage_tenants", "platform:nova")


async def test_roles_never_cross_tenants() -> None:
    sub = str(uuid.uuid4())
    for cap in MATRIX:
        check = fga.Check(f"user:{sub}", cap, f"tenant:{OTHER}", roles_of(sub, "tenant_admin"))
        assert not await fga.check(check)


async def test_workflow_and_run_inherit_from_their_tenant() -> None:
    run = uuid.uuid4()
    facts = workflow_tuples(T, "demo_approval", run)
    wf = f"workflow:{T}/demo_approval"
    assert await can("process_designer", "can_publish", wf, facts)
    assert not await can("ops_exec", "can_publish", wf, facts)
    assert await can("ops_exec", "can_start", wf, facts)
    assert await can("viewer", "can_view", f"run:{run}", facts)
    assert not await can("ops_exec", "can_cancel", f"run:{run}", facts)
    # the same structure in another tenant gives nothing
    other = workflow_tuples(OTHER, "demo_approval", run)
    assert not await can("viewer", "can_view", f"run:{run}", other)


def _task(role: str | None, amount: Any = None) -> tuple[str, list[fga.Tuple], Any]:
    run, tid = uuid.uuid4(), uuid.uuid4()
    limits = {"ops_lead": 10000, "finance": 50000, "controller": None}
    row = SimpleNamespace(
        id=tid, run_id=run, assignee_role=role, payload={"amount": amount} if amount else {}, limits=limits
    )
    tuples, ctx = task_facts(T, row)
    return f"task:{tid}", workflow_tuples(T, "wf", run) + tuples, ctx


async def test_assigned_task_completes_by_role_only() -> None:
    obj, facts, ctx = _task("ops_exec")
    assert await can("ops_exec", "can_complete", obj, facts, ctx)
    assert await can("ops_exec", "can_claim", obj, facts, ctx)
    for role in ("ops_lead", "finance", "auditor", "viewer", "tenant_admin", "platform_admin"):
        assert not await can(role, "can_complete", obj, facts, ctx), role
    assert await can("auditor", "can_view", obj, facts, ctx)  # members see; only assignees act
    assert not await can("platform_admin", "can_view", obj, facts, ctx)


async def test_attention_task_goes_to_tenant_admin() -> None:
    obj, facts, ctx = _task("tenant_admin")
    assert await can("tenant_admin", "can_complete", obj, facts, ctx)
    assert not await can("ops_exec", "can_complete", obj, facts, ctx)


@pytest.mark.parametrize(
    ("amount", "allowed"),
    [
        (10000, {"ops_lead", "finance", "controller"}),  # = limit is allowed
        (10000.01, {"finance", "controller"}),
        (50000.01, {"controller"}),  # controller: null limit = unlimited
        ("5000", {"ops_lead", "finance", "controller"}),  # rendered as a string
    ],
)
async def test_approval_within_limit(amount: Any, allowed: set[str]) -> None:
    obj, facts, ctx = _task("ops_lead", amount)
    for role in ROLES:
        assert await can(role, "can_complete", obj, facts, ctx) == (role in allowed), (role, amount)


async def test_dual_control_task_stays_with_controller() -> None:
    obj, facts, ctx = _task("controller", 22000)
    assert await can("controller", "can_complete", obj, facts, ctx)
    assert not await can("finance", "can_complete", obj, facts, ctx)  # 22000 is within finance's limit
