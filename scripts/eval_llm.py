"""LLM eval + benchmark for W1 against the running gateway (live calls, ~$0.01 a run in `cheap`).

- extraction: per-field F1 against definitions/seed/bol_cases.json ground truth (labels = the seed)
- validator: recall of the planted check codes on the model's own extraction, false alarms on clean docs
- decide: accuracy of Jev (or the chat fallback) on tests/evals/decide_bol.jsonl
- timings p50/p95 per stage, cache bypassed so they're real

Run: LLM_BASE_URL=http://localhost:4100 JEV_MODEL=typesafe/jev-1.13 uv run python scripts/eval_llm.py
Writes docs/evals/m2-baseline.json (cheap) or docs/evals/<mode>-baseline.json.
"""

import asyncio
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from nova_agents import checks
from nova_agents.decide import decide
from nova_agents.extractor import extract
from nova_agents.llm import LLMError
from nova_core.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "definitions/seed/bol_cases.json").read_text(encoding="utf-8"))["cases"]
_spec = importlib.util.spec_from_file_location("gen_bols", ROOT / "scripts/gen_bols.py")
assert _spec and _spec.loader
gen_bols = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_bols)
FIELDS = [k for k in CASES[0]["fields"] if k != "cargo_lines"]


def norm(v: Any) -> Any:
    if v is None or v == "" or v == []:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return round(float(v), 1)
    if isinstance(v, list):
        return tuple(sorted({norm(x) for x in v}))  # a code repeated per cargo line is one code
    return re.sub(r"[^A-Z0-9]+", " ", str(v).upper()).strip()


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else 0.0


async def main() -> None:
    s = get_settings()
    print(f"gateway {s.llm_base_url} · mode {s.llm_mode} · jev {s.jev_model or 'off'}")
    tp = dict.fromkeys(FIELDS, 0)
    fp = dict.fromkeys(FIELDS, 0)
    fn = dict.fromkeys(FIELDS, 0)
    ext_ms: list[float] = []
    cost = 0.0
    planted = found = false_alarms = 0
    per_doc = []
    for case in CASES:
        data = gen_bols.render(case)
        if case["scan"]:
            data = gen_bols.scan(data)
        calls: list[Any] = []
        t0 = time.perf_counter()
        for attempt in (1, 2, 3):  # like the activity's retry policy: a cut-off answer is retryable
            try:
                out = await extract(data, "bol_v1", {"eval": "m2"}, calls.append, fresh=True)
                break
            except LLMError:
                if attempt == 3:
                    raise
        ext_ms.append((time.perf_counter() - t0) * 1000)
        cost += sum(c.cost_usd for c in calls)
        wrong = []
        for k in FIELDS:
            truth, pred = norm(case["fields"][k]), norm(out["fields"].get(k))
            if truth == pred and truth is not None:
                tp[k] += 1
            else:
                if truth is not None:
                    fn[k] += 1
                if pred is not None and truth != pred:
                    fp[k] += 1
                if truth != pred:
                    wrong.append(k)
        issues = {
            i.code
            for i in checks.run(list(checks.CHECKS), out["fields"], checks.Ctx(booking=case["booking"]))
        }
        want = set(case["expected_issues"])
        planted += len(want)
        found += len(want & issues)
        false_alarms += len(issues - want)
        per_doc.append(
            {
                "file": case["file"],
                "mode": out["mode"],
                "min_conf": out["min_confidence"],
                "wrong_fields": wrong,
                "issues": sorted(issues),
                "expected": sorted(want),
                "ms": round(ext_ms[-1]),
            }
        )
        print(
            f"  {case['file']:11} {out['mode']:6} {ext_ms[-1] / 1000:5.1f}s conf {out['min_confidence']:.2f} "
            f"wrong={wrong or '-'} issues={sorted(issues) or '-'}"
        )

    f1 = {}
    for k in FIELDS:
        p = tp[k] / (tp[k] + fp[k]) if tp[k] + fp[k] else 1.0
        r = tp[k] / (tp[k] + fn[k]) if tp[k] + fn[k] else 1.0
        f1[k] = round(2 * p * r / (p + r), 3) if p + r else 0.0
    micro_tp, micro_fp, micro_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    micro = round(2 * micro_tp / (2 * micro_tp + micro_fp + micro_fn), 3)

    rows = [
        json.loads(line)
        for line in (ROOT / "tests/evals/decide_bol.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    wf = yaml.safe_load((ROOT / "definitions/workflows/acme/bol_intake.yaml").read_text(encoding="utf-8"))
    q = next(n for n in wf["nodes"] if n["type"] == "decide")["questions"][0]  # evaluate what ships
    correct, dec_ms, dec_cost, misses = 0, [], 0.0, []
    for row in rows:
        t0 = time.perf_counter()
        out = await decide([{**q, "context": row["issues"]}], {"eval": "m2"})
        dec_ms.append((time.perf_counter() - t0) * 1000)
        dec_cost += out["meta"]["cost_usd"]
        ok = out["material_issue"] == row["label"]
        correct += ok
        if not ok:
            misses.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "p": out["probability"].get("material_issue"),
                    "codes": [i["code"] for i in row["issues"]],
                }
            )

    report = {
        "mode": s.llm_mode,
        "extraction": {
            "docs": len(CASES),
            "field_f1": f1,
            "micro_f1": micro,
            "p50_s": pct(ext_ms, 0.5) / 1000,
            "p95_s": pct(ext_ms, 0.95) / 1000,
            "cost_usd": round(cost, 5),
            "per_doc": per_doc,
        },
        "validator": {
            "planted": planted,
            "found": found,
            "recall": round(found / planted, 3) if planted else 1.0,
            "false_alarms": false_alarms,
        },
        "decide": {
            "n": len(rows),
            "accuracy": round(correct / len(rows), 3),
            "p50_s": round(pct(dec_ms, 0.5) / 1000, 2),
            "p95_s": round(pct(dec_ms, 0.95) / 1000, 2),
            "cost_usd": round(dec_cost, 5),
            "misses": misses,
        },
    }
    name = "m2" if s.llm_mode == "cheap" else s.llm_mode  # one baseline per mode
    out_path = ROOT / f"docs/evals/{name}-baseline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                k: {kk: vv for kk, vv in v.items() if kk not in {"per_doc", "field_f1"}}
                if isinstance(v, dict)
                else v
                for k, v in report.items()
            },
            indent=1,
        )
    )
    print("field F1:", {k: v for k, v in f1.items() if v < 1.0} or "all 1.0")
    # regression gate (ai-eval.yml): a planted error must never slip through
    gates = {
        "validator recall == 1.0": report["validator"]["recall"] == 1.0,
        "extraction micro F1 >= 0.95": report["extraction"]["micro_f1"] >= 0.95,
        "decide accuracy >= 0.85": report["decide"]["accuracy"] >= 0.85,
    }
    for name, ok in gates.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    sys.exit(0 if all(gates.values()) else 1)


if __name__ == "__main__":
    asyncio.run(main())
