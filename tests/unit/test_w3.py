"""W3 without Docker: the seed scenario against a pure-Python reference of the four detection rules (exactly
the 6 scripted incidents, no false positives at any sim time), simulator payloads against the Phase 0
event schema, the metric registry, SOP chunking, the agents' evidence and citation guards (ClickHouse,
Weaviate and the model faked), the triage routing from both real YAMLs, and the router/lag helpers."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from nova_agents import exceptions as agents
from nova_agents import llm
from nova_api.clauses import sop_chunks
from nova_core import clickhouse, vectors
from nova_dsl import cel
from nova_engine.actions import delay_body
from nova_ingest import lag, router, simulator

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "definitions/seed/shipments.json").read_text(encoding="utf-8"))
RULES = {  # TenantConfig `exceptions.rules` thresholds (services/api seed.py)
    "acme": {"ETA_SLIP": 24, "DWELL": 72},
    "bolt": {"ETA_SLIP": 12, "DWELL": 72},
}


def breaches(now: float) -> set[tuple[str, str]]:
    """The dbt metrics' logic in Python, at sim day `now`: (shipment id, exception type) over threshold."""
    out = set()
    for tenant in ("acme", "bolt"):
        ships = [s for s in SEED["shipments"] if s["tenant"] == tenant]
        seen = [e["day"] for s in ships for e in s["events"] if e["day"] <= now]
        if not seen:
            continue
        clock = max(seen)
        for s in ships:
            ev = [e for e in s["events"] if e["day"] <= now]
            if not ev:
                continue
            etas = [e["eta"] for e in ev if e["eta"] is not None]
            if (etas[-1] - etas[0]) * 24 > RULES[tenant]["ETA_SLIP"]:
                out.add((s["id"], "ETA_SLIP"))
            ts = [e for e in ev if e["port_role"] == "ts"]
            dis = [e["day"] for e in ts if e["event_type"] == "discharged"]
            loaded = [e["day"] for e in ts if e["event_type"] == "loaded"]
            if dis and ((loaded[-1] if loaded else clock) - dis[0]) * 24 > RULES[tenant]["DWELL"]:
                out.add((s["id"], "DWELL"))
            if any(e["event_type"] == "departed" for e in ts) and not loaded:
                out.add((s["id"], "MISSED_TS"))
            if len({e["booked_vessel"] for e in ev}) > 1:
                out.add((s["id"], "ROLLOVER"))
    return out


def test_seed_raises_exactly_the_scripted_incidents() -> None:
    seen: set[tuple[str, str]] = set()
    for tenth in range(450):  # every 0.1 sim-day across the whole scenario
        seen |= breaches(tenth / 10)
    assert seen == {(i["shipment_id"], i["expect"]) for i in SEED["incidents"]}
    assert len(seen) == 6


def test_simulator_payloads_match_the_event_schema() -> None:
    tenants = {"acme": "4b053ba5-a962-4731-a05f-d481f607db36", "bolt": "14dd1adb-0edb-43fe-b575-bc00ad6c6df8"}
    plan = simulator.events(SEED, tenants)
    check = simulator.validator()
    assert len(plan) == sum(len(s["events"]) for s in SEED["shipments"])
    assert [d for d, _ in plan] == sorted(d for d, _ in plan)
    for _, ev in plan:
        assert not [e.message for e in check.iter_errors(ev)], ev
    assert len({ev["event_id"] for _, ev in plan}) == len(plan)
    assert simulator.events(SEED, {"acme": tenants["acme"]})[0][1]["tenant_id"] == tenants["acme"]


def test_every_rule_and_the_timeline_are_governed_metrics() -> None:
    for metric, op in (("eta_slip_hours", ">"), ("dwell_time_hours", ">"), ("missed_transhipment", "="),
                       ("rollover", ">=")):  # fmt: skip
        sql, params = clickhouse.build(metric, op=op, threshold=1)
        assert f"FROM nova_metrics.{metric}" in sql and params["threshold"] == "1.0"
    sql, params = clickhouse.build("shipment_timeline", shipment_id=SEED["shipments"][0]["id"])
    assert "shipment_id = {shipment_id:UUID}" in sql and "ORDER BY event_time" in sql
    for f in ("eta_slip_hours", "dwell_time_hours", "missed_transhipment", "rollover", "shipment_timeline"):
        assert (ROOT / "infra/dbt/models" / f"{f}.sql").exists()


