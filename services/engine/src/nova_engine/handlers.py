"""One small handler per node type (LLD §2.2). Runs inside workflow code: only deterministic work
here (CEL, routing); every I/O call is an activity."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from nova_core.registry import Registry
    from nova_core.temporal import AGENTS_QUEUE, WORKFLOW
    from nova_dsl import cel
    from nova_dsl.models import (
        ActionNode,
        AgentNode,
        DecideNode,
        EndNode,
        HumanTaskNode,
        ParallelNode,
        RuleNode,
        SubflowNode,
        WaitNode,
    )
    from nova_engine import activities as acts
    from nova_engine.contracts import (
        ActionRequest,
        AgentRequest,
        RunRequest,
        RunResult,
        SubrunRequest,
        TaskWrite,
    )

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nova_engine.interpreter import NovaWorkflow

    Handler = Callable[[NovaWorkflow, Any, "Step"], Awaitable[Any]]

HANDLERS: "Registry[Handler]" = Registry("node handler")
DEFAULT_TIMEOUT = timedelta(seconds=60)
FAST = timedelta(seconds=30)


@dataclass
class Step:
    id: str
    visit: int


def _retry(node: AgentNode | ActionNode) -> RetryPolicy:
    return RetryPolicy(maximum_attempts=node.retry.max_attempts if node.retry else 3)


@HANDLERS.register("rule")
async def rule(w: "NovaWorkflow", node: RuleNode, s: Step) -> dict[str, Any]:
    act = w.activation()
    for c in node.cases:
        if cel.truthy(c.when, act):
            return {"goto": c.goto, "matched": c.when}
    return {"goto": node.default}


@HANDLERS.register("agent")
async def agent(w: "NovaWorkflow", node: AgentNode, s: Step) -> Any:
    req = AgentRequest(
        w.req.tenant_id, w.req.run_id, node.id, node.agent, cel.render(node.with_, w.activation())
    )
    return await workflow.execute_activity(
        "run_agent",
        req,
        task_queue=AGENTS_QUEUE,
        start_to_close_timeout=node.timeout or DEFAULT_TIMEOUT,
        retry_policy=_retry(node),
    )


@HANDLERS.register("decide")
async def decide(w: "NovaWorkflow", node: DecideNode, s: Step) -> Any:
    act = w.activation()
    questions = [
        {
            "id": q.id,
            "ask": q.ask,
            "context": cel.render(q.context, act),
            "criteria": q.criteria.model_dump() if q.criteria else None,
            "threshold": q.threshold,
        }
        for q in node.questions
    ]
    req = AgentRequest(w.req.tenant_id, w.req.run_id, node.id, "decide", {"questions": questions})
    return await workflow.execute_activity(
        "decide",
        req,
        task_queue=AGENTS_QUEUE,
        start_to_close_timeout=node.timeout or DEFAULT_TIMEOUT,
        retry_policy=RetryPolicy(maximum_attempts=3),  # fallback model + human task live in the activity
    )


@HANDLERS.register("action")
async def action(w: "NovaWorkflow", node: ActionNode, s: Step) -> Any:
    key = f"{w.req.tenant_id}:{w.req.run_id}:{node.id}:{s.visit}"  # stable across retries and replays
    req = ActionRequest(
        w.req.tenant_id, w.req.run_id, node.id, key, node.action, cel.render(node.with_, w.activation())
    )
    return await workflow.execute_activity(
        acts.run_action,
        req,
        start_to_close_timeout=node.timeout or DEFAULT_TIMEOUT,
        retry_policy=_retry(node),
    )


@HANDLERS.register("human_task")
async def human_task_node(w: "NovaWorkflow", node: HumanTaskNode, s: Step) -> Any:
    return await human_task(w, node, s)


async def human_task(w: "NovaWorkflow", node: HumanTaskNode, s: Step, waiting: str = "waiting_human") -> Any:
    """Also used for the engine's own `needs_attention` task (waiting="needs_attention")."""
    act = w.activation()
    task_id = str(workflow.uuid4())
    t = w.tasks[task_id] = {
        "node_id": node.id,
        "status": "open",
        "user": None,
        "outputs": node.outputs,
        "result": None,
    }
    due = (workflow.now() + node.sla).isoformat() if node.sla else None
    await w.write_task(
        TaskWrite(
            w.req.tenant_id,
            task_id,
            "open",
            run_id=w.req.run_id,
            node_id=node.id,
            title=cel.render(node.title, act),
            app_key=node.app,
            assignee_role=node.assignee.role,
            due_at=due,
            payload=cel.render(node.with_, act),
        )
    )
    await w.project(s, node, "waiting", run_status=waiting)
    try:
        if node.escalate:
            try:
                await workflow.wait_condition(lambda: t["result"] is not None, timeout=node.escalate.after)
            except TimeoutError:
                t.update(status="escalated", user=None)
                await w.write_task(
                    TaskWrite(w.req.tenant_id, task_id, "escalated", assignee_role=node.escalate.to.role)
                )
        await workflow.wait_condition(lambda: t["result"] is not None)
    except asyncio.CancelledError:
        await w.write_task(TaskWrite(w.req.tenant_id, task_id, "done", decision="cancelled"))
        raise
    del w.tasks[task_id]
    return t["result"]


@HANDLERS.register("wait")
async def wait(w: "NovaWorkflow", node: WaitNode, s: Step) -> dict[str, Any]:
    if node.event is None:
        assert node.duration is not None  # graph.validate guarantees one of the two
        await workflow.sleep(node.duration)
        return {}
    event = node.event
    try:
        await workflow.wait_condition(lambda: event in w.events, timeout=node.duration)
    except TimeoutError:
        return {"timed_out": True}
    return {"event": w.events.pop(event)}


@HANDLERS.register("parallel")
async def parallel(w: "NovaWorkflow", node: ParallelNode, s: Step) -> dict[str, Any]:
    async def branch(i: int, ids: list[str]) -> int:
        for nid in ids:
            await w.step(w.wf.node(nid))
        return i

    tasks = [asyncio.ensure_future(branch(i, b)) for i, b in enumerate(node.branches)]
    if node.join == "all":
        await asyncio.gather(*tasks)
        return {"completed": list(range(len(tasks)))}
    done, pending = await workflow.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()
    first = min(d.result() for d in done)  # result() re-raises a failed branch
    return {"completed": [first]}


@HANDLERS.register("subflow")
async def subflow(w: "NovaWorkflow", node: SubflowNode, s: Step) -> dict[str, Any]:
    key, _, version = node.workflow.partition("@")
    child = await workflow.execute_activity(
        acts.start_subrun,
        SubrunRequest(
            w.req.tenant_id,
            key,
            int(version) if version else None,
            w.req.config_version,
            cel.render(node.with_, w.activation()),
            parent_run_id=w.req.run_id,
        ),
        start_to_close_timeout=FAST,
    )
    result: RunResult = await workflow.execute_child_workflow(
        WORKFLOW,
        RunRequest(child.run_id, w.req.tenant_id, child.definition_id, w.req.config_version, child.input),
        id=f"run-{child.run_id}",
        result_type=RunResult,
    )
    return {"run_id": child.run_id, "status": result.status}


@HANDLERS.register("end")
async def end(w: "NovaWorkflow", node: EndNode, s: Step) -> dict[str, Any]:
    return {"status": node.status}
