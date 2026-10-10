"""Scans through nova-extract-scan (LandingAI DPT-2 Parse + Extract behind the gateway, ADR-034), replayed
from a recorded response for the planted scan bol_10 (an ink stain hides its first container number).
No live calls."""

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nova_agents import llm
from nova_agents.extractor import ade_schema, extract
from nova_agents.llm import BudgetExceeded
from nova_agents.matching import Word, chunk_words

ROOT = Path(__file__).resolve().parents[2]
CASE = next(
    c
    for c in json.loads((ROOT / "definitions/seed/bol_cases.json").read_text(encoding="utf-8"))["cases"]
    if c["scan"]
)
RECORDED = json.loads((ROOT / "tests/fixtures/landingai/bol_10_scan.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("gen_bols", ROOT / "scripts/gen_bols.py")
assert _spec and _spec.loader
gen_bols = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_bols)
SCAN = gen_bols.scan(gen_bols.render(CASE), CASE["smudge"])


def gateway(scan: Any = None, text: Any = None) -> list[dict[str, Any]]:
    """A fake LiteLLM: `scan` answers nova-extract-scan (dict = content, int = HTTP status, str = error
    body), `text` answers the chat aliases. Returns the request log."""
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        answer = scan if body["model"] == "nova-extract-scan" else text
        if isinstance(answer, int):
            return httpx.Response(answer, text="provider error")
        if isinstance(answer, str):
            return httpx.Response(422, text=answer)  # the code LiteLLM answers a spent budget with today
        content = json.dumps(answer)
        out = {"model": body["model"], "choices": [{"message": {"content": content}}], "usage": {}}
        return httpx.Response(200, json=out, headers={"x-litellm-response-cost": "0.043"})

    llm.use(llm.LLM(transport=httpx.MockTransport(handler)))
    return seen


@pytest.fixture(autouse=True)
def _reset() -> Any:
    yield
    llm.use(None)


async def test_scan_goes_through_dpt2_with_real_boxes_and_cost() -> None:
    seen = gateway(scan=RECORDED)
    calls: list[Any] = []
    out = await extract(SCAN, "bol_v1", {"tenant_id": "t"}, calls.append)
    assert [b["model"] for b in seen] == ["nova-extract-scan"]
    # the PDF and our schema (minus x-nova hints) went to the gateway, nothing provider-specific
    sys_msg, user = seen[0]["messages"]
    assert "x-nova-prompt" not in sys_msg["content"] and "bol_number" in sys_msg["content"]
    assert user["content"][0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert out["mode"] == "dpt2" and out["fields"]["bol_number"] == CASE["fields"]["bol_number"]
    assert out["fields"]["hs_codes"] == ["854143"]  # deduped
    # the stained container isn't guessed: it's missing, which the booking check turns into a review
    assert out["fields"]["container_numbers"] == CASE["fields"]["container_numbers"][1:]
    ev = {e["field"]: e for e in out["evidence"]}
    box = ev["bol_number"]["bbox"]
    assert ev["bol_number"]["page"] == 1 and 0 <= box[0] < box[2] <= 700 and 0 <= box[1] < box[3] <= 900
    assert out["confidence"]["bol_number"] == 1.0 and calls[0].cost_usd == 0.043


async def test_low_confidence_span_pulls_the_field_down() -> None:
    rec = copy.deepcopy(RECORDED)
    chunk = next(c for c in rec["chunks"] if "CMDU2609170" in c["markdown"])
    spans = [{"text": "CMDU2609170", "span": [0, 11], "confidence": 0.41}]
    rec["grounding"][chunk["id"]]["low_confidence_spans"] = spans
    gateway(scan=rec)
    out = await extract(SCAN, "bol_v1", {})
    assert out["confidence"]["bol_number"] == 0.41 and out["min_confidence"] <= 0.41


@pytest.mark.parametrize("failure", [400, 500])
async def test_scan_alias_failure_falls_back_to_vision(failure: int) -> None:
    seen = gateway(scan=failure, text=CASE["fields"])
    out = await extract(SCAN, "bol_v1", {})
    assert [b["model"] for b in seen] == ["nova-extract-scan", "nova-extract-vision"]
    assert out["mode"] == "vision" and "DPT-2 unavailable" in out["note"]
    assert out["min_confidence"] <= 0.6  # vision values have no box to check against


async def test_budget_exhausted_on_scan_is_not_hidden_by_the_fallback() -> None:
    gateway(scan="ExceededBudget: Budget has been exceeded! Current cost: 2.1, Max budget: 2.0")
    with pytest.raises(BudgetExceeded):
        await extract(SCAN, "bol_v1", {})


async def test_extraction_off_schema_is_redone_by_the_text_model_over_dpt2_text() -> None:
    rec = copy.deepcopy(RECORDED)
    rec["extraction"]["freight_terms"] = "maybe"  # not in the schema's enum
    seen = gateway(scan=rec, text=CASE["fields"])
    out = await extract(SCAN, "bol_v1", {})
    assert [b["model"] for b in seen] == ["nova-extract-scan", "nova-extract-text"]
    assert "CMDU2609170" in json.dumps(seen[1]["messages"])  # the DPT-2 text, not an image
    assert out["mode"] == "dpt2" and out["fields"]["freight_terms"] == CASE["fields"]["freight_terms"]


async def test_text_layer_pdf_never_calls_dpt2() -> None:
    clean_case = next(
        c
        for c in json.loads((ROOT / "definitions/seed/bol_cases.json").read_text())["cases"]
        if not c["scan"]
    )
    seen = gateway(scan=RECORDED, text=clean_case["fields"])
    out = await extract(gen_bols.render(clean_case), "bol_v1", {})
    assert [b["model"] for b in seen] == ["nova-extract-text"] and out["mode"] == "text"


def test_ade_schema_strips_our_hints() -> None:
    s = ade_schema({"$id": "x", "x-nova-prompt": "p", "properties": {"a": {"type": "string", "x-y": 1}}})
    assert s == {"properties": {"a": {"type": "string"}}}


def test_chunk_words_maps_normalised_boxes_to_points() -> None:
    chunk = {
        "id": "c1",
        "markdown": "<a id='c1'></a>\n\nB/L NO.\nCMDU2609170",
        "grounding": {"page": 0, "box": {"left": 0.1, "top": 0.2, "right": 0.5, "bottom": 0.3}},
    }
    ws = chunk_words([chunk], {}, {1: (600.0, 800.0)})
    assert [w.text for w in ws] == ["B/L", "NO.", "CMDU2609170"]
    assert ws[2] == Word("CMDU2609170", 60.0, 200.0, 300.0, 240.0, 1, 1.0)  # second line, full width
    assert chunk_words([chunk], {}, {2: (600.0, 800.0)}) == []  # a page that isn't a scan