def test_sop_corpus_covers_every_exception_type() -> None:
    chunks = sop_chunks()
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)
    by_type: dict[str, set[str]] = {}
    for c in chunks:
        by_type.setdefault(c["exception_type"], set()).add(c["sop_id"])
        assert c["section"] and c["text"]
    assert all(len(by_type[t]) >= 2 for t in ("ETA_SLIP", "DWELL", "MISSED_TS", "ROLLOVER"))
    assert len({c["sop_id"] for c in chunks}) == 15


# --- agents -------------------------------------------------------------------------------------------

SHIPMENT = {"shipment_id": SEED["incidents"][2]["shipment_id"], "container_no": "MSCU2858382", "bol_number": "B1",
            "pol": "VNSGN", "pod": "USNYC", "carrier": "MSC"}  # fmt: skip
EXC = {"exception_id": "e1", "type": "ROLLOVER", "severity": "medium", "detected_at": "2026-10-01T00:00:00",
       "facts": {"metric": "rollover", "row": {"rollovers": 1}}}  # fmt: skip


def state(params: dict[str, Any] | None = None) -> Any:
    return {
        "scope": {"tenant_id": "t", "run_id": "r"},
        "route": {"alias": "nova-reason"},
        "req": {"params": params or {}},
        "ctx": {"exception": EXC, "shipment": SHIPMENT},
        "meta": {"cost_usd": 0.0, "calls": []},
    }


