"""`NovaWorkflow`: the one generic interpreter for every process (LLD §2.4). It never names a
process, doc type or tenant: routing comes from the definition, thresholds from TenantConfig."""

import asyncio
from dataclasses import replace
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from nova_core.temporal import CLAIM_TASK, COMPLETE_TASK, EVENT, WORKFLOW
    from nova_dsl import cel
    from nova_dsl.graph import entry, next_node
    from nova_dsl.models import Assignee, EndNode, HumanTaskNode, Node, Workflow
    from nova_engine import activities as acts
    from nova_engine.contracts import LoadRequest, RunRequest, RunResult, StepEvent, TaskWrite
    from nova_engine.handlers import FAST, HANDLERS, Step, human_task

FAILED = (ActivityError, ChildWorkflowError, ApplicationError)


@workflow.defn(name=WORKFLOW)
class NovaWorkflow:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}  # open human tasks by id
        self.events: dict[str, Any] = {}
        self.status = "pending"
        self.current: set[str] = set()

    @workflow.run
    async def run(self, req: RunRequest) -> RunResult:
        self.req = req
        loaded = await workflow.execute_activity(
            acts.load_definition,
            LoadRequest(req.tenant_id, req.definition_id, req.config_version),
            start_to_close_timeout=FAST,
        )
        self.wf = Workflow.model_validate(loaded.definition)
        self.config = loaded.config  # pinned snapshot: config edits mid-run don't reroute this run
        self.nodes, self.visits = dict(req.nodes), dict(req.visits)
        node = self.wf.node(req.resume_at) if req.resume_at else entry(self.wf)
        try:
            while not isinstance(node, EndNode):
                if workflow.info().get_current_history_length() > req.max_events:
                    workflow.continue_as_new(
                        replace(req, nodes=self.nodes, resume_at=node.id, visits=self.visits)
                    )
                try:
                    out = await self.step(node)
                except FAILED as e:
                    if is_cancelled_exception(e):
                        raise
                    if await self.attention(node, e) == "abort":
                        await self.project(None, node, "failed", run_status="failed")
                        return RunResult("failed", self.nodes)
                    continue  # retry the same node
                node = next_node(self.wf, node, out)
            await self.step(node, run_status=node.status)
            return RunResult(node.status, self.nodes)
        except BaseException as e:  # workflow cancel arrives as CancelledError or a cancelled ActivityError
            if isinstance(e, asyncio.CancelledError) or is_cancelled_exception(e):
                await self.project(None, node, "cancelled", run_status="cancelled")
            raise

    async def step(self, node: Node, run_status: str | None = None) -> Any:
        visit = self.visits[node.id] = self.visits.get(node.id, 0) + 1
        s = Step(str(workflow.uuid4()), visit)
        await self.project(s, node, "running", run_status=None if self.status == "running" else "running")
        self.current.add(node.id)
        try:
            out = await HANDLERS[node.type](self, node, s)
        except cel.CelError as e:
            await self.project(s, node, "failed", output={"error": str(e)})
            raise ApplicationError(str(e), type="CelError", non_retryable=True) from e
        except asyncio.CancelledError:
            await self.project(s, node, "skipped")
            raise
        except FAILED as e:
            if not is_cancelled_exception(e):
                await self.project(s, node, "failed", output={"error": str(e.cause or e)})
            raise
        finally:
            self.current.discard(node.id)
        self.nodes[node.id] = {"output": out}
        await self.project(s, node, "completed", output=out, run_status=run_status)
        return out

    async def attention(self, node: Node, err: Exception) -> str:
        """Retries exhausted / budget exhausted / bad expression → `needs_attention` + a task to retry or
        abort (09 §7). The task carries the error type so the screen can say what to fix first."""
        cause = getattr(err, "cause", None) or err
        kind = getattr(cause, "type", None) or type(cause).__name__
        title = (
            f"LLM budget exhausted at {node.id!r}"
            if kind == "BudgetExceeded"
            else f"Step {node.id!r} failed: {cause}"
        )
        fix = HumanTaskNode(
            id=node.id,
            type="human_task",
            title=title[:200],
            assignee=Assignee(role=self.config.get("attention_role", "tenant_admin")),
            app="step_failure",
            outputs=["retry", "abort"],
            **{"with": {"error_type": kind, "message": str(cause)[:500]}},
        )
        s = Step(str(workflow.uuid4()), self.visits.get(node.id, 0))
        out = await human_task(self, fix, s, waiting="needs_attention")
        await self.project(s, fix, "completed", output=out)
        decision: str = out["decision"]
        return decision

    def activation(self) -> dict[str, Any]:
        return cel.activation(
            {"input": self.req.input, "nodes": self.nodes, "tenant": self.config}, workflow.now()
        )

    async def project(
        self, s: Step | None, node: Node, status: str, output: Any = None, run_status: str | None = None
    ) -> None:
        if run_status:
            self.status = run_status
        await workflow.execute_local_activity(
            acts.project_step,
            StepEvent(
                self.req.tenant_id,
                self.req.run_id,
                s.id if s else None,
                node.id,
                node.type,
                status,
                output,
                run_status,
            ),
            start_to_close_timeout=FAST,
        )

    async def write_task(self, w: TaskWrite) -> None:
        await workflow.execute_activity(acts.write_task, w, start_to_close_timeout=FAST)

    # --- human input: updates (validated, synchronous answer to the API) and signals ---

    @workflow.update(name=CLAIM_TASK)
    async def claim_task(self, task_id: str, user: str) -> dict[str, Any]:
        t = self.tasks[task_id]
        t.update(status="claimed", user=user)
        await self.write_task(
            TaskWrite(self.req.tenant_id, task_id, "claimed", assignee_user=user, actor=user)
        )
        return {"task_id": task_id, "status": "claimed"}

    @claim_task.validator
    def _claim_ok(self, task_id: str, user: str) -> None:
        t = self.tasks.get(task_id)
        if t is None or t["status"] == "done":
            raise ValueError("task is not open")
        if t["status"] == "claimed" and t["user"] != user:
            raise ValueError("task is claimed by someone else")

    @workflow.update(name=COMPLETE_TASK)
    async def complete_task(
        self, task_id: str, user: str, decision: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        t = self.tasks[task_id]
        t["status"] = "done"  # validators now reject a second completion
        await self.write_task(
            TaskWrite(
                self.req.tenant_id,
                task_id,
                "done",
                assignee_user=user,
                decision=decision,
                payload=payload,
                actor=user,
            )
        )
        t["result"] = {"decision": decision, "payload": payload, "by": user}  # wakes the waiting node
        return {"task_id": task_id, "status": "done", "decision": decision}

    @complete_task.validator
    def _complete_ok(self, task_id: str, user: str, decision: str, payload: dict[str, Any]) -> None:
        t = self.tasks.get(task_id)
        if t is None or t["status"] == "done":
            raise ValueError("task is not open")
        if t["status"] != "claimed" or t["user"] != user:
            raise ValueError("claim the task before completing it")
        if decision not in t["outputs"]:
            raise ValueError(f"decision must be one of {t['outputs']}")

    @workflow.signal(name=EVENT)
    def event(self, name: str, payload: Any = None) -> None:
        self.events[name] = payload

    @workflow.query
    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current": sorted(self.current),
            "tasks": {k: {**v, "result": None} for k, v in self.tasks.items()},
        }
