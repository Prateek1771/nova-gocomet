"""NovaWorkflow on Temporal's time-skipping test server; every activity is an in-memory fake."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nova_core.temporal import AGENTS_QUEUE, CLAIM_TASK, COMPLETE_TASK, EVENT
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
CONFIG = {"currency": "USD", "auto_approve_below": 1000, "approval_limits": {"ops_lead": 10000}}


def load(path: str) -> dict[str, Any]:
    wf, issues = parse_workflow((ROOT / path).read_text(encoding="utf-8"))
    assert wf and not issues, issues
    return wf.model_dump(mode="json", by_alias=True)


def inline(text: str) -> dict[str, Any]:
    wf, issues = parse_workflow(text)
    assert wf and not issues, issues
    return wf.model_dump(mode="json", by_alias=True)


DEFS = {
    "demo": load("definitions/workflows/acme/demo_approval.yaml"),
    "every": load("tests/fixtures/workflows/valid/every_node.yaml"),
}


class Fake:
    """In-memory stand-in for the DB-backed activities, plus the agents-worker contract."""

    def __init__(self) -> None:
        self.steps: list[StepEvent] = []
        self.tasks: list[TaskWrite] = []
        self.actions: list[ActionRequest] = []
        self.fail_actions = 0  # next N run_action calls fail (non-retryable)
        self.slow_actions = False
        self.decide_answer = {"blocking": False}
        self.budget_left = True  # False: run_agent fails like the agents worker on an exhausted budget

    def acts(self) -> list[Any]:
        @activity.defn(name="load_definition")
        async def load_definition(r: LoadRequest) -> Loaded:
            return Loaded(DEFS[r.definition_id], CONFIG)

        @activity.defn(name="project_step")
        async def project_step(e: StepEvent) -> None:
            self.steps.append(e)

        @activity.defn(name="write_task")
        async def write_task(w: TaskWrite) -> None:
            self.tasks.append(w)

        @activity.defn(name="run_action")
        async def run_action(r: ActionRequest) -> Any:
            self.actions.append(r)
            while self.slow_actions:  # noqa: ASYNC110 (the test flips a plain flag)
                await asyncio.sleep(0.05)
            if self.fail_actions:
                self.fail_actions -= 1
                raise ApplicationError("tms down", non_retryable=True)
            return {"ok": True, **r.params}

        @activity.defn(name="start_subrun")
        async def start_subrun(r: SubrunRequest) -> Subrun:
            return Subrun(str(uuid.uuid4()), "demo", r.input)

        return [load_definition, project_step, write_task, run_action, start_subrun]

    def agent_acts(self) -> list[Any]:
        @activity.defn(name="run_agent")
        async def run_agent(r: AgentRequest) -> Any:
            if not self.budget_left:
                raise ApplicationError("LLM budget exhausted", type="BudgetExceeded", non_retryable=True)
            return {"agent": r.name, "fields": {"total": 1}}

        @activity.defn(name="decide")
        async def decide(r: AgentRequest) -> Any:
            assert r.params["questions"][0]["context"] == {"agent": "doc_extractor", "fields": {"total": 1}}
            return self.decide_answer

        return [run_agent, decide]

    def run_statuses(self) -> list[str]:
        return [e.run_status for e in self.steps if e.run_status]


@pytest.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def run(env: WorkflowEnvironment) -> AsyncIterator[Any]:
    fake = Fake()
    q = f"q-{uuid.uuid4()}"
    async with (
        Worker(env.client, task_queue=q, workflows=[NovaWorkflow], activities=fake.acts()),
        Worker(env.client, task_queue=AGENTS_QUEUE, activities=fake.agent_acts()),
    ):

        async def start(def_id: str, inp: dict[str, Any], **kw: Any) -> WorkflowHandle[Any, RunResult]:
            DEFS.update(kw.pop("defs", {}))
            req = RunRequest(str(uuid.uuid4()), str(uuid.uuid4()), def_id, 1, inp, **kw)
            return await env.client.start_workflow(
                NovaWorkflow.run, req, id=f"run-{req.run_id}", task_queue=q, result_type=RunResult
            )

        start.fake = fake  # type: ignore[attr-defined]
        yield start


async def open_task(h: WorkflowHandle[Any, Any]) -> str:
    for _ in range(200):
        tasks = (await h.query(NovaWorkflow.state))["tasks"]
        if tasks:
            return str(next(iter(tasks)))
        await asyncio.sleep(0.05)
    raise AssertionError("no task opened")


async def decide_task(h: WorkflowHandle[Any, Any], decision: str, user: str = "u1") -> None:
    tid = await open_task(h)
    await h.execute_update(CLAIM_TASK, args=[tid, user])
    await h.execute_update(COMPLETE_TASK, args=[tid, user, decision, {"note": "ok"}])


async def test_rule_auto_path(run: Any) -> None:
    res = await (await run("demo", {"amount": 500})).result()
    assert res.status == "completed"
    assert run.fake.actions[0].params == {"message": "approved 500", "by": "auto"}
    assert run.fake.run_statuses() == ["running", "completed"]


async def test_human_approve_resumes(run: Any) -> None:
    h = await run("demo", {"amount": 5000, "note": "rush"})
    tid = await open_task(h)
    created = run.fake.tasks[0]
    assert created.title == "Approve 5000 — rush" and created.assignee_role == "ops_lead"
    assert created.payload == {"amount": 5000} and created.due_at
    await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
    await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "approved", {}])
    res = await h.result()
    assert res.status == "completed" and res.nodes["review"]["output"]["by"] == "u1"
    assert run.fake.actions[0].params["by"] == "u1"
    assert run.fake.run_statuses() == ["running", "waiting_human", "running", "completed"]
    assert [t.status for t in run.fake.tasks] == ["open", "claimed", "done"]


async def test_human_reject_ends_rejected(run: Any) -> None:
    h = await run("demo", {"amount": 5000})
    await decide_task(h, "rejected")
    assert (await h.result()).status == "rejected"
    assert run.fake.actions == []


async def test_updates_are_validated(run: Any) -> None:
    h = await run("demo", {"amount": 5000})
    tid = await open_task(h)
    with pytest.raises(WorkflowUpdateFailedError):  # complete before claim
        await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "approved", {}])
    await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
    await h.execute_update(CLAIM_TASK, args=[tid, "u1"])  # re-claim by the holder is fine
    with pytest.raises(WorkflowUpdateFailedError):
        await h.execute_update(CLAIM_TASK, args=[tid, "u2"])
    with pytest.raises(WorkflowUpdateFailedError):
        await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "maybe", {}])
    with pytest.raises(WorkflowUpdateFailedError):
        await h.execute_update(CLAIM_TASK, args=["ghost", "u1"])
    with pytest.raises(WorkflowUpdateFailedError):
        await h.execute_update(COMPLETE_TASK, args=["ghost", "u1", "approved", {}])
    await h.execute_update(COMPLETE_TASK, args=[tid, "u1", "approved", {}])
    assert (await h.result()).status == "completed"


async def test_sla_escalates(run: Any, env: WorkflowEnvironment) -> None:
    h = await run("demo", {"amount": 5000})
    tid = await open_task(h)
    await env.sleep(timedelta(minutes=3))
    for _ in range(100):
        if any(t.status == "escalated" for t in run.fake.tasks):
            break
        await asyncio.sleep(0.05)
    esc = next(t for t in run.fake.tasks if t.status == "escalated")
    assert esc.assignee_role == "finance"
    state = await h.query(NovaWorkflow.state)
    assert state["tasks"][tid]["status"] == "escalated"
    await decide_task(h, "approved", user="fin")
    assert (await h.result()).status == "completed"


async def test_cancel(run: Any) -> None:
    h = await run("demo", {"amount": 5000})
    await open_task(h)
    await h.cancel()
    with pytest.raises(WorkflowFailureError):
        await h.result()
    assert run.fake.run_statuses()[-1] == "cancelled"
    assert run.fake.tasks[-1].decision == "cancelled"
    assert run.fake.steps[-2].status == "skipped"


async def test_every_node_type(run: Any) -> None:
    res = await (await run("every", {"amount": 50, "document_id": "d1"})).result()
    assert res.status == "completed"
    assert res.nodes["fanout"]["output"] == {"completed": [0, 1]}
    assert res.nodes["child"]["output"]["status"] == "completed"  # child demo run: 50 < 1000 → auto
    assert res.nodes["route"]["output"] == {"goto": "done"}


async def test_decide_routes_to_review(run: Any, env: WorkflowEnvironment) -> None:
    run.fake.decide_answer = {"blocking": True}
    h = await run("every", {"amount": 50, "document_id": "d1"})
    await env.sleep(timedelta(hours=2))  # past the 1h `nap`; queries alone don't skip time
    await decide_task(h, "rejected")
    assert (await h.result()).status == "rejected"


ANY = """
metadata: {key: race}
nodes:
  - {id: p, type: parallel, join: any, branches: [[slow], [fast]]}
  - {id: slow, type: wait, duration: 10h}
  - {id: fast, type: action, action: noop}
  - {id: done, type: end}
