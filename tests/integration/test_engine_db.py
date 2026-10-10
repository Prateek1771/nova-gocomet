"""Engine + API against a real Postgres (RLS, migrations, seed) and Temporal's test server.
Only the Keycloak token is faked (dependency override); everything else is the real code path."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nova_core import db
from nova_core import temporal as t
from nova_core.auth import Principal
from nova_core.settings import get_settings

ACME = "acme"
ROLES = {
    "ctrl": "controller",
    "audit": "auditor",
    "ops": "ops_exec",
    "lead": "ops_lead",
    "fin": "finance",
    "designer": "process_designer",
    "admin": "tenant_admin",
}
GATED = """
metadata: {key: gated}
nodes:
  - {id: gate, type: human_task, title: Gate, assignee: {role: ops_exec}, app: generic_review}
  - id: route
    type: rule
    cases: [{when: "double(input.amount) < double(tenant.auto_approve_below)", goto: ok}]
    default: held
  - {id: ok, type: end}
  - {id: held, type: end, status: rejected}
edges:
  - {from: gate, to: route, on: approved}
  - {from: gate, to: held, on: rejected}
"""


@pytest.fixture(scope="module")
async def stack(pg: tuple[str, str]) -> AsyncIterator[dict[str, Any]]:
    owner, app_url = pg
    os.environ.update(DATABASE_URL=app_url, MIGRATIONS_DATABASE_URL=owner)
    for cached in (get_settings, db.engine, db._sessions):
        cached.cache_clear()
    from nova_api import fga

    fga._http = fga._store = None  # bound to the previous module's event loop
    from nova_api import seed

    await seed.main()  # tenants, config v1, demo_approval v1
    from nova_api.authz import TenantCaller, tenant_caller
    from nova_api.deps import Caller
    from nova_api.main import app
    from nova_engine import activities
    from nova_engine.interpreter import NovaWorkflow

    async with db.session() as s:
        tenants = {r.slug: r.id for r in await s.execute(text("select slug, id from tenants"))}
    acme = tenants[ACME]
    users: dict[str, str] = {}

    def as_user(name: str) -> httpx.AsyncClient:
        users.setdefault(name, str(uuid.uuid4()))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", headers={"x-user": name}
        )

    async def override(request: Request) -> TenantCaller:  # x-user "<name>[@bolt]" picks the principal
        name = request.headers["x-user"]
        role, _, slug = name.partition("@")
        slug = slug or ACME
        p = Principal(
            sub=users[name],
            email=None,
            name=name,
            org_id=None,
            org_alias=slug,
            roles=frozenset({ROLES[role]}),
        )
        return TenantCaller(Caller(p, tenants[slug], slug), tenants[slug])

    app.dependency_overrides[tenant_caller] = override
    async with await WorkflowEnvironment.start_time_skipping() as env:
        t._client = env.client
        async with Worker(
            env.client, task_queue=t.ENGINE_QUEUE, workflows=[NovaWorkflow], activities=activities.ALL
        ):
            yield {"as": as_user, "env": env, "tenant": acme}
    t._client = None
    app.dependency_overrides.clear()


async def until(fetch: Any, ok: Any, tries: int = 100) -> Any:
    for _ in range(tries):
        value = await fetch()
        if ok(value):
            return value
        await asyncio.sleep(0.1)
    raise AssertionError(f"condition never held; last: {value}")


async def run_of(c: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    r = await c.get(f"/runs/{run_id}")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_run_lifecycle_through_the_api(stack: dict[str, Any]) -> None:
    ops, lead = stack["as"]("ops"), stack["as"]("lead")
    r = await ops.post("/runs", json={"workflow_key": "demo_approval", "input": {"amount": 5000}})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    run = await until(lambda: run_of(ops, run_id), lambda b: b["status"] == "waiting_human" and b["tasks"])
    task = run["tasks"][0]
    assert task["assignee_role"] == "ops_lead"

    inbox = (await lead.get("/tasks")).json()
    assert [x["id"] for x in inbox] == [task["id"]]
    assert (
        await lead.post(f"/tasks/{task['id']}/complete", json={"decision": "approved"})
    ).status_code == 409
    assert (await lead.post(f"/tasks/{task['id']}/claim")).json()["status"] == "claimed"
    assert [x["id"] for x in (await lead.get("/tasks?mine=true")).json()] == [task["id"]]
    assert (await ops.post(f"/tasks/{task['id']}/claim")).status_code == 403  # ops_exec: not an approver
    done = await lead.post(
        f"/tasks/{task['id']}/complete", json={"decision": "approved", "payload": {"n": 1}}
    )
    assert done.json()["status"] == "done" and done.json()["decision"] == "approved"

    run = await until(lambda: run_of(ops, run_id), lambda b: b["status"] == "completed")
    assert [s["node_id"] for s in run["steps"]] == ["route", "review", "notify", "done"]
    assert all(s["status"] == "completed" for s in run["steps"]) and run["ended_at"]
    async with db.tenant_session(stack["tenant"]) as s:
        acts = (
            await s.execute(text("select status from action_executions where run_id = :r"), {"r": run_id})
        ).all()
        audit = (
            (
                await s.execute(
                    text("select action from audit_log where subject like :p"), {"p": f"%{task['id']}%"}
                )
            )
            .scalars()
            .all()
        )
    assert [a.status for a in acts] == ["succeeded"]
    assert audit == ["task.open", "task.claimed", "task.done"]


async def test_approval_limit_is_enforced(stack: dict[str, Any]) -> None:
    """07 M4 exit: L1 (ops_lead, limit 10000 in Acme's TenantConfig) can't approve above it via the API;
    finance (50000) can. The limit comes from the run's pinned config, checked by OpenFGA within_limit."""
    ops, lead, fin = stack["as"]("ops"), stack["as"]("lead"), stack["as"]("fin")
    r = await ops.post("/runs", json={"workflow_key": "demo_approval", "input": {"amount": 20000}})
    run_id = r.json()["id"]
    run = await until(lambda: run_of(ops, run_id), lambda b: b["tasks"])
    tid = run["tasks"][0]["id"]
    assert tid not in [x["id"] for x in (await lead.get("/tasks")).json()]
    denied = await lead.post(f"/tasks/{tid}/claim")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "above_approval_limit"
    denied = await lead.post(f"/tasks/{tid}/complete", json={"decision": "approved"})
    assert denied.status_code == 403
    assert tid in [x["id"] for x in (await fin.get("/tasks")).json()]
    assert (await fin.post(f"/tasks/{tid}/claim")).json()["status"] == "claimed"
    done = await fin.post(f"/tasks/{tid}/complete", json={"decision": "approved"})
    assert done.json()["status"] == "done"


async def test_cancel_through_the_api(stack: dict[str, Any]) -> None:
    ops, designer = stack["as"]("ops"), stack["as"]("designer")
    run_id = (
        await ops.post("/runs", json={"workflow_key": "demo_approval", "input": {"amount": 9000}})
    ).json()["id"]
    await until(lambda: run_of(ops, run_id), lambda b: b["status"] == "waiting_human")
    assert (await ops.post(f"/runs/{run_id}/cancel")).status_code == 403  # can_cancel = can_publish
    assert (await designer.post(f"/runs/{run_id}/cancel")).status_code == 202
    await until(lambda: run_of(ops, run_id), lambda b: b["status"] == "cancelled")
    assert (await designer.post(f"/runs/{run_id}/cancel")).status_code == 409


async def test_idempotent_action(stack: dict[str, Any]) -> None:
    from nova_core.runs import create_run
    from nova_engine.actions import ACTIONS
    from nova_engine.activities import run_action
    from nova_engine.contracts import ActionRequest

    calls: list[dict[str, Any]] = []
    if "test.count" not in ACTIONS:

        @ACTIONS.register("test.count")
        async def count(params: dict[str, Any], ctx: ActionRequest) -> dict[str, Any]:
            calls.append(params)
            return {"n": len(calls)}

    tenant = stack["tenant"]
    async with db.tenant_session(tenant) as s:
        run = await create_run(s, tenant, "demo_approval", {})
    req = ActionRequest(
        str(tenant), str(run.run_id), "notify", f"{tenant}:{run.run_id}:notify:1", "test.count", {}
    )
    first, again = await run_action(req), await run_action(req)  # a replayed / retried activity
    assert first == again == {"n": 1} and len(calls) == 1
    async with db.tenant_session(tenant) as s:
        n = (
            await s.execute(
                text("select count(*) from action_executions where run_id = :r"), {"r": run.run_id}
            )
        ).scalar()
    assert n == 1


async def test_config_is_pinned_at_run_start(stack: dict[str, Any]) -> None:
    designer, ops = stack["as"]("designer"), stack["as"]("ops")
    draft = await designer.put("/workflows/gated/draft", json={"yaml": GATED})
    assert draft.json()["valid"], draft.json()
    assert (await designer.post("/workflows/gated/publish")).json() == {"key": "gated", "version": 1}

    # 1500 is over v1's auto_approve_below (1000) but under the 2000 published mid-run
    run_id = (await ops.post("/runs", json={"workflow_key": "gated", "input": {"amount": 1500}})).json()["id"]
    run = await until(lambda: run_of(ops, run_id), lambda b: b["tasks"])
    admin = stack["as"]("admin")
    cfg = (await admin.get("/tenant-config")).json()["config"]
    assert (await designer.put("/tenant-config", json=cfg)).status_code == 403  # can_edit_config
    v2 = await admin.put("/tenant-config", json={**cfg, "auto_approve_below": 2000})
    assert v2.json()["version"] == 2

    tid = run["tasks"][0]["id"]
    await ops.post(f"/tasks/{tid}/claim")
    await ops.post(f"/tasks/{tid}/complete", json={"decision": "approved"})
    run = await until(lambda: run_of(ops, run_id), lambda b: b["ended_at"])
    assert run["config_version"] == 1
    assert run["status"] == "rejected"  # routed on the pinned v1 threshold, not v2

    new = (await ops.post("/runs", json={"workflow_key": "gated", "input": {"amount": 1500}})).json()
    assert new["config_version"] == 2


async def test_publish_rejects_invalid_and_versions_are_immutable(stack: dict[str, Any]) -> None:
    d = stack["as"]("designer")
    bad = "metadata: {key: gated}\nnodes:\n  - {id: a, type: action, action: noop}\n"
    assert (await d.put("/workflows/gated/draft", json={"yaml": bad})).json()["valid"] is False
    r = await d.post("/workflows/gated/publish")
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "next_edge"
    v1 = (await d.get("/workflows/gated/versions/1")).json()
    assert v1["yaml"] == GATED and v1["status"] == "published"
    assert (await d.post("/workflows/gated/validate", json={"yaml": GATED})).json()["valid"]


async def test_bolt_cannot_reach_acme(stack: dict[str, Any]) -> None:
    """07 M4 exit: a Bolt user can't see Acme runs (or tasks, or their facts); RLS makes them 404."""
    ops, bolt = stack["as"]("ops"), stack["as"]("lead@bolt")
    run_id = (
        await ops.post("/runs", json={"workflow_key": "demo_approval", "input": {"amount": 7000}})
    ).json()["id"]
    run = await until(lambda: run_of(ops, run_id), lambda b: b["tasks"])
    tid = run["tasks"][0]["id"]
    assert (await bolt.get(f"/runs/{run_id}")).status_code == 404
    assert (await bolt.get(f"/tasks/{tid}")).status_code == 404
    assert (await bolt.post(f"/tasks/{tid}/claim")).status_code == 404
    assert (await bolt.post(f"/runs/{run_id}/cancel")).status_code == 404
    assert run_id not in [r["id"] for r in (await bolt.get("/runs")).json()]
    assert tid not in [x["id"] for x in (await bolt.get("/tasks")).json()]


async def test_bolt_runs_its_own_process(stack: dict[str, Any]) -> None:
    """FR-X.2: Bolt's demo_approval adds a controller sign-off at/above its dual_control_above (20000),
    and Bolt's lower limits apply (finance 25000)."""
    ops, fin, ctrl = stack["as"]("ops@bolt"), stack["as"]("fin@bolt"), stack["as"]("ctrl@bolt")
    run_id = (
        await ops.post("/runs", json={"workflow_key": "demo_approval", "input": {"amount": 22000}})
    ).json()["id"]
    run = await until(lambda: run_of(ops, run_id), lambda b: b["tasks"])
    first = run["tasks"][0]["id"]
    await fin.post(f"/tasks/{first}/claim")
    assert (await fin.post(f"/tasks/{first}/complete", json={"decision": "approved"})).status_code == 200
    run = await until(lambda: run_of(ops, run_id), lambda b: b["tasks"] and b["tasks"][0]["id"] != first)
    second = run["tasks"][0]
    assert second["assignee_role"] == "controller"
    assert (await fin.post(f"/tasks/{second['id']}/claim")).status_code == 403
    await ctrl.post(f"/tasks/{second['id']}/claim")
    await ctrl.post(f"/tasks/{second['id']}/complete", json={"decision": "approved"})
    run = await until(lambda: run_of(ops, run_id), lambda b: b["status"] == "completed")
    assert [s["node_id"] for s in run["steps"]][-3:] == ["second", "notify", "done"]


async def test_audit_chain_verifies_and_detects_tampering(stack: dict[str, Any], pg: tuple[str, str]) -> None:
    """FR-X.1: every row is chained and stamped with its versions; an edit (even by the table owner)
    is caught at its seq. Only auditor / tenant_admin may read it."""
    from sqlalchemy.ext.asyncio import create_async_engine

    ops, auditor = stack["as"]("ops"), stack["as"]("audit")
    assert (await ops.get("/audit")).status_code == 403
    ok = (await auditor.get("/audit/verify")).json()
    assert ok["valid"] and ok["entries"] > 0, ok
    rows = (await auditor.get("/audit?limit=500")).json()
    task_rows = [r for r in rows if r["subject"].startswith("task:")]
    assert task_rows and all(r["definition_version"] and r["config_version"] for r in task_rows)
    published = [r for r in rows if r["action"] == "workflow.published"]
    assert published and published[0]["definition_version"]

    victim = rows[len(rows) // 2]
    owner = create_async_engine(pg[0])
    async with owner.begin() as c:  # owner bypasses the app role's no-update grant, not the chain
        await c.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(stack["tenant"])})
        await c.execute(
            text("update audit_log set actor_id = 'someone-else' where seq = :s"), {"s": victim["seq"]}
        )
    await owner.dispose()
    bad = (await auditor.get("/audit/verify")).json()
    assert not bad["valid"] and bad["broken"][0]["seq"] == victim["seq"], bad
