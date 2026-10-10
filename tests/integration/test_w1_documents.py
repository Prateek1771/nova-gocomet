"""W1 end to end on real Postgres + Temporal test server + the real agents (pdfplumber, matching, checks,
decide, persistence) and the real mock-TMS action. Only the model is faked: it answers with each seed
case's ground truth, so this proves everything around the LLM. Object storage is in-memory."""

import importlib.util
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nova_core import db, storage
from nova_core import temporal as t
from nova_core.auth import Principal
from nova_core.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "definitions/seed/bol_cases.json").read_text(encoding="utf-8"))["cases"]
_spec = importlib.util.spec_from_file_location("gen_bols", ROOT / "scripts/gen_bols.py")
assert _spec and _spec.loader
gen_bols = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_bols)


def fake_model(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    alias, user = body["model"], body["messages"][-1]["content"]
    blob = json.dumps(user)
    if alias == "nova-extract-scan":  # DPT-2 through the gateway, recorded on the planted scan (ADR-034)
        answer: Any = json.loads(
            (ROOT / "tests/fixtures/landingai/bol_10_scan.json").read_text(encoding="utf-8")
        )
    elif alias.startswith("nova-extract"):
        if isinstance(user, list) and any(p.get("type") == "image_url" for p in user):
            case = next(c for c in CASES if c["scan"])  # the only image-only document
        else:
            case = next(c for c in CASES if c["fields"]["bol_number"] in blob)
        answer = case["fields"]
    elif alias == "nova-reason":
        answer = {"consistent": True, "why": "matches"}
    else:  # decide: material iff there are issues to judge
        qs = json.loads(user)
        answer = {"answers": {q["id"]: bool(q["context"]) for q in qs}, "why": {q["id"]: "t" for q in qs}}
    return httpx.Response(
        200, json={"model": f"fake/{alias}", "choices": [{"message": {"content": json.dumps(answer)}}]}
    )


@pytest.fixture(scope="module")
async def w1(pg: tuple[str, str]) -> AsyncIterator[dict[str, Any]]:
    owner, app_url = pg
    os.environ.update(DATABASE_URL=app_url, MIGRATIONS_DATABASE_URL=owner)
    for cached in (get_settings, db.engine, db._sessions):
        cached.cache_clear()
    from nova_api import fga

    fga._http = fga._store = None  # bound to the previous module's event loop
    from nova_agents import activities as agent_acts
    from nova_agents import llm
    from nova_api import seed
    from nova_api.authz import TenantCaller, tenant_caller
    from nova_api.deps import Caller
    from nova_api.main import app
    from nova_engine import activities
    from nova_engine.interpreter import NovaWorkflow

    await seed.main()
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
    acme = tenants["acme"]
    sub = str(uuid.uuid4())

    async def override(request: Request) -> TenantCaller:  # `x-tenant: bolt` = a Bolt operator
        slug = request.headers.get("x-tenant", "acme")
        p = Principal(
            sub=sub,
            email=None,
            name="ops",
            org_id=None,
            org_alias=slug,
            roles=frozenset({"ops_exec", "process_designer"}),
        )
        return TenantCaller(Caller(p, tenants[slug], slug), tenants[slug])

    app.dependency_overrides[tenant_caller] = override
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=60)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        t._client = env.client
        async with (
            Worker(
                env.client, task_queue=t.ENGINE_QUEUE, workflows=[NovaWorkflow], activities=activities.ALL
            ),
            Worker(env.client, task_queue=t.AGENTS_QUEUE, activities=agent_acts.ALL),
        ):
            yield {"c": client, "tenant": acme, "blobs": blobs}
    t._client = None
    llm.use(None)
    storage.put, storage.get = storage_put, storage_get  # type: ignore[assignment]
    app.dependency_overrides.clear()


async def _settled(c: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    import asyncio

    for _ in range(300):
        run = (await c.get(f"/runs/{run_id}")).json()
        if run["status"] in {"completed", "waiting_human", "failed", "needs_attention", "rejected"}:
            return dict(run)
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} never settled: {run['status']}")


def _pdf(case: dict[str, Any]) -> bytes:
    data = gen_bols.render(case)
    return gen_bols.scan(data, case.get("smudge")) if case["scan"] else data