edges: [{from: p, to: done}]
"""


async def test_parallel_any_cancels_the_rest(run: Any) -> None:
    res = await (await run("any", {}, defs={"any": inline(ANY)})).result()
    assert res.nodes["p"]["output"] == {"completed": [1]}
    assert any(e.node_id == "slow" and e.status == "skipped" for e in run.fake.steps)


EVENT_WF = """
metadata: {key: ev}
nodes:
  - {id: w, type: wait, event: docs_ready, duration: 1h}
  - {id: done, type: end}
edges: [{from: w, to: done}]
"""


async def test_wait_for_event(run: Any) -> None:
    h = await run("ev", {}, defs={"ev": inline(EVENT_WF)})
    await h.signal(EVENT, args=["docs_ready", {"n": 2}])
    assert (await h.result()).nodes["w"]["output"] == {"event": {"n": 2}}


async def test_wait_for_event_times_out(run: Any) -> None:
    res = await (await run("ev", {}, defs={"ev": inline(EVENT_WF)})).result()
    assert res.nodes["w"]["output"] == {"timed_out": True}


def chain(n: int) -> str:
    nodes = [{"id": f"a{i}", "type": "action", "action": "noop"} for i in range(n)] + [
        {"id": "done", "type": "end"}
    ]
    ids = [x["id"] for x in nodes]
    edges = [{"from": a, "to": b} for a, b in zip(ids, ids[1:], strict=False)]
    return yaml.safe_dump({"metadata": {"key": "long"}, "nodes": nodes, "edges": edges})


async def test_continue_as_new_carries_context(run: Any) -> None:
    h = await run("long", {}, defs={"long": inline(chain(12))}, max_events=20)
    res = await h.result()
    assert res.status == "completed" and len(res.nodes) == 13
    desc = await h.describe()
    assert desc.run_id != h.first_execution_run_id  # it really did continue-as-new
    keys = [a.idempotency_key.rsplit(":", 2)[1] for a in run.fake.actions]
    assert keys == [f"a{i}" for i in range(12)]  # every action exactly once across executions


async def test_failed_step_needs_attention_then_retry(run: Any) -> None:
    run.fake.fail_actions = 1
    h = await run("demo", {"amount": 10})
    await decide_task(h, "retry", user="admin")
    res = await h.result()
    assert res.status == "completed"
    assert "needs_attention" in run.fake.run_statuses()
    fix = next(t for t in run.fake.tasks if t.title)
    assert fix.app_key == "step_failure" and fix.assignee_role == "tenant_admin"
    assert len(run.fake.actions) == 2


async def test_failed_step_abort(run: Any) -> None:
    run.fake.fail_actions = 1
    h = await run("demo", {"amount": 10})
    await decide_task(h, "abort", user="admin")
    assert (await h.result()).status == "failed"
    assert run.fake.run_statuses()[-1] == "failed"


AGENT_ONLY = """
metadata: {key: agent_only}
nodes:
  - {id: extract, type: agent, agent: doc_extractor, retry: {max_attempts: 5}}
  - {id: done, type: end}
