"""Runs: create the projection row, start NovaWorkflow, read the projection the engine writes."""

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from nova_api.authz import TenantDep, authorize
from nova_core import db
from nova_core import temporal as t
from nova_core.runs import create_run

router = APIRouter(prefix="/runs", tags=["runs"])
log = structlog.get_logger()
TERMINAL = ("completed", "rejected", "cancelled", "failed")
POLL_S = 0.5
HEARTBEAT_S = 15.0


class StartIn(BaseModel):
    workflow_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int | None = None  # default: latest published
    input: dict[str, Any] = Field(default_factory=dict)
    subject_type: str | None = None
    subject_id: str | None = None


class RunOut(BaseModel):
    id: str
    workflow_key: str
    version: int
    config_version: int
    status: str
    input: dict[str, Any]
    started_at: str
    ended_at: str | None
    cost_usd: float = 0.0
    subject_type: str | None = None
    subject_id: str | None = None
    temporal_workflow_id: str | None = None


class StepOut(BaseModel):
    node_id: str
    node_type: str
    status: str
    output: Any
    started_at: str
    ended_at: str | None
    trace_id: str | None = None  # OTel trace: Jaeger / Langfuse link


class TaskBrief(BaseModel):
    id: str
    node_id: str
    title: str
    status: str
    assignee_role: str | None


class RunDetail(RunOut):
    steps: list[StepOut]
    tasks: list[TaskBrief]


_RUN = """select r.id, d.key, d.version, r.config_version, r.status, r.input, r.started_at, r.ended_at,
                 r.temporal_workflow_id, r.cost_usd, r.subject_type, r.subject_id
          from workflow_runs r join workflow_definitions d on d.id = r.definition_id"""


def _run(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "workflow_key": r.key,
        "version": r.version,
        "config_version": r.config_version,
        "status": r.status,
        "input": r.input,
        "started_at": r.started_at.isoformat(),
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "cost_usd": float(r.cost_usd or 0),
        "subject_type": r.subject_type,
        "subject_id": r.subject_id,
        "temporal_workflow_id": r.temporal_workflow_id,
    }


async def launch(
    tenant_id: uuid.UUID,
    key: str,
    input: dict[str, Any],
    version: int | None = None,
    subject: tuple[str, str] | None = None,
) -> uuid.UUID:
    """Create the projection row and start NovaWorkflow. Shared by POST /runs and document uploads."""
    async with db.tenant_session(tenant_id) as s:
        try:
            run = await create_run(s, tenant_id, key, input, version, subject=subject)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
    req = {
        "run_id": str(run.run_id),
        "tenant_id": str(tenant_id),
        "definition_id": str(run.definition_id),
        "config_version": run.config_version,
        "input": input,
    }
    try:
        client = await t.client()
        await client.start_workflow(t.WORKFLOW, req, id=run.workflow_id, task_queue=t.ENGINE_QUEUE)
    except Exception as e:
        log.exception("start_workflow failed", run_id=str(run.run_id))
        async with db.tenant_session(tenant_id) as s:
            await s.execute(
                text("update workflow_runs set status = 'failed', ended_at = now() where id = :r"),
                {"r": run.run_id},
            )
        raise HTTPException(503, "workflow engine unavailable") from e
    return run.run_id


@router.post("", status_code=201)
async def start(c: TenantDep, body: StartIn) -> RunOut:
    await authorize(c, "can_start", f"workflow:{c.tenant_id}/{body.workflow_key}")
    subject = (body.subject_type, body.subject_id) if body.subject_type and body.subject_id else None
    run_id = await launch(c.tenant_id, body.workflow_key, body.input, body.version, subject)
    return await get_run(c, run_id)


