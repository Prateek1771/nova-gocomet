"""Human tasks. Reads come from the projection; claim/complete are workflow updates, so the engine
stays the only writer of task status and validates the transition (09 §7)."""

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from temporalio.client import RPCTimeoutOrCancelledError, WorkflowUpdateFailedError
from temporalio.service import RPCError, RPCStatusCode

from nova_api.authz import TenantDep, authorize
from nova_core import db
from nova_core import temporal as t

router = APIRouter(prefix="/tasks", tags=["tasks"])
UPDATE_TIMEOUT = timedelta(seconds=10)


class TaskOut(BaseModel):
    id: str
    run_id: str
    node_id: str
    title: str
    app_key: str
    status: str
    assignee_role: str | None
    assignee_user: str | None
    due_at: str | None
    decision: str | None
    payload: dict[str, Any]


class CompleteIn(BaseModel):
    decision: str = Field(max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


def _task(r: Any) -> TaskOut:
    return TaskOut(
        id=str(r.id),
        run_id=str(r.run_id),
        node_id=r.node_id,
        title=r.title,
        app_key=r.app_key,
        status=r.status,
        assignee_role=r.assignee_role,
        assignee_user=str(r.assignee_user) if r.assignee_user else None,
        due_at=r.due_at.isoformat() if r.due_at else None,
        decision=r.decision,
        payload=r.payload,
    )


_COLS = "id, run_id, node_id, title, app_key, status, assignee_role, assignee_user, due_at, decision, payload"


@router.get("")
async def inbox(c: TenantDep, mine: bool = False, status: str | None = None) -> list[TaskOut]:
    """Open work by default. `mine=true`: tasks I've claimed.
    ponytail: role-based inbox (OpenFGA ListObjects can_view) arrives in M4; until then every open task
    in the tenant is listed."""
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text(f"""select {_COLS} from human_tasks
                     where (cast(:st as text) is null and status <> 'done' or status = :st)
                       and (not :mine or assignee_user = cast(:u as uuid))
                     order by due_at nulls last limit 200"""),  # noqa: S608 (constant column list)
            {"st": status, "mine": mine, "u": c.sub},
        )
        return [_task(r) for r in rows]


async def _update(c: TenantDep, task_id: uuid.UUID, name: str, args: list[Any]) -> TaskOut:
    async with db.tenant_session(c.tenant_id) as s:
        wf = (
            await s.execute(
                text("""select r.temporal_workflow_id from human_tasks h
                        join workflow_runs r on r.id = h.run_id where h.id = :id"""),
                {"id": task_id},
            )
        ).scalar()
    if wf is None:
        raise HTTPException(404, "task not found")
    await send_update(wf, name, [str(task_id), c.sub, *args])
    async with db.tenant_session(c.tenant_id) as s:
        q = text(f"select {_COLS} from human_tasks where id = :id")  # noqa: S608
        r = (await s.execute(q, {"id": task_id})).one()
    return _task(r)


async def send_update(workflow_id: str, name: str, args: list[Any]) -> None:
    """Workflow update, bounded: with no engine worker polling, the caller gets a 503 instead of a hang.
    Retrying is safe: claim by the same user and a repeated complete are both rejected or no-ops."""
    try:
        handle = (await t.client()).get_workflow_handle(workflow_id)
        await handle.execute_update(name, args=args, rpc_timeout=UPDATE_TIMEOUT)
    except WorkflowUpdateFailedError as e:
        raise HTTPException(409, str(e.cause.message if hasattr(e.cause, "message") else e.cause)) from e
    except RPCTimeoutOrCancelledError as e:  # no worker accepted the update in time
        raise HTTPException(503, "workflow engine unavailable; retry shortly") from e
    except RPCError as e:
        if e.status in (RPCStatusCode.DEADLINE_EXCEEDED, RPCStatusCode.UNAVAILABLE, RPCStatusCode.CANCELLED):
            raise HTTPException(503, "workflow engine unavailable; retry shortly") from e
        raise HTTPException(409, f"task can't change: {e.message}") from e  # run already finished


@router.post("/{task_id}/claim")
async def claim(c: TenantDep, task_id: uuid.UUID) -> TaskOut:
    await authorize(c, "can_claim", f"task:{task_id}")
    return await _update(c, task_id, t.CLAIM_TASK, [])


@router.post("/{task_id}/complete")
async def complete(c: TenantDep, task_id: uuid.UUID, body: CompleteIn) -> TaskOut:
    # M4: context={"amount": …} so OpenFGA `within_limit` checks the TenantConfig approval limit
    await authorize(c, "can_complete", f"task:{task_id}")
    return await _update(c, task_id, t.COMPLETE_TASK, [body.decision, body.payload])