async def test_ten_seed_bols_route_as_specified(w1: dict[str, Any]) -> None:
    c = w1["c"]
    runs, sent = {}, {}
    for case in CASES:
        sent[case["file"]] = _pdf(case)
        r = await c.post("/documents", files={"file": (case["file"], sent[case["file"]], "application/pdf")})
        assert r.status_code == 201, r.text
        doc = r.json()
        assert not doc["duplicate"] and doc["run_id"] and doc["pages"] == 1
        runs[case["file"]] = (case, doc)
    assert all(k.startswith(f"tenant/{w1['tenant']}/documents/") for k in w1["blobs"])

    touchless, reviewed = [], []
    for case, doc in runs.values():
        run = await _settled(c, doc["run_id"])
        if case["expect_review"]:
            assert run["status"] == "waiting_human", (case["file"], run["status"])
            [task] = run["tasks"]
            full = (await c.get(f"/tasks/{task['id']}")).json()
            codes = sorted({i["code"] for i in full["payload"]["issues"]})
            # the scan's printed data is valid; its issue comes from what DPT-2 can read past the stain
            assert codes == sorted(case.get("review_issues", case["expected_issues"])), case["file"]
            ev = full["payload"]["issues"][0]["evidence"]  # the highlight points at the offending box
            assert ev and ev[0]["bbox"] and ev[0]["page"] == 1, (case["file"], ev)
            reviewed.append((case, task["id"]))
        else:
            assert run["status"] == "completed" and not run["tasks"], (case["file"], run)
            touchless.append(case)
    assert len(touchless) == 4 and len(reviewed) == 6

    async with db.tenant_session(w1["tenant"]) as s:
        n_ext = (await s.execute(text("select count(*) from extractions"))).scalar()
        shipped = set((await s.execute(text("select bol_number from shipments"))).scalars())
    assert n_ext == 10
    assert shipped == {case["fields"]["bol_number"] for case in touchless}

    # reviewer fixes the weight on the WEIGHT_SUM BoL and approves: the edit is what reaches the TMS
    case, task_id = next((cs, tid) for cs, tid in reviewed if cs["planted"] == "WEIGHT_SUM")
    fixed = {
        **case["fields"],
        "gross_weight_kg": sum(ln["weight_kg"] for ln in case["fields"]["cargo_lines"]),
    }
    assert (await c.post(f"/tasks/{task_id}/claim")).status_code == 200
    bad = await c.post(f"/tasks/{task_id}/complete", json={"decision": "rejected"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_task_output"
    ok = await c.post(
        f"/tasks/{task_id}/complete", json={"decision": "approved", "payload": {"fields": fixed}}
    )
    assert ok.status_code == 200, ok.text
    run = await _settled(c, runs[case["file"]][1]["run_id"])
    assert run["status"] == "completed"
    async with db.tenant_session(w1["tenant"]) as s:
        row = (
            await s.execute(
                text("select fields from shipments where bol_number = :b"),
                {"b": case["fields"]["bol_number"]},
            )
        ).one()
    assert row.fields["gross_weight_kg"] == fixed["gross_weight_kg"]

    # same bytes again (reportlab stamps a creation time, so reuse what was sent): deduped, no second run
    again = await c.post(
        "/documents", files={"file": ("again.pdf", sent[CASES[0]["file"]], "application/pdf")}
    )
    assert again.status_code == 201 and again.json()["duplicate"] is True
    assert again.json()["id"] == runs[CASES[0]["file"]][1]["id"]
    docs = (await c.get("/documents")).json()
    assert len(docs) == 10 and {d["run_status"] for d in docs} >= {"completed", "waiting_human"}
    pdf = await c.get(f"/documents/{docs[0]['id']}/file")
    # cross-tenant (07 M4): objects live under tenant/<id>/ and Bolt can't reach Acme's document or bytes
    assert w1["blobs"] and all(k.startswith(f"tenant/{w1['tenant']}/") for k in w1["blobs"])
    bolt = {"x-tenant": "bolt"}
    assert (await c.get(f"/documents/{docs[0]['id']}", headers=bolt)).status_code == 404
    assert (await c.get(f"/documents/{docs[0]['id']}/file", headers=bolt)).status_code == 404
    assert (await c.get("/documents", headers=bolt)).json() == []
    assert (
        pdf.status_code == 200
        and pdf.content.startswith(b"%PDF")
        and re.search("inline", pdf.headers["content-disposition"])
    )


async def test_studio_publish_pins_in_flight_runs_and_streams(w1: dict[str, Any]) -> None:
    """M3 exit criterion at the API: publish v2 while a run waits on v1; it finishes on v1, new runs use v2.
    Plus catalog-backed validation, the versions list and the SSE stream."""
    c = w1["c"]
    assert {a["key"] for a in (await c.get("/catalog/agents")).json()} >= {"doc_extractor", "bol_validator"}
    assert "tms.upsert_shipment" in {a["key"] for a in (await c.get("/catalog/actions")).json()}

    v1 = (await c.get("/workflows/bol_intake/versions")).json()
    assert [v["version"] for v in v1] == [1]
    yaml = (await c.get("/workflows/bol_intake/versions/1")).json()["yaml"]

    bad = (
        await c.post("/workflows/bol_intake/validate", json={"yaml": yaml.replace("bol_validator", "nope")})
    ).json()
    assert not bad["valid"] and {(i["code"], i["node_id"]) for i in bad["issues"]} == {
        ("unknown_agent", "validate")
    }

    waiting = next(r for r in (await c.get("/runs?status=waiting_human")).json())
    assert waiting["version"] == 1
    v2_yaml = yaml.replace("threshold: 0.4", "threshold: 0.45")
    assert (await c.put("/workflows/bol_intake/draft", json={"yaml": v2_yaml})).json()["valid"]
    assert (await c.post("/workflows/bol_intake/publish")).json()["version"] == 2

    task = (await c.get(f"/runs/{waiting['id']}")).json()["tasks"][0]
    await c.post(f"/tasks/{task['id']}/claim")
    done = await c.post(
        f"/tasks/{task['id']}/complete", json={"decision": "rejected", "payload": {"reason": "t"}}
    )
    assert done.status_code == 200, done.text
    old = await _settled(c, waiting["id"])
    assert old["status"] == "rejected" and old["version"] == 1

    case = CASES[0]
    up = await c.post("/documents", files={"file": ("v2.pdf", _pdf(case), "application/pdf")})
    new = await _settled(c, up.json()["run_id"])
    assert new["version"] == 2

    # SSE on a finished run: one snapshot, then `end`; the same Last-Event-ID skips the snapshot
    r = await c.get(f"/runs/{new['id']}/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [e for e in r.text.split("\n\n") if e]
    assert (
        events[0].startswith("id: ")
        and "event: snapshot" in events[0]
        and events[-1].startswith("event: end")
    )
    eid = events[0].split("\n")[0][4:]
    again = await c.get(f"/runs/{new['id']}/stream", headers={"last-event-id": eid})
    assert "event: snapshot" not in again.text and "event: end" in again.text
