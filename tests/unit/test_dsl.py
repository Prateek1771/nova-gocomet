"""Graph validator: golden fixtures (one invalid file per issue code) + property tests on random graphs."""

from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_dsl import Workflow, entry, next_node, parse_doc_type, parse_workflow, validate
from nova_dsl.schema import OUT, json_schema

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/fixtures/workflows"
VALID = sorted([*FIX.glob("valid/*.yaml"), *ROOT.glob("definitions/workflows/*/*.yaml")])
INVALID = sorted(FIX.glob("invalid/*.yaml"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid(path: Path) -> None:
    wf, issues = parse_workflow(path.read_text(encoding="utf-8"))
    assert wf is not None and issues == []


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_reports_its_code(path: Path) -> None:
    _, issues = parse_workflow(path.read_text(encoding="utf-8"))
    assert path.stem in {i.code for i in issues}, issues


def test_every_issue_code_has_a_fixture() -> None:
    src = (ROOT / "packages/nova_dsl/src/nova_dsl/graph.py").read_text(encoding="utf-8")
    import re

    codes = set(re.findall(r'Issue\("([a-z_]+)"', src)) | {"schema", "yaml"}
    assert codes - {p.stem for p in INVALID} <= {"branch_edges", "duplicate_question"}


def test_routing() -> None:
    wf, _ = parse_workflow(
        (ROOT / "definitions/workflows/acme/demo_approval.yaml").read_text(encoding="utf-8")
    )
    assert wf
    route = entry(wf)
    assert route.id == "route"
    assert next_node(wf, route, {"goto": "review"}).id == "review"
    review = wf.node("review")
    assert next_node(wf, review, {"decision": "rejected"}).id == "declined"
    assert next_node(wf, wf.node("notify"), {}).id == "done"
    assert review.type == "human_task" and review.sla == timedelta(minutes=2)


def test_round_trip_keeps_durations() -> None:
    wf, _ = parse_workflow((FIX / "valid/every_node.yaml").read_text(encoding="utf-8"))
    assert wf
    again = Workflow.model_validate(wf.model_dump(mode="json", by_alias=True))
    assert again == wf


def test_doc_types_load() -> None:
    for p in ROOT.glob("definitions/doc_types/*.yaml"):
        dt = parse_doc_type(p.read_text(encoding="utf-8"))
        assert dt.key == p.stem


def test_committed_schema_is_current() -> None:
    import json

    assert json.loads(OUT.read_text(encoding="utf-8")) == json_schema(), (
        "run: uv run python -m nova_dsl.schema"
    )


# --- property tests: random DAGs of actions ending in `end` are valid; breaking them is caught ---


@st.composite
def chains(draw: st.DrawFn) -> dict:  # type: ignore[type-arg]
    n = draw(st.integers(1, 12))
    ids = [f"n{i}" for i in range(n)]
    nodes: list[dict] = [{"id": i, "type": "action", "action": "noop"} for i in ids]  # type: ignore[type-arg]
    nodes.append({"id": "done", "type": "end"})
    # each node points to some later node (or done): a DAG with one entry when n0 reaches everything
    edges = []
    for k, i in enumerate(ids):
        later = [*ids[k + 1 :], "done"]
        edges.append(
            {"from": i, "to": later[0] if k == 0 or not later[:-1] else draw(st.sampled_from(later))}
        )
    # make every node reachable: chain n0→n1→… is guaranteed by pointing node k-1 at k when needed
    targets = {e["to"] for e in edges}
    for k in range(1, n):
        if ids[k] not in targets:
            edges[k - 1]["to"] = ids[k]
            targets = {e["to"] for e in edges}
    return {"metadata": {"key": "p"}, "nodes": nodes, "edges": edges}


@settings(max_examples=150, deadline=None)
@given(chains())
def test_random_dags_are_valid(data: dict) -> None:  # type: ignore[type-arg]
    assert validate(Workflow.model_validate(data)) == []


@settings(max_examples=150, deadline=None)
@given(chains(), st.data())
def test_dropping_an_edge_is_caught(data: dict, pick: st.DataObject) -> None:  # type: ignore[type-arg]
    victim = pick.draw(st.integers(0, len(data["edges"]) - 1))
    del data["edges"][victim]
    assert validate(Workflow.model_validate(data)) != []


@settings(max_examples=100, deadline=None)
@given(chains())
def test_removing_end_is_caught(data: dict) -> None:  # type: ignore[type-arg]
    data["nodes"] = [n for n in data["nodes"] if n["type"] != "end"]
    data["edges"] = [e for e in data["edges"] if e["to"] != "done"]
    assert validate(Workflow.model_validate(data)) != []