@router.get("")
async def list_runs(
    c: TenantDep,
    status: str | None = None,
    workflow_key: str | None = None,
    limit: int = Query(50, le=200),
    hide_scheduled_ok: bool = Query(
        False, description="leave out scheduled runs that completed (W3 fires each minute)"
    ),
) -> list[RunOut]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text(
                _RUN
                + """ where (cast(:st as text) is null or r.status = :st)
                              and (cast(:k as text) is null or d.key = :k)
                              and not (:h and r.subject_type = 'schedule' and r.status = 'completed')
                            order by r.started_at desc limit :l"""
            ),
            {"st": status, "k": workflow_key, "l": limit, "h": hide_scheduled_ok},
        )
        return [RunOut(**_run(r)) for r in rows]


@router.get("/{run_id}")
async def get_run(c: TenantDep, run_id: uuid.UUID) -> RunDetail:
    await authorize(c, "can_view", f"run:{run_id}")
    async with db.tenant_session(c.tenant_id) as s:
        r = (await s.execute(text(_RUN + " where r.id = :r"), {"r": run_id})).one_or_none()
        if r is None:
            raise HTTPException(404, "run not found")
        steps = await s.execute(
            text("""select node_id, node_type, status, output, started_at, ended_at, trace_id from run_steps
                    where run_id = :r order by started_at, id"""),
            {"r": run_id},
        )
        tasks = await s.execute(
            text("""select id, node_id, title, status, assignee_role from human_tasks
                    where run_id = :r and status <> 'done' order by due_at nulls last"""),
            {"r": run_id},
        )
        return RunDetail(
            **_run(r),
            steps=[
                StepOut(
                    node_id=x.node_id,
                    node_type=x.node_type,
                    status=x.status,
                    output=x.output,
                    started_at=x.started_at.isoformat(),
                    ended_at=x.ended_at.isoformat() if x.ended_at else None,
                    trace_id=x.trace_id,
                )
                for x in steps
            ],
            tasks=[
                TaskBrief(
                    id=str(x.id),
                    node_id=x.node_id,
                    title=x.title,
                    status=x.status,
                    assignee_role=x.assignee_role,
                )
                for x in tasks
            ],
        )


@router.get("/{run_id}/stream")
async def stream(
    c: TenantDep, run_id: uuid.UUID, request: Request, last_event_id: str | None = Header(None)
) -> StreamingResponse:
    """SSE for the live run view (FR-1.7): a `snapshot` event (the run detail) whenever it changes, until
    the run ends. The event id is a hash of the snapshot, so a reconnect with Last-Event-ID skips a
    snapshot the client already has. ponytail: polls the projection every 0.5 s; Postgres LISTEN/NOTIFY
    on run_steps if many viewers ever make polling cost something."""
    first = await get_run(c, run_id)  # 404 / authz before the stream opens

    async def events() -> AsyncIterator[str]:
        sent, beat, detail = last_event_id, time.monotonic(), first
        while True:
            body = detail.model_dump_json()
            eid = hashlib.sha256(body.encode()).hexdigest()[:16]
            if eid != sent:
                sent, beat = eid, time.monotonic()
                yield f"id: {eid}\nevent: snapshot\ndata: {body}\n\n"
            if detail.status in TERMINAL:
                yield "event: end\ndata: {}\n\n"
                return
            if time.monotonic() - beat > HEARTBEAT_S:
                beat = time.monotonic()
                yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_S)
            if await request.is_disconnected():
                return
            detail = await get_run(c, run_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@router.post("/{run_id}/cancel", status_code=202)
async def cancel(c: TenantDep, run_id: uuid.UUID) -> dict[str, str]:
    await authorize(c, "can_cancel", f"run:{run_id}")
    async with db.tenant_session(c.tenant_id) as s:
        r = (
            await s.execute(
                text("select status, temporal_workflow_id from workflow_runs where id = :r"), {"r": run_id}
            )
        ).one_or_none()
    if r is None:
        raise HTTPException(404, "run not found")
    if r.status in TERMINAL:
        raise HTTPException(409, f"run is already {r.status}")
    await (await t.client()).get_workflow_handle(r.temporal_workflow_id).cancel()
    return {"status": "cancelling"}