def fake_llm(answer: Any) -> list[Any]:
    asked: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        asked.append(json.loads(json.loads(req.content)["messages"][1]["content"]))
        body = {"model": "fake", "choices": [{"message": {"content": json.dumps(answer)}}], "usage": {}}
        return httpx.Response(200, json=body)

    llm.use(llm.LLM(transport=httpx.MockTransport(handler)))
    return asked


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queried: list[str] = []

    async def query_metric(
        tenant: Any, metric: str, shipment_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        queried.append(metric)
        row = (
            {"slip_hours": 20.4}
            if metric == "eta_slip_hours"
            else {"rollovers": 1, "booked_now": "MSC OSCAR"}
        )
        return {
            "metric": metric,
            "sql": f"SELECT … FROM nova_metrics.{metric}",  # noqa: S608
            "params": {},
            "rows": [row],
        }

    monkeypatch.setattr(clickhouse, "query_metric", query_metric)
    yield queried
    llm.use(None)


async def test_analyst_facts_come_from_code_and_severity_is_clamped(metrics: list[str]) -> None:
    asked = fake_llm({"severity": "catastrophic", "summary": "Rolled to MSC OSCAR, +20 h."})
    out = await agents._analyst_execute(state())
    assert metrics == ["rollover", "eta_slip_hours"]
    assert [f["metric"] for f in out["facts"]] == metrics and all(f["sql"] for f in out["facts"])
    assert out["severity"] == "medium"  # outside the enum -> the configured default
    assert out["slip_hours"] == 20.4 and out["summary"].startswith("Rolled")
    assert asked[0]["facts"][0]["rows"] == [{"rollovers": 1, "booked_now": "MSC OSCAR"}]
    assert not Draft202012Validator(agents.ANALYST_OUTPUT).is_valid({}) and Draft202012Validator(
        agents.ANALYST_OUTPUT
    ).is_valid(out)


HITS = [
    {"chunk_id": f"SOP-ROLL-0{n}#2", "sop_id": f"SOP-ROLL-0{n}", "exception_type": "ROLLOVER", "carrier": "",
     "title": f"Rollover {n}", "section": "Steps", "text": "1. Re-book on the next sailing.\n2. Notify.", "score": 0.9}
    for n in (1, 4)
]  # fmt: skip


@pytest.fixture
def sops(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def search(
        tenant: str, query: str, col: Any, where: Any = None, limit: int = 4
    ) -> list[dict[str, Any]]:
        calls.append({"col": col.name, "where": where})
        return HITS

    monkeypatch.setattr(vectors, "search", search)
    yield calls
    llm.use(None)


async def test_recommender_cites_only_retrieved_sops(sops: list[dict[str, Any]]) -> None:
    fake_llm({"recommendations": [{"n": 2, "action": "Ask MSC for the next sailing.", "why": "MSC roll"},
                                  {"n": 7, "action": "Invented", "why": "x"}, {"n": True, "action": "bool"}]})  # fmt: skip
    out = await agents._recommender_execute(state({"summary": "rolled"}))
    assert sops == [{"col": "Sop", "where": {"exception_type": "ROLLOVER"}}]
    assert [r["sop_ref"] for r in out["recommendations"]] == ["SOP-ROLL-04"]
    assert Draft202012Validator(agents.RECOMMENDER_OUTPUT).is_valid(out)


async def test_recommender_falls_back_to_the_top_sop(sops: list[dict[str, Any]]) -> None:
    fake_llm({"nonsense": True})
    out = await agents._recommender_execute(state())
    (rec,) = out["recommendations"]
    assert rec["sop_ref"] == "SOP-ROLL-01" and rec["action"] == "Re-book on the next sailing."


def test_delay_email_names_what_happened_and_the_sop() -> None:
    body = delay_body(
        {"type": "ROLLOVER", "facts": {"row": {"booked_first": "MSC GULSUN", "booked_now": "MSC OSCAR"}}},
        {"bol_number": "B1", "container_no": "MSCU2858382", "pol": "VNSGN", "pod": "USNYC"},
        [{"action": "Re-booked on the next sailing", "sop_ref": "SOP-ROLL-01"}],
        "Customer prefers email",
    )
    assert "rolled from MSC GULSUN to MSC OSCAR" in body and "procedure SOP-ROLL-01" in body
    assert "Re-booked on the next sailing" not in body  # no draft: a neutral line, never the internal steps
    assert "Customer prefers email" in body
    drafted = delay_body(
        {"type": "ROLLOVER", "facts": {"row": {"booked_first": "A", "booked_now": "B"}}},
        {"bol_number": "B1"},
        [{"action": "Record against MSC", "sop_ref": "SOP-ROLL-01"}],
        "",
        "Your container now sails on B, arriving 30 Oct.",
    )
    assert "arriving 30 Oct" in drafted and "Record against MSC" not in drafted  # no internal steps
    assert "SOP-ROLL-01" in drafted


# --- routing: the real triage rules from both YAMLs, real CEL --------------------------------------------


def route_rule(tenant: str) -> str:
    wf = yaml.safe_load((ROOT / f"definitions/workflows/{tenant}/exception_triage.yaml").read_text("utf-8"))
    (node,) = [n for n in wf["nodes"] if n["id"] == "route"]
    return str(node["cases"][0]["when"])


@pytest.mark.parametrize(
    ("tenant", "actionable", "severity", "human"),
    [("acme", True, "low", True), ("acme", False, "high", False), ("bolt", False, "high", True),
     ("bolt", False, "critical", True), ("bolt", False, "medium", False), ("bolt", True, "low", True)],
)  # fmt: skip
def test_triage_routing_per_tenant(tenant: str, actionable: bool, severity: str, human: bool) -> None:
    nodes = {"triage": {"output": {"actionable": actionable}}, "analyse": {"output": {"severity": severity}}}
    assert cel.truthy(route_rule(tenant), cel.activation({"nodes": nodes}, datetime.now(UTC))) is human


# --- ingest helpers -------------------------------------------------------------------------------------


def test_router_triggers_on_new_open_exceptions_only() -> None:
    base = {"id": "e", "tenant_id": "t", "status": "open", "run_id": None}
    assert router.opened({**base, "__op": "c"}) and router.opened({**base, "__op": "r"})
    assert not router.opened({**base, "__op": "u"})  # our own run_id update, closes, ...
    assert not router.opened({**base, "__op": "r", "run_id": "x"})  # snapshot of a triaged one
    assert not router.opened({**base, "__op": "c", "status": "resolved"})
    assert router.decode(None) is None and router.decode(b'"x"') is None
    assert router.decode(b'{"id": 1}') == {"id": 1}


def test_lag_alerts_only_when_sustained() -> None:
    assert lag.alerting(5, 10, None, 100.0) == (False, None)
    assert lag.alerting(50, 10, None, 100.0) == (False, 100.0)
    assert lag.alerting(50, 10, 100.0, 159.0) == (False, 100.0)
    assert lag.alerting(50, 10, 100.0, 160.0) == (True, 100.0)
    assert lag.alerting(3, 10, 100.0, 200.0) == (False, None)
