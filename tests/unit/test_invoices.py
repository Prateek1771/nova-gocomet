"""W2 matcher on the seed cases (definitions/seed/invoice_cases.json), no DB and no model: the context is
built from the seed, retrieval and the clause-confirming model are faked. Plus the citation guard, the
dispute email body and the approval routing per tenant (the real rules from both YAMLs, real CEL)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from nova_agents import invoices, llm
from nova_core import vectors
from nova_dsl import cel
from nova_engine.actions import dispute_body

ROOT = Path(__file__).resolve().parents[2]
SEED = json.loads((ROOT / "definitions/seed/invoice_cases.json").read_text(encoding="utf-8"))
CASES = {c["file"]: c for c in SEED["cases"]}
CONFIG = {  # services/api seed.py TenantConfig v1 (invoice.* per docs/04)
    "acme": {"auto_variance_pct": 2, "auto_limit_usd": 2000, "l2_limit_usd": 10000, "l2_variance_pct": 5},
    "bolt": {"auto_variance_pct": 1, "auto_limit_usd": 1000, "l2_limit_usd": 5000, "l2_variance_pct": 3},
}
CLAUSES = {
    cl["clause_id"]: {**cl, "contract_no": k["contract_no"], "carrier_scac": k["carrier_scac"]}
    for k in SEED["contracts"]
    for cl in k["clauses"]
}


def ctx_for(case: dict[str, Any]) -> dict[str, Any]:
    f = case["fields"]
    po = next((p for p in SEED["purchase_orders"] if p["po_number"] == f["po_number"]), None)
    k = next(k for k in SEED["contracts"] if k["carrier_scac"] == f["carrier_scac"])
    return {
        "fields": f,
        "evidence": [
            {"field": f"lines[{i}].amount", "page": 1, "bbox": [1, 2, 3, 4]} for i in range(len(f["lines"]))
        ],
        "po": {"id": "p", "po_number": po["po_number"], "currency": po["currency"]} if po else None,
        "po_lines": {
            ln["charge_code"]: {"qty": ln["qty"], "unit_rate": ln["unit_rate"]}
            for ln in (po or {}).get("lines", [])
        },
        "contract": {
            "id": "k",
            "contract_no": k["contract_no"],
            "currency": k["currency"],
            "free_time_days": k["free_time_days"],
        },
        "clauses": {c["clause_id"]: {**c, "rate": float(c["rate"])} for c in k["clauses"]},
        "fx": SEED["fx"],
        "events": [
            {**e, "event_time": datetime.fromisoformat(e["event_time"]).astimezone(UTC)}
            for e in SEED["container_events"]
            if e["container_no"] in f["container_numbers"]
        ],
    }


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Retrieval returns the carrier's clauses (right one first); the model picks the clause whose charge
    code matches, unless `lie` is set, then it picks a number that wasn't retrieved."""
    state: dict[str, Any] = {"lie": False, "calls": 0}

    async def search(
        tenant: str, query: str, carrier: str | None = None, limit: int = 4
    ) -> list[dict[str, Any]]:
        code = query.split()[0]
        hits = [c for c in CLAUSES.values() if c["carrier_scac"] == carrier]
        return sorted(hits, key=lambda c: c["charge_code"] != code)[:limit]

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        ask = json.loads(json.loads(req.content)["messages"][1]["content"])
        picks = [
            {
                "index": a["index"],
                "clause": 99  # not among the retrieved candidates
                if state["lie"]
                else next(
                    (c["n"] for c in a["clauses"] if c["charge_code"] == a["line"]["charge_code"]), None
                ),
            }
            for a in ask
        ]
        body = {
            "model": "fake",
            "choices": [{"message": {"content": json.dumps({"lines": picks})}}],
            "usage": {},
        }
        return httpx.Response(200, json=body)

    monkeypatch.setattr(vectors, "search_clauses", search)
    llm.use(llm.LLM(transport=httpx.MockTransport(handler)))
    yield state
    llm.use(None)


async def match(case: dict[str, Any]) -> dict[str, Any]:
    s: Any = {
        "scope": {"tenant_id": "t", "run_id": "r"},
        "route": {"alias": "nova-reason"},
        "ctx": ctx_for(case),
        "meta": {"cost_usd": 0.0, "calls": []},
    }
    return await invoices._execute(s)


EXPECTED_CODES = {
    "inv_01.pdf": set(),
    "inv_02.pdf": set(),
    "inv_03.pdf": set(),
    "inv_04.pdf": {"RATE_OVER_CONTRACT"},
    "inv_05.pdf": set(),  # the duplicate is caught before the matcher (dedupe)
    "inv_06.pdf": {"DET_NOT_SUPPORTED"},
    "inv_07.pdf": {"CURRENCY_MISMATCH", "RATE_OVER_CONTRACT"},  # EUR billed against a USD contract
    "inv_08.pdf": set(),
}


