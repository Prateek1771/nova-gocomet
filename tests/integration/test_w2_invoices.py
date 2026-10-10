"""W2 end to end (07 M5) on real Postgres + Weaviate + Temporal test server + OpenFGA, with the real agents
(extractor, dedupe, matcher with Weaviate retrieval, decide), the real actions and the real API. Only the
model and the embedder are faked: the model answers with each seed case's ground truth and picks clauses
by charge code; the embedder hashes tokens. All 8 invoices are uploaded for Acme and for Bolt."""

import asyncio
import hashlib
import importlib.util
import json
import math
import os
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

from nova_core import db, storage, vectors
from nova_core import temporal as t
from nova_core.auth import Principal
from nova_core.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "definitions/seed/invoice_cases.json").read_text(encoding="utf-8"))
CASES = SEED["cases"]
_spec = importlib.util.spec_from_file_location("gen_invoices", ROOT / "scripts/gen_invoices.py")
assert _spec and _spec.loader
gen_invoices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_invoices)
CALLS: list[dict[str, Any]] = []  # every model call: alias + run_id


def fake_model(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    alias, user = body["model"], body["messages"][-1]["content"]
    CALLS.append({"alias": alias, "run_id": (body.get("metadata") or {}).get("run_id")})
    if alias.startswith("nova-extract"):
        blob = json.dumps(user)
        case = next(
            c for c in CASES if c["fields"]["invoice_no"] in blob and c["fields"]["invoice_date"] in blob
        )
        answer: Any = case["fields"]
    elif alias == "nova-reason":  # matcher: pick the retrieved clause for the line's charge code
        ask = json.loads(user)
        answer = {
            "lines": [
                {
                    "index": a["index"],
                    "clause": next(
                        (c["n"] for c in a["clauses"] if c["charge_code"] == a["line"]["charge_code"]),
                        None,
                    ),
                }
                for a in ask
            ]
        }
    else:  # decide: an accessorial is unjustified when more days are charged than chargeable
        qs = json.loads(user)
        answer = {
            "answers": {
                q["id"]: any(
                    a.get("chargeable_days") is None or a["charged_days"] > a["chargeable_days"]
                    for a in q["context"]
                )
                for q in qs
            },
            "why": {q["id"]: "events vs free time" for q in qs},
        }
    return httpx.Response(
        200, json={"model": f"fake/{alias}", "choices": [{"message": {"content": json.dumps(answer)}}]}
    )


async def fake_embed(texts: list[str], kind: str, tenant_id: str | None = None) -> list[list[float]]:
    """Hashed bag of words: similar wording lands close, deterministic, no model."""
    out = []
    for txt in texts:
        v = [0.0] * 64
        for w in txt.lower().replace(":", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0  # noqa: S324 (not security)
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


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
        for _ in range(150):
            try:
                if httpx.get(f"{url}/v1/.well-known/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        yield url


ROLE_OF = {"ops": "ops_exec", "lead": "ops_lead", "fin": "finance", "ctrl": "controller"}


@pytest.fixture(scope="module")
async def w2(pg: tuple[str, str], weaviate: str) -> AsyncIterator[dict[str, Any]]:
    owner, app_url = pg
    os.environ.update(DATABASE_URL=app_url, MIGRATIONS_DATABASE_URL=owner, WEAVIATE_URL=weaviate)
    for cached in (get_settings, db.engine, db._sessions):
        cached.cache_clear()
    from nova_api import fga

    fga._http = fga._store = None
    vectors.use(None)
    from nova_agents import activities as agent_acts
    from nova_agents import llm
    from nova_api import clauses, seed
    from nova_api.authz import TenantCaller, tenant_caller
    from nova_api.deps import Caller
    from nova_api.main import app
    from nova_engine import activities
    from nova_engine.interpreter import NovaWorkflow

    await seed.main()
    real_embed, vectors.embed = vectors.embed, fake_embed  # type: ignore[assignment]
    await clauses.index_all(attempts=1)
    blobs: dict[str, bytes] = {}

    async def put(k: str, data: bytes, content_type: str) -> None:
        blobs[k] = data

    async def get(k: str) -> bytes:
        return blobs[k]

    storage_put, storage_get = storage.put, storage.get
    storage.put, storage.get = put, get  # type: ignore[assignment]
    llm.use(llm.LLM(transport=httpx.MockTransport(fake_model)))
    async with db.session() as s:
        tenants = {r.slug: r.id for r in await s.execute(text("select slug, id from tenants"))}
    subs: dict[str, str] = {}

    async def override(request: Request) -> TenantCaller:  # x-user: "<role>@<tenant>"
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
                env.client, task_queue=t.ENGINE_QUEUE, workflows=[NovaWorkflow], activities=activities.ALL
            ),
            Worker(env.client, task_queue=t.AGENTS_QUEUE, activities=agent_acts.ALL),
        ):
            yield {"as": as_user, "tenants": tenants, "runs": {}}
    t._client = None
    llm.use(None)
    vectors.embed = real_embed  # type: ignore[assignment]
    storage.put, storage.get = storage_put, storage_get  # type: ignore[assignment]
    app.dependency_overrides.clear()


async def settled(c: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    for _ in range(400):
        run = (await c.get(f"/runs/{run_id}")).json()
        if run["status"] in {"completed", "failed", "needs_attention", "rejected"} or run["tasks"]:
            return dict(run)
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} never settled: {run['status']}")


def branch(run: dict[str, Any]) -> str:
    if run["status"] in ("completed", "rejected") and not run["tasks"]:
        nodes = [s["node_id"] for s in run["steps"]]
        return "pay" if "pay" in nodes else "rejected"
    return {"l1": "l1_only", "l1_l2": "l1_then_l2"}.get(
        run["tasks"][0]["node_id"], run["tasks"][0]["node_id"]
    )


@pytest.mark.parametrize("tenant", ["acme", "bolt"])
async def test_every_invoice_routes_as_specified(w2: dict[str, Any], tenant: str) -> None:
    """07 M5 exit: all 8 cases route correctly for both tenants (the "+l3" of Bolt shows up after L2)."""
    ops = w2["as"](f"ops@{tenant}")
    got = {}
    for case in CASES:  # in file order: INV-5 must arrive after INV-1
        files = {"file": (case["file"], gen_invoices.render(case), "application/pdf")}
        up = await ops.post("/documents", files=files, data={"doc_type": "invoice"})
        assert up.status_code == 201 and not up.json()["duplicate"], up.text
        run = await settled(ops, up.json()["run_id"])
        assert run["status"] != "needs_attention", run["steps"][-1]
        w2["runs"][(tenant, case["file"])] = run
        got[case["file"]] = branch(run)
    want = {c["file"]: c["expect"][tenant].replace("+l3", "") for c in CASES}
    assert got == want


async def test_duplicate_makes_no_model_call_after_extraction(w2: dict[str, Any]) -> None:
    run = w2["runs"][("acme", "inv_05.pdf")]
    aliases = [c["alias"] for c in CALLS if c["run_id"] == run["id"]]
    assert aliases == ["nova-extract-text"]  # extraction only: no matcher, no decide
    assert [s["node_id"] for s in run["steps"]][-2:] == ["dup", "duplicate"]


async def test_dispute_email_carries_the_cited_clause(w2: dict[str, Any]) -> None:
    lead, fin = w2["as"]("lead@acme"), w2["as"]("fin@acme")
    run = w2["runs"][("acme", "inv_04.pdf")]
    tid = run["tasks"][0]["id"]
    task = (await lead.get(f"/tasks/{tid}")).json()
    assert {i["code"] for i in task["payload"]["issues"]} == {"RATE_OVER_CONTRACT"}
    assert task["payload"]["cited_clauses"][0]["clause_id"] == "MAEU-RC-2026 §3.1"
    await lead.post(f"/tasks/{tid}/claim")
    assert (await lead.post(f"/tasks/{tid}/complete", json={"decision": "approved"})).status_code == 200
    run = await _wait_task(fin, run["id"], "l2")
    l2 = run["tasks"][0]["id"]
    await fin.post(f"/tasks/{l2}/claim")
    no_reason = await fin.post(f"/tasks/{l2}/complete", json={"decision": "dispute"})
    assert no_reason.status_code == 422  # invoice_review output_schema: a dispute needs a reason
    done = await fin.post(
        f"/tasks/{l2}/complete", json={"decision": "dispute", "payload": {"reason": "OFR above contract"}}
    )
    assert done.status_code == 200
    final = await _wait_status(fin, run["id"], "rejected")
    assert [s["node_id"] for s in final["steps"]][-2:] == ["dispute", "disputed"]
    async with db.tenant_session(w2["tenants"]["acme"]) as s:
        body = (
            await s.execute(text("select payload ->> 'body' from outbox where type = 'email.dispute'"))
        ).scalar_one()
    clause = SEED["contracts"][0]["clauses"][0]
    assert "MAEU-RC-2026 §3.1" in body and clause["text"] in body and "OFR above contract" in body


async def test_bolt_three_levels_and_limits(w2: dict[str, Any]) -> None:
    """Demo steps 5 and 8: Bolt's 12,615 invoice needs L1, L2 and the controller; an ops lead (Bolt
    limit 5,000) can't act on it even through the API."""
    lead, fin, ctrl = w2["as"]("lead@bolt"), w2["as"]("fin@bolt"), w2["as"]("ctrl@bolt")
    run = w2["runs"][("bolt", "inv_08.pdf")]
    l1 = run["tasks"][0]["id"]
    refused = await lead.post(f"/tasks/{l1}/claim")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "above_approval_limit"
    for who, node in ((fin, "l1_l2"), (fin, "l2")):
        run = await _wait_task(who, run["id"], node)
        tid = run["tasks"][0]["id"]
        await who.post(f"/tasks/{tid}/claim")
        assert (await who.post(f"/tasks/{tid}/complete", json={"decision": "approved"})).status_code == 200
    child = await _wait_child_task(ctrl)
    assert (await fin.post(f"/tasks/{child}/claim")).status_code == 403  # controller only
    await ctrl.post(f"/tasks/{child}/claim")
    assert (await ctrl.post(f"/tasks/{child}/complete", json={"decision": "approved"})).status_code == 200
    final = await _wait_status(fin, run["id"], "completed")
    assert "l3" in [s["node_id"] for s in final["steps"]]
    async with db.tenant_session(w2["tenants"]["bolt"]) as s:
        status = (
            await s.execute(text("select status from invoices where invoice_no = 'MAEU-INV-26-0108'"))
        ).scalar_one()
    assert status == "posted"


async def test_clause_index_is_tenant_isolated(w2: dict[str, Any]) -> None:
    acme, bolt = (str(w2["tenants"][k]) for k in ("acme", "bolt"))
    probe = {"clause_id": "BOLT-ONLY §1", "contract_no": "X", "carrier_scac": "ZZZZ", "charge_code": "XYZ",
             "title": "Bolt private surcharge", "text": "zebra quokka surcharge", "rate": 1, "unit": "each"}  # fmt: skip
    assert await vectors.index_clauses(bolt, [probe]) == 1
    assert [h["clause_id"] for h in await vectors.search_clauses(bolt, "zebra quokka", "ZZZZ")] == [
        "BOLT-ONLY §1"
    ]
    assert await vectors.search_clauses(acme, "zebra quokka", "ZZZZ") == []


async def _wait_task(c: httpx.AsyncClient, run_id: str, node: str) -> dict[str, Any]:
    for _ in range(300):
        run = (await c.get(f"/runs/{run_id}")).json()
        if run["tasks"] and run["tasks"][0]["node_id"] == node:
            return dict(run)
        await asyncio.sleep(0.1)
    raise AssertionError(f"no {node} task on {run_id}")


async def _wait_status(c: httpx.AsyncClient, run_id: str, status: str) -> dict[str, Any]:
    for _ in range(300):
        run = (await c.get(f"/runs/{run_id}")).json()
        if run["status"] == status:
            return dict(run)
        await asyncio.sleep(0.1)
    raise AssertionError(f"{run_id} never reached {status}: {run['status']}")


async def _wait_child_task(ctrl: httpx.AsyncClient) -> str:
    for _ in range(300):
        tasks = [x for x in (await ctrl.get("/tasks")).json() if x["node_id"] == "signoff"]
        if tasks:
            return str(tasks[0]["id"])
        await asyncio.sleep(0.1)
    raise AssertionError("controller never got the L3 task")
