"""Engine activities: every DB write the workflow needs. Each one is one tenant-scoped transaction."""

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from opentelemetry import trace
from sqlalchemy import text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from nova_core import db
from nova_core.runs import create_run
from nova_engine.actions import ACTIONS
from nova_engine.contracts import (
    ActionRequest,
    Loaded,
    LoadRequest,
    StepEvent,
    Subrun,
    SubrunRequest,
    TaskWrite,
)

TERMINAL = {"completed", "rejected", "cancelled", "failed"}


def _trace_id() -> str | None:
    """The run's OTel trace (Temporal's interceptor propagates it into activities): the step links to
    Jaeger / Langfuse by it (08 §5 traceability)."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _j(v: Any) -> str:
    return json.dumps(v)


async def _audit(
    s: Any, tenant: str, run_id: str, actor_type: str, actor: str, action: str, subject: str, ev: Any
) -> None:
    """One audit row, stamped with the run's definition and config versions (09 §7); the DB trigger
    chains it (migration 0004)."""
    await s.execute(
        text("""insert into audit_log (tenant_id, actor_type, actor_id, action, subject, evidence,
                  definition_version, config_version)
                values (:t, :at, :a, :ac, :s, cast(:e as jsonb),
                  (select d.version from workflow_runs r join workflow_definitions d
                     on d.id = r.definition_id where r.id = cast(:r as uuid)),
                  (select config_version from workflow_runs where id = cast(:r as uuid)))"""),
        {"t": tenant, "r": run_id, "at": actor_type, "a": actor, "ac": action, "s": subject, "e": _j(ev)},
    )


@activity.defn
async def load_definition(req: LoadRequest) -> Loaded:
    """The immutable published version + the TenantConfig version pinned at run start."""
    async with db.tenant_session(uuid.UUID(req.tenant_id)) as s:
        compiled = (
            await s.execute(
                text("select compiled from workflow_definitions where id = :d and status = 'published'"),
                {"d": req.definition_id},
            )
        ).scalar()
        config = (
            await s.execute(
                text("select config from tenant_configs where version = :v"), {"v": req.config_version}
            )
        ).scalar()
    if compiled is None or config is None:
        raise ApplicationError("definition or config version not found", non_retryable=True)
    return Loaded(definition=compiled, config=config)


@activity.defn
async def project_step(e: StepEvent) -> None:
    """run_steps + workflow_runs.status + audit_log in one transaction (09 §7)."""
    async with db.tenant_session(uuid.UUID(e.tenant_id)) as s:
        if e.step_id is None:
            pass  # run-level transition only (cancel)
        elif e.status == "running":
            await s.execute(
                text("""insert into run_steps (id, run_id, tenant_id, node_id, node_type, status, trace_id)
                        values (:id, :r, :t, :n, :nt, 'running', :tr) on conflict (id) do nothing"""),
                {
                    "id": e.step_id,
                    "r": e.run_id,
                    "t": e.tenant_id,
                    "n": e.node_id,
                    "nt": e.node_type,
                    "tr": _trace_id(),
                },
            )
        else:
            await s.execute(
                text("""update run_steps set status = :st, output = cast(:o as jsonb),
                          ended_at = case when :st = 'waiting' then null else now() end
                        where id = :id"""),
                {"st": e.status, "o": _j(e.output), "id": e.step_id},
            )
        if e.run_status:
            await s.execute(
                text("""update workflow_runs set status = :st,
                          ended_at = case when :term then now() else ended_at end where id = :r"""),
                {"st": e.run_status, "term": e.run_status in TERMINAL, "r": e.run_id},
            )
        await _audit(
            s,
            e.tenant_id,
            e.run_id,
            "system",
            "engine",
            f"step.{e.status}",
            f"run:{e.run_id}/{e.node_id}",
            {"run_status": e.run_status} if e.run_status else None,
        )


@activity.defn
async def write_task(w: TaskWrite) -> None:
    async with db.tenant_session(uuid.UUID(w.tenant_id)) as s:
        if w.title is not None:
            await s.execute(
                text("""insert into human_tasks (id, tenant_id, run_id, node_id, title, app_key,
                          assignee_role, status, due_at, payload)
                        values (:id, :t, :r, :n, :ti, :app, :role, 'open', :due,
                          cast(:p as jsonb)) on conflict (id) do nothing"""),
                {
                    "id": w.task_id,
                    "t": w.tenant_id,
                    "r": w.run_id,
                    "n": w.node_id,
                    "ti": w.title,
                    "app": w.app_key,
                    "role": w.assignee_role,
                    "due": datetime.fromisoformat(w.due_at) if w.due_at else None,
                    "p": _j(w.payload),
                },
            )
        else:
            await s.execute(
                text("""update human_tasks set status = :st,
                          assignee_role = coalesce(:role, assignee_role),
                          assignee_user = case when :st = 'escalated' then null
                                               else coalesce(cast(:u as uuid), assignee_user) end,
                          decision = coalesce(:d, decision),
                          completed_by = case when :st = 'done' then cast(:u as uuid) end,
                          payload = payload || cast(:p as jsonb)
                        where id = :id"""),
                {
                    "st": w.status,
                    "role": w.assignee_role,
                    "u": w.assignee_user,
                    "d": w.decision,
                    "p": _j(w.payload),
                    "id": w.task_id,
                },
            )
        actor_type = "system" if w.actor == "system" else "user"
        run_id = w.run_id or str(
            (
                await s.execute(text("select run_id from human_tasks where id = :id"), {"id": w.task_id})
            ).scalar()
        )
        await _audit(
            s,
            w.tenant_id,
            run_id,
            actor_type,
            w.actor,
            f"task.{w.status}",
            f"task:{w.task_id}",
            {"decision": w.decision} if w.decision else None,
        )


@activity.defn
async def run_action(r: ActionRequest) -> Any:
    """Exactly-once side effects: the idempotency key row is the lock and the result cache (rule 8)."""
    tenant = uuid.UUID(r.tenant_id)
    async with db.tenant_session(tenant) as s:
        await s.execute(
            text("""insert into action_executions
                      (tenant_id, idempotency_key, run_id, node_id, action, request)
                    values (:t, :k, :r, :n, :a, cast(:q as jsonb))
                    on conflict (idempotency_key) do nothing"""),
            {
                "t": r.tenant_id,
                "k": r.idempotency_key,
                "r": r.run_id,
                "n": r.node_id,
                "a": r.action,
                "q": _j(r.params),
            },
        )
        row = (
            await s.execute(
                text("select status, response from action_executions where idempotency_key = :k"),
                {"k": r.idempotency_key},
            )
        ).one()
    if row.status == "succeeded":
        return row.response
    # ponytail: two attempts racing past this point would both run the action; Temporal runs one
    # attempt at a time, so add `select … for update skip locked` only if that ever changes
    try:
        response = await ACTIONS[r.action](r.params, r)
    except KeyError as e:
        raise ApplicationError(str(e), non_retryable=True) from e
    except Exception:
        async with db.tenant_session(tenant) as s:
            await s.execute(
                text("update action_executions set status = 'failed' where idempotency_key = :k"),
                {"k": r.idempotency_key},
            )
        raise
    async with db.tenant_session(tenant) as s:
        await s.execute(
            text("""update action_executions set status = 'succeeded', response = cast(:p as jsonb),
                      completed_at = now() where idempotency_key = :k"""),
            {"p": _j(response), "k": r.idempotency_key},
        )
    return response


ALL: list[Callable[..., Any]] = [load_definition, project_step, write_task, run_action]


@activity.defn
async def start_subrun(r: SubrunRequest) -> Subrun:
    async with db.tenant_session(uuid.UUID(r.tenant_id)) as s:
        try:
            run = await create_run(
                s,
                uuid.UUID(r.tenant_id),
                r.key,
                r.input,
                r.version,
                r.config_version,
                subject=("run", r.parent_run_id),
            )
        except LookupError as e:
            raise ApplicationError(str(e), non_retryable=True) from e
    return Subrun(str(run.run_id), str(run.definition_id), r.input)


ALL.append(start_subrun)