@pytest.mark.parametrize("file", sorted(CASES))
async def test_matcher_flags_each_planted_error_and_only_it(fakes: dict[str, Any], file: str) -> None:
    out = await match(CASES[file])
    assert {i["code"] for i in out["issues"]} == EXPECTED_CODES[file], out["issues"]
    assert all(i["evidence"] for i in out["issues"] if i["field"].startswith("lines["))
    if cited := CASES[file].get("cited_clause"):
        assert cited in {c["clause_id"] for c in out["cited_clauses"]}
        assert any(i.get("clause_id") == cited for i in out["issues"])


async def test_rate_and_fx_arithmetic(fakes: dict[str, Any]) -> None:
    over = await match(CASES["inv_04.pdf"])
    ofr = over["matches"][0]
    assert (
        ofr["contract_rate"] == 1850
        and ofr["status"] == "over"
        and ofr["variance_pct"] == pytest.approx(7.03, abs=0.01)
    )
    assert over["total_usd"] == 6735 and over["expected_usd"] == 6345
    assert over["variance_pct"] == pytest.approx(6.15, abs=0.01)
    eur = await match(CASES["inv_07.pdf"])
    assert eur["total_usd"] == pytest.approx(1460 * 1.08) and eur["currency"] == "EUR"
    det = (await match(CASES["inv_06.pdf"]))["accessorials"][0]
    assert det | {"clause": None} == det | {
        "charged_days": 4, "free_time_days": 5, "dwell_days": 4, "chargeable_days": 0, "clause_id": "MAEU-RC-2026 §7.1", "clause": None,
    }  # fmt: skip


async def test_citation_outside_retrieved_set_is_discarded(fakes: dict[str, Any]) -> None:
    fakes["lie"] = True  # the model picks a clause that retrieval never returned
    out = await match(CASES["inv_04.pdf"])
    assert out["cited_clauses"] == [] and all(m["clause_id"] is None for m in out["matches"])
    assert {i["code"] for i in out["issues"]} >= {"NO_CONTRACT_RATE"}
    assert fakes["calls"] == 1  # one confirming call per invoice, not per line


def test_dwell_days_needs_both_events() -> None:
    t = datetime(2026, 9, 10, tzinfo=UTC)
    assert invoices.dwell_days([], None) is None
    assert (
        invoices.dwell_days([{"container_no": "X", "event_type": "gate_out", "event_time": t}], None) is None
    )
    back = {"container_no": "X", "event_type": "gate_in", "event_time": t.replace(day=12, hour=1)}
    assert (
        invoices.dwell_days([{"container_no": "X", "event_type": "gate_out", "event_time": t}, back], "X")
        == 3
    )


async def test_dispute_email_cites_the_clause(fakes: dict[str, Any]) -> None:
    out = await match(CASES["inv_04.pdf"])
    body = dispute_body(
        CASES["inv_04.pdf"]["fields"], out["issues"], out["cited_clauses"], "rate above contract"
    )
    clause = CLAUSES["MAEU-RC-2026 §3.1"]
    assert "MAEU-RC-2026 §3.1" in body and clause["text"] in body and "MAEU-INV-26-0104" in body


def _route(tenant: str, match_out: dict[str, Any], unjustified: bool, duplicate: bool) -> str:
    wf = yaml.safe_load(
        (ROOT / f"definitions/workflows/{tenant}/invoice_match.yaml").read_text(encoding="utf-8")
    )
    nodes = {n["id"]: n for n in wf["nodes"]}
    act = cel.activation(
        {
            "input": {},
            "tenant": {"invoice": CONFIG[tenant]},
            "nodes": {
                "dedupe": {"output": {"duplicate": duplicate}},
                "match": {"output": match_out},
                "accessorials": {"output": {"unjustified": unjustified}},
            },
        },
        datetime.now(UTC),
    )

    def rule(node: str) -> str:
        r = nodes[node]
        return next((c["goto"] for c in r["cases"] if cel.truthy(c["when"], act)), r["default"])

    if rule("dup") == "duplicate":
        return "rejected"
    first = rule("approval")
    if first == "pay":
        return "pay"
    if first == "l1":
        return "l1_only"
    l3 = "l3_gate" in nodes and rule("l3_gate") == "l3"
    return "l1_then_l2+l3" if l3 else "l1_then_l2"


@pytest.mark.parametrize("tenant", ["acme", "bolt"])
@pytest.mark.parametrize("file", sorted(CASES))
async def test_each_case_routes_as_specified_for_both_tenants(
    fakes: dict[str, Any], tenant: str, file: str
) -> None:
    """07 M5: every planted invoice reaches its expected branch for Acme and for Bolt."""
    case = CASES[file]
    out = await match(case)
    unjustified = any(
        a["chargeable_days"] is None or a["charged_days"] > a["chargeable_days"] for a in out["accessorials"]
    )
    assert (
        _route(tenant, out, unjustified, duplicate=case["planted"] == "DUPLICATE") == case["expect"][tenant]
    )