edges:
  - {from: extract, to: done}
"""


async def test_budget_exhausted_degrades_to_a_human_then_retries(run: Any) -> None:
    """07 M4 exit: a spent tenant budget becomes a needs_attention task saying so, with no wasted
    activity retries; after the budget is raised, Retry re-runs the step."""
    run.fake.budget_left = False
    h = await run("agent_only", {}, defs={"agent_only": inline(AGENT_ONLY)})
    await open_task(h)  # the shared wait-for-a-task helper (no wall-clock loop of our own)
    fix = next(t for t in run.fake.tasks if t.title)
    assert fix.app_key == "step_failure" and fix.title.startswith("LLM budget exhausted")
    assert fix.payload["error_type"] == "BudgetExceeded"
    assert "needs_attention" in run.fake.run_statuses()
    run.fake.budget_left = True  # an admin raised the budget
    await decide_task(h, "retry", user="admin")
    assert (await h.result()).status == "completed"


BAD_RULE = """
metadata: {key: bad}
nodes:
  - {id: r, type: rule, cases: [{when: "input.amount", goto: done}], default: done}
  - {id: done, type: end}
"""


async def test_runtime_cel_error_needs_attention(run: Any) -> None:
    h = await run("bad", {"amount": 1}, defs={"bad": inline(BAD_RULE)})
    await decide_task(h, "abort", user="admin")
    assert (await h.result()).status == "failed"
    failed = next(e for e in run.fake.steps if e.status == "failed" and e.step_id)
    assert "must be a bool" in failed.output["error"]


async def test_cancel_during_an_activity() -> None:
    # Own server: the cancelled activity stays pending server-side until it times out, and a pending
    # activity stops the shared time-skipping server from skipping time for the tests after it.
    fake = Fake()
    fake.slow_actions = True
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue="q", workflows=[NovaWorkflow], activities=fake.acts()),
    ):
        req = RunRequest(str(uuid.uuid4()), str(uuid.uuid4()), "demo", 1, {"amount": 10})
        h = await env.client.start_workflow(
            NovaWorkflow.run, req, id="c", task_queue="q", result_type=RunResult
        )
        for _ in range(100):
            if fake.actions:
                break
            await asyncio.sleep(0.05)
        await h.cancel()
        with pytest.raises(WorkflowFailureError):
            await h.result()
        fake.slow_actions = False
    assert fake.run_statuses()[-1] == "cancelled"
    assert not any(e.status == "failed" for e in fake.steps)
