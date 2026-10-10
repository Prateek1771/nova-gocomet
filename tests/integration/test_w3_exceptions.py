"""W3 end to end (07 M6) on real Postgres + ClickHouse + Weaviate + OpenFGA + the Temporal test server, with
the real actions, agents, router, notifier and API. ClickHouse runs the real DDL (infra/clickhouse) and the
dbt models as views. The Kafka hops are played in-process: the simulator's events are inserted as the Kafka
engine would, each exceptions row is handed to the router as Debezium's CDC record, and each outbox row to
the notifier as the EventRouter's message. Only the model and the embedder are faked."""

import asyncio
import hashlib
import json
import math
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from testcontainers.core.container import DockerContainer

from nova_core import clickhouse, db, vectors
from nova_core import temporal as t
from nova_core.auth import Principal
from nova_core.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "definitions/seed/shipments.json").read_text(encoding="utf-8"))
EXPECTED = {(i["shipment_id"], i["expect"]) for i in SEED["incidents"]}
SOP_IDS = {
    m[1]
    for p in (ROOT / "definitions/sops").glob("*.md")
    if (m := re.search(r"^id: (\S+)", p.read_text("utf-8"), re.M))
}


def fake_model(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    if system.startswith("You grade shipment exceptions"):
        answer: Any = {"severity": "high", "summary": "Container is late against plan."}
    elif system.startswith("You recommend next actions"):
        answer = {"recommendations": [{"n": 1, "action": "Follow the SOP step.", "why": "fits"}],
                  "customer_message": "Your shipment is delayed; we are re-planning."}  # fmt: skip
    else:  # decide: every scripted incident is actionable and customer-impacting
        qs = json.loads(user)
        answer = {"answers": {q["id"]: True for q in qs}, "why": {q["id"]: "over threshold" for q in qs}}
    return httpx.Response(
        200, json={"model": "fake", "choices": [{"message": {"content": json.dumps(answer)}}], "usage": {}}
    )


async def fake_embed(texts: list[str], kind: str, tenant_id: str | None = None) -> list[list[float]]:
    out = []
    for txt in texts:
        v = [0.0] * 64
        for w in txt.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0  # noqa: S324 (not security)
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _wait(url: str, path: str) -> None:
    for _ in range(200):
        try:
            if httpx.get(f"{url}{path}", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url} never became ready")


@pytest.fixture(scope="module")
def weaviate() -> Iterator[str]:
    c = (
        DockerContainer("semitechnologies/weaviate:1.32.5")
        .with_env("AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED", "true")
        .with_env("DEFAULT_VECTORIZER_MODULE", "none")
        .with_env("PERSISTENCE_DATA_PATH", "/var/lib/weaviate")
        .with_env("CLUSTER_HOSTNAME", "node1")
        .with_exposed_ports(8080)
    )
    with c:
        url = f"http://{c.get_container_host_ip()}:{c.get_exposed_port(8080)}"
        _wait(url, "/v1/.well-known/ready")
        yield url


def ch_admin(url: str, sql: str, **params: str) -> str:
    r = httpx.post(
        url, params=params, content=sql, headers={"X-ClickHouse-User": "nova", "X-ClickHouse-Key": "nova"}
    )
    assert r.status_code == 200, r.text
    return r.text


@pytest.fixture(scope="module")
def ch() -> Iterator[str]:
    """ClickHouse with the real DDL, minus the Kafka engine queues and their MVs (no broker here), and the
    dbt models created as the views dbt would make."""
    c = (
        DockerContainer("clickhouse/clickhouse-server:25.8")
        .with_env("CLICKHOUSE_USER", "nova")
        .with_env("CLICKHOUSE_PASSWORD", "nova")
        .with_env("CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT", "1")
        .with_exposed_ports(8123)
    )
    with c:
        url = f"http://{c.get_container_host_ip()}:{c.get_exposed_port(8123)}"
        _wait(url, "/ping")
        ddl = re.sub(r"--[^\n]*", "", (ROOT / "infra/clickhouse/01-nova.sql").read_text("utf-8"))
        for stmt in (s.strip() for s in ddl.split(";")):
            if stmt and "ENGINE = Kafka" not in stmt and "MATERIALIZED VIEW" not in stmt:
                ch_admin(url, stmt)
        for model in sorted((ROOT / "infra/dbt/models").glob("*.sql")):
            sql = re.sub(r"\{\{\s*source\('nova',\s*'(\w+)'\)\s*\}\}", r"nova.\1", model.read_text("utf-8"))
            ch_admin(url, f"CREATE OR REPLACE VIEW nova_metrics.{model.stem} AS {sql}")
        yield url


ROLE_OF = {"ops": "ops_exec", "lead": "ops_lead", "admin": "tenant_admin"}


@pytest.fixture(scope="module")
async def w3(pg: tuple[str, str], weaviate: str, ch: str) -> AsyncIterator[dict[str, Any]]:
    owner, app_url = pg
    os.environ.update(
        DATABASE_URL=app_url, MIGRATIONS_DATABASE_URL=owner, WEAVIATE_URL=weaviate, CLICKHOUSE_URL=ch
    )
    for cached in (get_settings, db.engine, db._sessions):
        cached.cache_clear()
    from nova_api import fga

    fga._http = fga._store = None
    vectors.use(None)
    clickhouse.use(None)
    from nova_agents import activities as agent_acts
    from nova_agents import llm
    from nova_api import clauses, seed
    from nova_api.authz import TenantCaller, tenant_caller
    from nova_api.deps import Caller
    from nova_api.main import app
    from nova_engine import activities
    from nova_engine.interpreter import NovaWorkflow
    from nova_engine.scheduled import ScheduledRun
    from nova_ingest import simulator

    await seed.main()
    real_embed, vectors.embed = vectors.embed, fake_embed  # type: ignore[assignment]
    await clauses.index_all(attempts=1)  # contract clauses + the SOP corpus, per tenant shard
    llm.use(llm.LLM(transport=httpx.MockTransport(fake_model)))
    async with db.session() as s:
        tenants = {r.slug: r.id for r in await s.execute(text("select slug, id from tenants"))}
    # what the Kafka engine would insert: the scenario up to sim day 11 (all 6 incidents visible)
    rows = [e for d, e in simulator.events(SEED, {k: str(v) for k, v in tenants.items()}) if d <= 11]
    ch_admin(
        ch,
        "INSERT INTO nova.shipment_events FORMAT JSONEachRow\n" + "\n".join(json.dumps(r) for r in rows),
        date_time_input_format="best_effort",
    )
    subs: dict[str, str] = {}

    async def override(request: Request) -> TenantCaller:
        role, _, slug = request.headers.get("x-user", "ops@acme").partition("@")
        sub = subs.setdefault(f"{role}@{slug}", str(uuid.uuid4()))
        p = Principal(
            sub=sub, email=None, name=role, org_id=None, org_alias=slug, roles=frozenset({ROLE_OF[role]})
        )
        return TenantCaller(Caller(p, tenants[slug], slug), tenants[slug])

    app.dependency_overrides[tenant_caller] = override

    def as_user(who: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t/api/v1",
            headers={"x-user": who},
            timeout=60,
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        t._client = env.client
        async with (
            Worker(
                env.client,
                task_queue=t.ENGINE_QUEUE,
                workflows=[NovaWorkflow, ScheduledRun],
                activities=activities.ALL,
            ),
            Worker(env.client, task_queue=t.AGENTS_QUEUE, activities=agent_acts.ALL),
        ):
            yield {"as": as_user, "tenants": tenants, "env": env}
    t._client = None
    llm.use(None)
    clickhouse.use(None)
    vectors.embed = real_embed  # type: ignore[assignment]
    app.dependency_overrides.clear()


async def wait_run(c: httpx.AsyncClient, run_id: str, done: set[str]) -> dict[str, Any]:
    for _ in range(400):
        run = (await c.get(f"/runs/{run_id}")).json()
        if run["status"] in done:
            return dict(run)
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id}: {run['status']} {run['steps'][-1:]}")


async def detect(w3: dict[str, Any], tenant: str) -> dict[str, Any]:
    ops = w3["as"](f"ops@{tenant}")
    r = await ops.post("/runs", json={"workflow_key": "detect_exceptions", "input": {}})
    assert r.status_code == 201, r.text
    run = await wait_run(ops, r.json()["id"], {"completed", "needs_attention", "failed"})
    assert run["status"] == "completed", run["steps"]
    return run


async def exceptions(tenant_id: Any) -> list[dict[str, Any]]:
    async with db.tenant_session(tenant_id) as s:
        rows = await s.execute(
            text("select id, tenant_id, shipment_id, type, severity, status, run_id from exceptions")
        )
        return [dict(r._mapping) for r in rows]


async def test_clickhouse_row_policy_isolates_tenants(w3: dict[str, Any], ch: str) -> None:
    """Completes the M4 cross-tenant suite: the analyst user reads only the tenant in SQL_tenant_id."""
    acme, bolt = w3["tenants"]["acme"], w3["tenants"]["bolt"]
    ids = {t: {s["id"] for s in SEED["shipments"] if s["tenant"] == t} for t in ("acme", "bolt")}
    got = await clickhouse.query_metric(acme, "eta_slip_hours", limit=500)
    assert {r["shipment_id"] for r in got["rows"]} == ids["acme"]
    reader = clickhouse.client()
    rows = await reader.query(bolt, "SELECT DISTINCT shipment_id FROM nova.shipment_events", {})
    assert {r["shipment_id"] for r in rows} == ids["bolt"]  # Bolt's id: none of Acme's rows
    async with httpx.AsyncClient() as h:  # no tenant setting -> an error, never every tenant's rows
        r = await h.post(
            ch,
            content="SELECT count() FROM nova.shipment_events",
            headers={"X-ClickHouse-User": "nova_reader", "X-ClickHouse-Key": "nova_reader"},
        )
    assert r.status_code != 200 and "SQL_tenant_id" in r.text
    with pytest.raises(clickhouse.MetricError):  # read-only
        await reader.query(acme, "INSERT INTO nova.shipment_events (event_id) VALUES (generateUUIDv4())", {})


async def test_one_cycle_raises_exactly_the_scripted_incidents(w3: dict[str, Any]) -> None:
    for tenant in ("acme", "bolt"):
        run = await detect(w3, tenant)
        out = next(s for s in run["steps"] if s["node_id"] == "detect")["output"]
        assert out["rules"] == 4 and len(out["new"]) == {"acme": 4, "bolt": 2}[tenant]
    got = [
        (str(e["shipment_id"]), e["type"]) for tid in w3["tenants"].values() for e in await exceptions(tid)
    ]
    assert sorted(got) == sorted(EXPECTED)  # all 6, no false positive from normal ETA jitter
    await detect(w3, "acme")  # the next cycle: the same breaches are already raised
    assert len(await exceptions(w3["tenants"]["acme"])) == 4


async def test_router_replay_starts_one_triage_per_exception(w3: dict[str, Any]) -> None:
    from nova_ingest import router

    client = await t.client()
    started = []
    for tid in w3["tenants"].values():
        for e in await exceptions(tid):
            rec = {"__op": "c", **{k: str(v) if v is not None else None for k, v in e.items()}}
            started += await router.handle(rec, client)
            assert await router.handle(rec, client) == []  # Kafka redelivery
            assert await router.handle({**rec, "__op": "r"}, client) == []  # snapshot re-read
    assert len(started) == 6
    for tid in w3["tenants"].values():
        async with db.tenant_session(tid) as s:
            n = (
                await s.execute(text("select count(*) from workflow_runs where subject_type = 'exception'"))
            ).scalar()
            linked = (
                await s.execute(text("select count(*) from exceptions where run_id is not null"))
            ).scalar()
        assert n == linked == {w3["tenants"]["acme"]: 4, w3["tenants"]["bolt"]: 2}[tid]


async def test_triage_cites_an_sop_and_the_customer_is_notified(w3: dict[str, Any]) -> None:
    from nova_ingest import notifier

    for slug, tid in w3["tenants"].items():
        lead = w3["as"](f"lead@{slug}")
        for e in await exceptions(tid):
            run = await wait_run(lead, str(e["run_id"]), {"waiting_human", "needs_attention", "failed"})
            assert run["status"] == "waiting_human", run["steps"][-1]
            task = (await lead.get(f"/tasks/{run['tasks'][0]['id']}")).json()
            assert task["app_key"] == "exception_panel"
            recs = task["payload"]["recommendations"]
            assert recs and all(r["sop_ref"] in SOP_IDS for r in recs)
            assert task["payload"]["facts"][0]["sql"].startswith("SELECT")
            assert (await lead.post(f"/tasks/{task['id']}/claim")).status_code == 200
            done = await lead.post(f"/tasks/{task['id']}/complete", json={"decision": "accept"})
            assert done.status_code == 200, done.text
            assert (await wait_run(lead, run["id"], {"completed", "failed"}))["status"] == "completed"
        listed = (await lead.get("/exceptions")).json()
        assert {x["status"] for x in listed} == {"resolved"} and all(x["note"] for x in listed)
        # the EventRouter's messages: payload as value, id/tenant_id/type as headers
        async with db.tenant_session(tid) as s:
            outbox = (
                await s.execute(
                    text("select id, tenant_id, type, payload from outbox where aggregate = 'shipment'")
                )
            ).all()
        for o in outbox:
            headers = [
                ("id", str(o.id).encode()),
                ("tenant_id", str(o.tenant_id).encode()),
                ("type", o.type.encode()),
            ]
            assert await notifier.store("nova.outbox.shipment", headers, json.dumps(o.payload).encode())
            assert not await notifier.store("nova.outbox.shipment", headers, json.dumps(o.payload).encode())
        sink = (await lead.get("/notifications")).json()
        assert len(sink) == len(await exceptions(tid)) == {"acme": 4, "bolt": 2}[slug]
        assert all(n["refs"]["sop_refs"] and "re-planning" in n["body"] for n in sink)


async def test_analytics_and_exceptions_respect_roles_and_tenants(w3: dict[str, Any]) -> None:
    lead, ops, bolt = w3["as"]("lead@acme"), w3["as"]("ops@acme"), w3["as"]("lead@bolt")
    m = await lead.get("/analytics/metrics/eta_slip_hours")
    assert m.status_code == 200 and m.json()["sql"].startswith("SELECT")
    assert (await lead.get("/analytics/metrics/raw_sql")).status_code == 404  # only registered metrics
    assert (await ops.get("/analytics/metrics/eta_slip_hours")).status_code == 403  # ops_exec: no analytics
    acme_ids = {x["id"] for x in (await lead.get("/exceptions")).json()}
    assert acme_ids and acme_ids.isdisjoint({x["id"] for x in (await bolt.get("/exceptions")).json()})
    assert (await bolt.get(f"/exceptions/{next(iter(acme_ids))}")).status_code == 404
