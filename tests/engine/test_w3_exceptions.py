"""W3 on the time-skipping server: the real detect_exceptions and exception_triage YAMLs (Acme and Bolt) with
fake agents and actions. Proves the docs/04 W3 paths: not actionable → auto-close with no human and no
recommender; accept → notify (with the draft and the SOP-cited steps) → close; override → notify with the
ops lead's own words; dismiss → close without notifying; Bolt sends a high-severity exception to a human
even when decide says not actionable, with a 30 min SLA that escalates to the tenant admin. Plus the
detection run passes the tenant's rules, and ScheduledRun creates a run and returns without waiting."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
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
    ScheduledFire,
    StepEvent,
    Subrun,
    SubrunRequest,
    TaskWrite,
)
from nova_engine.interpreter import NovaWorkflow
from nova_engine.scheduled import ScheduledRun

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> dict[str, Any]:
    wf, issues = parse_workflow((ROOT / path).read_text(encoding="utf-8"))
    assert wf and not issues, issues
    return wf.model_dump(mode="json", by_alias=True)


DEFS = {f"{t}/{k}": load(f"definitions/workflows/{t}/{k}.yaml") for t in ("acme", "bolt")
        for k in ("exception_triage", "detect_exceptions")}  # fmt: skip
RULES = [{"type": "ETA_SLIP", "metric": "eta_slip_hours", "op": ">", "threshold": 24}]
CONFIG = {"exceptions": {"rules": RULES, "severity": {"ETA_SLIP": "high"}}}
RECS = [
    {
        "action": "Re-book on the next sailing",
        "why": "rolled",
        "sop_ref": "SOP-ROLL-01",
        "chunk_id": "SOP-ROLL-01#2",
    }
]
# exception id -> (decide actionable?, analyst severity)
CASES = {"noise": (False, "low"), "real": (True, "medium"), "bolt_high": (False, "high")}


class Fake:
    def __init__(self) -> None:
        self.actions: list[ActionRequest] = []
        self.tasks: list[TaskWrite] = []
        self.agents_called: list[str] = []
        self.subruns: list[SubrunRequest] = []

    def acts(self) -> list[Any]:
        @activity.defn(name="load_definition")
        async def load_definition(r: LoadRequest) -> Loaded:
            return Loaded(DEFS[r.definition_id], CONFIG)

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
            self.subruns.append(r)
            return Subrun(str(uuid.uuid4()), "acme/detect_exceptions", r.input, 7)

        return [load_definition, project_step, write_task, run_action, start_subrun]

    def agents(self) -> list[Any]:
        @activity.defn(name="run_agent")
        async def run_agent(r: AgentRequest) -> Any:
            self.agents_called.append(r.name)
            _, severity = CASES[r.params["exception_id"]]
            if r.name == "exception_analyst":
                return {
                    "exception_id": r.params["exception_id"],
                    "type": "ROLLOVER",
                    "severity": severity,
                    "summary": "Rolled to MSC OSCAR",
                    "slip_hours": 17.3,
                    "rule": {},
                    "shipment": {"container_no": "MSCU2858382"},
                    "facts": [{"metric": "rollover", "sql": "SELECT …", "rows": [{"rollovers": 1}]}],
                }
            assert r.name == "action_recommender" and r.params["summary"] == "Rolled to MSC OSCAR"
            return {"recommendations": RECS, "customer_message": "Your box sails on MSC OSCAR."}

        @activity.defn(name="decide")
        async def decide(r: AgentRequest) -> Any:
            actionable, _ = CASES[r.params["questions"][0]["context"]["exception_id"]]
            return {"actionable": actionable, "customer_impacting": actionable, "why": {"actionable": "x"}}

        return [run_agent, decide]


@pytest.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest.fixture
async def w3(env: WorkflowEnvironment) -> AsyncIterator[Any]:
    fake = Fake()
    q = f"q-{uuid.uuid4()}"
    async with (
        Worker(env.client, task_queue=q, workflows=[NovaWorkflow, ScheduledRun], activities=fake.acts()),
        Worker(env.client, task_queue=AGENTS_QUEUE, activities=fake.agents()),
    ):

        async def start(
            tenant: str, exception_id: str, key: str = "exception_triage"
        ) -> WorkflowHandle[Any, RunResult]:
            req = RunRequest(
                str(uuid.uuid4()), str(uuid.uuid4()), f"{tenant}/{key}", 1, {"exception_id": exception_id}
            )
            return await env.client.start_workflow(
                NovaWorkflow.run, req, id=f"run-{req.run_id}", task_queue=q, result_type=RunResult
            )

        start.fake = fake  # type: ignore[attr-defined]
        start.queue = q  # type: ignore[attr-defined]
        yield start


async def decide_task(
    h: WorkflowHandle[Any, Any], decision: str, payload: dict[str, Any] | None = None
) -> str:
    for _ in range(300):
        tasks = (await h.query(NovaWorkflow.state))["tasks"]
        if tasks:
            tid = str(next(iter(tasks)))
            await h.execute_update(CLAIM_TASK, args=[tid, "u1"])
            await h.execute_update(COMPLETE_TASK, args=[tid, "u1", decision, payload or {}])
            return tid
        await asyncio.sleep(0.05)
    raise AssertionError("no task opened")


def actions(w3: Any) -> list[str]:
    return [a.action for a in w3.fake.actions]


async def test_not_actionable_is_auto_closed_without_a_human(w3: Any) -> None:
    res = await (await w3("acme", "noise")).result()
    assert res.status == "completed" and w3.fake.tasks == []
    assert w3.fake.agents_called == ["exception_analyst"]  # no recommender call
    [close] = w3.fake.actions
    assert close.action == "exceptions.close" and close.params["note"] == "Auto-closed, not actionable"


async def test_accept_notifies_with_the_draft_and_closes(w3: Any) -> None:
    h = await w3("acme", "real")
    await decide_task(h, "accept")
    assert (await h.result()).status == "completed"
    task = next(t for t in w3.fake.tasks if t.title)
    assert task.app_key == "exception_panel" and task.assignee_role == "ops_lead"
    assert task.title == "Exception ROLLOVER · MSCU2858382 (medium)"
    assert task.payload["recommendations"] == RECS and task.payload["facts"][0]["metric"] == "rollover"
    assert actions(w3) == ["customer.notify", "exceptions.close"]
    notify = w3.fake.actions[0].params
    assert notify["customer_message"] == "Your box sails on MSC OSCAR." and notify["override_action"] == ""
    assert notify["recommendations"] == RECS


async def test_override_sends_the_ops_leads_words(w3: Any) -> None:
    h = await w3("acme", "real")
    await decide_task(h, "override", {"action": "We moved it to air freight.", "reason": "VIP"})
    assert (await h.result()).status == "completed"
    notify = w3.fake.actions[0].params
    assert notify["override_action"] == "We moved it to air freight." and notify["reason"] == "VIP"


async def test_dismiss_closes_without_notifying(w3: Any) -> None:
    h = await w3("acme", "real")
    await decide_task(h, "dismiss", {"reason": "duplicate of carrier notice"})
    assert (await h.result()).status == "rejected"
    [close] = w3.fake.actions
    assert close.action == "exceptions.close" and close.params["reason"] == "duplicate of carrier notice"


async def test_high_severity_reaches_a_human_only_for_bolt(w3: Any, env: WorkflowEnvironment) -> None:
    acme = await (await w3("acme", "bolt_high")).result()
    assert acme.status == "completed" and w3.fake.tasks == []  # Acme: decide says no, auto-close
    h = await w3("bolt", "bolt_high")
    for _ in range(300):
        if any(t.title for t in w3.fake.tasks):
            break
        await asyncio.sleep(0.05)
    await env.sleep(timedelta(minutes=31))  # Bolt's 30 min SLA
    for _ in range(300):
        if any(t.status == "escalated" for t in w3.fake.tasks):
            break
        await asyncio.sleep(0.05)
    esc = next(t for t in w3.fake.tasks if t.status == "escalated")
    assert esc.assignee_role == "tenant_admin"
    await decide_task(h, "accept")
    assert (await h.result()).status == "completed"


async def test_detection_passes_the_tenants_rules(w3: Any) -> None:
    res = await (await w3("acme", "", "detect_exceptions")).result()
    assert res.status == "completed"
    [detect] = w3.fake.actions
    assert detect.action == "exceptions.detect" and detect.params["rules"] == RULES
    assert detect.params["severity"] == {"ETA_SLIP": "high"}


async def test_scheduled_run_creates_a_run_and_returns(w3: Any, env: WorkflowEnvironment) -> None:
    run_id = await env.client.execute_workflow(
        ScheduledRun.run,
        ScheduledFire("t1", "detect_exceptions"),
        id=f"fire-{uuid.uuid4()}",
        task_queue=w3.queue,
    )
    [sub] = w3.fake.subruns
    assert sub.subject_type == "schedule" and sub.parent_run_id == "sched-t1-detect_exceptions"
    assert sub.config_version is None and sub.version is None  # latest version + config at firing time
    child = await env.client.get_workflow_handle(f"run-{run_id}").result()
    assert child["status"] == "completed"
