"""W2 invoice_match.yaml (Acme and Bolt) on the time-skipping server: the real definitions, fake agents.
Proves the paths docs/04 W2 specifies: auto-pay, duplicate → rejected with no matcher call, L1 → L2 →
dispute (the email action gets the cited clauses and the reviewer's reason), and Bolt's L3 controller
sign-off as a subflow, approved and rejected."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.service import RPCError
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
    Subrun,
    SubrunRequest,
    TaskWrite,
)
from nova_engine.interpreter import NovaWorkflow

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> dict[str, Any]:
    wf, issues = parse_workflow((ROOT / path).read_text(encoding="utf-8"))
    assert wf and not issues, issues
    return wf.model_dump(mode="json", by_alias=True)


DEFS = {
    "acme": load("definitions/workflows/acme/invoice_match.yaml"),
    "bolt": load("definitions/workflows/bolt/invoice_match.yaml"),
    "signoff": load("definitions/workflows/bolt/controller_signoff.yaml"),
}
CONFIG = {
    "acme": {
        "invoice": {
            "auto_variance_pct": 2,
            "auto_limit_usd": 2000,
            "l2_limit_usd": 10000,
            "l2_variance_pct": 5,
        }
    },
    "bolt": {
        "invoice": {
            "auto_variance_pct": 1,
            "auto_limit_usd": 1000,
            "l2_limit_usd": 5000,
            "l2_variance_pct": 3,
        }
    },
}
CONFIG["signoff"] = CONFIG["bolt"]
CLAUSE = {
    "clause_id": "MAEU-RC-2026 §3.1",
    "title": "Ocean freight",
    "text": "USD 1,850 per 40' container.",
    "rate": 1850,
}
OVER = {
    "code": "RATE_OVER_CONTRACT",
    "field": "lines[0].amount",
    "severity": "high",
    "message": "OFR +7%",
    "clause_id": CLAUSE["clause_id"],
}

# document → (duplicate?, total_usd, variance_pct, issues)
DOCS: dict[str, tuple[bool, float, float, list[dict[str, Any]]]] = {
    "small": (False, 555.0, 0.0, []),
    "dup": (True, 555.0, 0.0, []),
    "over": (False, 6735.0, 6.15, [OVER]),
    "big": (False, 12615.0, 0.0, []),
}


class Fake:
    def __init__(self) -> None:
        self.actions: list[ActionRequest] = []
        self.tasks: list[TaskWrite] = []
        self.agents_called: list[str] = []
        self.children: list[str] = []  # workflow ids of controller_signoff subruns

    def acts(self) -> list[Any]:
        @activity.defn(name="load_definition")
        async def load_definition(r: LoadRequest) -> Loaded:
            return Loaded(DEFS[r.definition_id], CONFIG[r.definition_id])

        @activity.defn(name="project_step")
        async def project_step(e: StepEvent) -> None:
            pass

        @activity.defn(name="write_task")
        async def write_task(w: TaskWrite) -> None:
            self.tasks.append(w)

        @activity.defn(name="run_action")
        async def run_action(r: ActionRequest) -> Any:
            self.actions.append(r)
            return {"ok": True}

        @activity.defn(name="start_subrun")
        async def start_subrun(r: SubrunRequest) -> Subrun:
            assert r.key == "controller_signoff"
            child = str(uuid.uuid4())
            self.children.append(f"run-{child}")
            return Subrun(child, "signoff", r.input)

        return [load_definition, project_step, write_task, run_action, start_subrun]

    def agents(self) -> list[Any]:
        @activity.defn(name="run_agent")
        async def run_agent(r: AgentRequest) -> Any:
            self.agents_called.append(r.name)
            dup, total, variance, issues = DOCS[r.params["document_id"]]
            if r.name == "doc_extractor":
                return {
                    "fields": {"invoice_no": "INV-1", "carrier_scac": "MAEU", "total": total},
                    "evidence": [],
                }
            if r.name == "invoice_dedupe":
                return {"duplicate": dup}
            assert r.name == "invoice_matcher"
            return {
                "matches": [],
                "total_usd": total,
                "variance_pct": variance,
                "issues": issues,
                "max_severity": issues[0]["severity"] if issues else None,
                "cited_clauses": [CLAUSE] if issues else [],
                "accessorials": [],
            }

        @activity.defn(name="decide")
        async def decide(r: AgentRequest) -> Any:
            assert r.params["questions"][0]["context"] == []  # no accessorials in these cases
            return {"unjustified": False, "why": {}}

        return [run_agent, decide]


@pytest.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def w2(env: WorkflowEnvironment) -> AsyncIterator[Any]:
    fake = Fake()
    q = f"q-{uuid.uuid4()}"
    async with (
        Worker(env.client, task_queue=q, workflows=[NovaWorkflow], activities=fake.acts()),
        Worker(env.client, task_queue=AGENTS_QUEUE, activities=fake.agents()),
    ):

        async def start(tenant: str, doc: str) -> WorkflowHandle[Any, RunResult]:
            req = RunRequest(str(uuid.uuid4()), str(uuid.uuid4()), tenant, 1, {"document_id": doc})
            return await env.client.start_workflow(
                NovaWorkflow.run, req, id=f"run-{req.run_id}", task_queue=q, result_type=RunResult
            )

        start.fake = fake  # type: ignore[attr-defined]
        start.env = env  # type: ignore[attr-defined]
        yield start


async def decide_next(
    h: WorkflowHandle[Any, Any], seen: set[str], decision: str, payload: dict[str, Any] | None = None
) -> str:
    for _ in range(300):
        try:
            tasks = set((await h.query(NovaWorkflow.state))["tasks"]) - seen
        except RPCError:  # a child that's about to start isn't queryable yet
            tasks = set()
        if tasks:
            tid = str(next(iter(tasks)))
            seen.add(tid)
            await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
            await h.execute_update(COMPLETE_TASK, args=[tid, "u1", decision, payload or {}])
            return tid
        await asyncio.sleep(0.05)
    raise AssertionError("no task opened")


async def child(w2: Any) -> WorkflowHandle[Any, Any]:
    for _ in range(300):
        if w2.fake.children:
            return w2.env.client.get_workflow_handle(w2.fake.children[-1])
        await asyncio.sleep(0.05)
    raise AssertionError("no subflow started")


async def test_small_clean_invoice_is_paid_without_a_human(w2: Any) -> None:
    res = await (await w2("acme", "small")).result()
    assert res.status == "completed" and w2.fake.tasks == []
    [pay] = w2.fake.actions
    assert pay.action == "erp.post_payable" and pay.params["amount_usd"] == 555.0


async def test_duplicate_is_rejected_before_the_matcher(w2: Any) -> None:
    res = await (await w2("acme", "dup")).result()
    assert res.status == "rejected" and w2.fake.actions == []
    assert w2.fake.agents_called == [
        "doc_extractor",
        "invoice_dedupe",
    ]  # no matcher, no model after extraction


async def test_over_contract_goes_l1_then_l2_and_dispute_cites_the_clause(w2: Any) -> None:
    h = await w2("acme", "over")
    seen: set[str] = set()
    await decide_next(h, seen, "approved")
    l1 = w2.fake.tasks[0]
    assert l1.node_id == "l1_l2" and l1.assignee_role == "ops_lead" and l1.payload["amount"] == 6735.0
    await decide_next(h, seen, "dispute", {"reason": "OFR billed above contract"})
    l2 = next(t for t in w2.fake.tasks if t.node_id == "l2" and t.title)
    assert l2.assignee_role == "finance" and l2.app_key == "invoice_review"
    assert (await h.result()).status == "rejected"
    [email] = w2.fake.actions
    assert email.action == "carrier.dispute_email"
    assert email.params["clauses"] == [CLAUSE] and email.params["issues"] == [OVER]
    assert email.params["reason"] == "OFR billed above contract"


async def test_bolt_needs_a_controller_above_its_l2_limit(w2: Any) -> None:
    h = await w2("bolt", "big")
    seen: set[str] = set()
    await decide_next(h, seen, "approved")  # L1
    await decide_next(h, seen, "approved")  # L2
    await decide_next(await child(w2), seen, "approved")  # L3, inside the controller_signoff child
    assert (await h.result()).status == "completed"
    assert [a.action for a in w2.fake.actions] == ["erp.post_payable"]
    l3 = next(t for t in w2.fake.tasks if t.node_id == "signoff" and t.title)
    assert l3.assignee_role == "controller" and l3.payload["amount"] == 12615.0


async def test_bolt_controller_rejection_never_pays(w2: Any) -> None:
    h = await w2("bolt", "big")
    seen: set[str] = set()
    await decide_next(h, seen, "approved")
    await decide_next(h, seen, "approved")
    await decide_next(await child(w2), seen, "rejected", {"reason": "hold"})
    assert (await h.result()).status == "rejected" and w2.fake.actions == []


async def test_same_invoice_one_level_for_acme(w2: Any) -> None:
    """12,615 is above both L2 limits, but only Bolt adds the third level (FR-X.2)."""
    h = await w2("acme", "big")
    seen: set[str] = set()
    await decide_next(h, seen, "approved")
    await decide_next(h, seen, "approved")
    assert (await h.result()).status == "completed"
    assert not any(t.node_id == "signoff" for t in w2.fake.tasks)
