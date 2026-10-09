"""W1 bol_intake.yaml on the time-skipping server: the real definition, fake agents. Proves the routing
the acceptance criteria depend on (docs/04 W1): clean → touchless push; any material issue or low
confidence → review; reviewer edits flow into the TMS push; rejection never pushes."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nova_core.temporal import AGENTS_QUEUE, CLAIM_TASK, COMPLETE_TASK
from nova_dsl import parse_workflow
from nova_engine.contracts import (
    ActionRequest,
    AgentRequest,
    Loaded,
    LoadRequest,
    RunRequest,
    RunResult,
    StepEvent,
    TaskWrite,
)
from nova_engine.interpreter import NovaWorkflow

ROOT = Path(__file__).resolve().parents[2]
WF, ISSUES = parse_workflow((ROOT / "definitions/workflows/acme/bol_intake.yaml").read_text(encoding="utf-8"))
assert WF and not ISSUES, ISSUES
DEF = WF.model_dump(mode="json", by_alias=True)
CONFIG = {"currency": "USD", "approval_limits": {}, "bol_min_confidence": 0.85}
FIELDS = {"bol_number": "MAEU2609017", "container_numbers": ["MSCU1721374"], "pol": "CNSHA", "pod": "USLGB"}

# per document: (min_confidence, issues, material?)
DOCS: dict[str, tuple[float, list[dict[str, Any]], bool]] = {
    "clean": (0.97, [], False),
    "weight": (0.95, [{"code": "WEIGHT_SUM", "field": "gross_weight_kg", "severity": "medium"}], True),
    "scan": (0.6, [], False),
    # the model says "not material" (fooled or injected): a medium issue still reaches a human
    "fooled": (0.97, [{"code": "WEIGHT_SUM", "field": "gross_weight_kg", "severity": "medium"}], False),
    # only a cosmetic issue and the model says it's fine: touchless
    "cosmetic": (0.97, [{"code": "HS_FORMAT", "field": "hs_codes[0]", "severity": "low"}], False),
}


class Fake:
    def __init__(self) -> None:
        self.actions: list[ActionRequest] = []
        self.tasks: list[TaskWrite] = []
        self.decide_contexts: list[Any] = []
        self.material = False  # what the (fake) decide model answers

    def acts(self) -> list[Any]:
        @activity.defn(name="load_definition")
        async def load_definition(r: LoadRequest) -> Loaded:
            return Loaded(DEF, CONFIG)

        @activity.defn(name="project_step")
        async def project_step(e: StepEvent) -> None:
            pass

        @activity.defn(name="write_task")
        async def write_task(w: TaskWrite) -> None:
            self.tasks.append(w)

        @activity.defn(name="run_action")
        async def run_action(r: ActionRequest) -> Any:
            self.actions.append(r)
            return {"shipment_id": "s1"}

        return [load_definition, project_step, write_task, run_action]

    def agents(self) -> list[Any]:
        @activity.defn(name="run_agent")
        async def run_agent(r: AgentRequest) -> Any:
            if r.name == "doc_extractor":
                conf, _, _ = DOCS[r.params["document_id"]]
                return {
                    "fields": FIELDS,
                    "confidence": {"bol_number": conf},
                    "min_confidence": conf,
                    "evidence": [],
                }
            assert r.name == "bol_validator" and r.params["extraction"]["fields"] == FIELDS
            issues = DOCS[r.params["document_id"]][1]
            return {
                "issues": issues,
                "checked": [],
                "max_severity": issues[0]["severity"] if issues else None,
            }

        @activity.defn(name="decide")
        async def decide(r: AgentRequest) -> Any:
            q = r.params["questions"][0]
            self.decide_contexts.append(q["context"])
            assert q["criteria"]["true"] and q["threshold"] == 0.4  # definition-level, passed through
            return {"material_issue": self.material, "why": {"material_issue": "test"}}

        return [run_agent, decide]


@pytest.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def w1(env: WorkflowEnvironment) -> AsyncIterator[Any]:
    fake = Fake()
    q = f"q-{uuid.uuid4()}"
    async with (
        Worker(env.client, task_queue=q, workflows=[NovaWorkflow], activities=fake.acts()),
        Worker(env.client, task_queue=AGENTS_QUEUE, activities=fake.agents()),
    ):

        async def start(doc: str) -> WorkflowHandle[Any, RunResult]:
            req = RunRequest(str(uuid.uuid4()), str(uuid.uuid4()), "w1", 1, {"document_id": doc})
            return await env.client.start_workflow(
                NovaWorkflow.run, req, id=f"run-{req.run_id}", task_queue=q, result_type=RunResult
            )

        start.fake = fake  # type: ignore[attr-defined]
        yield start


async def _task(h: WorkflowHandle[Any, Any]) -> str:
    for _ in range(200):
        if tasks := (await h.query(NovaWorkflow.state))["tasks"]:
            return str(next(iter(tasks)))
        await asyncio.sleep(0.05)
    raise AssertionError("no review task opened")


async def test_clean_bol_is_touchless(w1: Any) -> None:
    res = await (await w1("clean")).result()
    assert res.status == "completed"
    [push] = w1.fake.actions
    assert push.action == "tms.upsert_shipment" and push.params == {"fields": FIELDS}
    assert w1.fake.tasks == [] and w1.fake.decide_contexts == [[]]


async def test_material_issue_goes_to_review_and_edits_reach_the_tms(w1: Any) -> None:
    h = await w1("weight")
    tid = await _task(h)
    [opened] = w1.fake.tasks
    assert opened.app_key == "bol_review" and opened.assignee_role == "ops_exec"
    assert opened.title == "Review BoL MAEU2609017"
    assert opened.payload["issues"][0]["code"] == "WEIGHT_SUM" and opened.payload["document_id"] == "weight"
    fixed = {**FIELDS, "gross_weight_kg": 9600.0}
    await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
    await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "approved", {"fields": fixed}])
    assert (await h.result()).status == "completed"
    assert w1.fake.actions[0].params == {"fields": fixed}


async def test_low_confidence_scan_goes_to_review_and_reject_never_pushes(w1: Any) -> None:
    h = await w1("scan")
    tid = await _task(h)
    await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
    await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "rejected", {"reason": "unreadable"}])
    assert (await h.result()).status == "rejected"
    assert w1.fake.actions == []


async def test_a_fooled_model_cannot_wave_a_real_discrepancy_through(w1: Any) -> None:
    w1.fake.material = False  # e.g. prompt-injected "nothing material here"
    h = await w1("fooled")
    await _task(h)  # still a review task: medium severity routes deterministically
    assert w1.fake.actions == []
    await h.cancel()


async def test_model_only_judges_the_residue(w1: Any) -> None:
    w1.fake.material = False
    res = await (await w1("cosmetic")).result()
    assert res.status == "completed" and len(w1.fake.actions) == 1  # low-only issue: the model's call
