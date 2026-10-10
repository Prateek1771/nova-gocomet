"""W3 read side: exceptions (Postgres, RLS), governed analytics metrics (ClickHouse via query_metric only,
FR-4.2), the notifier's mock sink and Kafka consumer lag (M6 exit criteria)."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from nova_api.authz import TenantDep, authorize
from nova_core import clickhouse, db

router = APIRouter(tags=["exceptions"])


class ExceptionOut(BaseModel):
    id: str
    type: str
    severity: str
    status: str
    detected_at: str
    resolved_at: str | None
    note: str | None
    run_id: str | None
    shipment: dict[str, Any]
    facts: dict[str, Any]
    task_id: str | None = None


_SELECT = """select e.id, e.type, e.severity, e.status, e.detected_at, e.resolved_at, e.note, e.run_id,
               e.facts,
               s.id as shipment_id, s.bol_number, s.container_no, s.lane, s.pol, s.ts_port, s.pod, s.vessel,
               s.eta_planned, s.fields,
               (select h.id from human_tasks h where h.run_id = e.run_id
                  and h.status in ('open', 'claimed', 'escalated')
                order by h.due_at desc nulls last limit 1) as task_id
             from exceptions e join shipments s on s.id = e.shipment_id"""


def _out(r: Any) -> ExceptionOut:
    return ExceptionOut(
        id=str(r.id),
        type=r.type,
        severity=r.severity,
        status=r.status,
        detected_at=r.detected_at.isoformat(),
        resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
        note=r.note,
        run_id=str(r.run_id) if r.run_id else None,
        task_id=str(r.task_id) if r.task_id else None,
        facts=r.facts or {},
        shipment={
            "id": str(r.shipment_id),
            "bol_number": r.bol_number,
            "container_no": r.container_no,
            "lane": r.lane,
            "pol": r.pol,
            "ts_port": r.ts_port,
            "pod": r.pod,
            "vessel": r.vessel,
            "carrier": (r.fields or {}).get("carrier"),
            "eta_planned": r.eta_planned.isoformat() if r.eta_planned else None,
        },
    )


@router.get("/exceptions")
async def list_exceptions(
    c: TenantDep,
    status: str | None = Query(None, pattern="^(open|acknowledged|resolved)$"),
    limit: int = Query(200, le=500),
) -> list[ExceptionOut]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text(f"""{_SELECT} where (cast(:st as text) is null or e.status = :st)
                     order by e.detected_at desc limit :n"""),  # noqa: S608 (constant SQL)
            {"st": status, "n": limit},
        )
        return [_out(r) for r in rows]


@router.get("/exceptions/{exception_id}")
async def get_exception(c: TenantDep, exception_id: uuid.UUID) -> ExceptionOut:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        r = (
            await s.execute(text(f"{_SELECT} where e.id = :e"), {"e": exception_id})  # noqa: S608
        ).one_or_none()
    if r is None:
        raise HTTPException(404, "exception not found")
    return _out(r)


class MetricOut(BaseModel):
    metric: str
    description: str
    sql: str
    rows: list[dict[str, Any]]


@router.get("/analytics/metrics")
async def list_metrics(c: TenantDep) -> list[dict[str, Any]]:
    await authorize(c, "can_view_analytics", f"tenant:{c.tenant_id}")
    return [{"metric": k, **{f: m.get(f) for f in ("description", "grain", "value", "columns")}}
            for k, m in clickhouse.metrics().items()]  # fmt: skip


@router.get("/analytics/metrics/{name}")
async def metric(
    c: TenantDep, name: str, shipment_id: uuid.UUID | None = None, limit: int = Query(100, le=500)
) -> MetricOut:
    """A governed metric for the caller's tenant (definitions/metrics.yaml; the row policy does the rest)."""
    await authorize(c, "can_view_analytics", f"tenant:{c.tenant_id}")
    if name not in clickhouse.metrics():
        raise HTTPException(404, f"unknown metric {name!r}")
    try:
        res = await clickhouse.query_metric(
            c.tenant_id, name, shipment_id=str(shipment_id) if shipment_id else None, limit=limit
        )
    except clickhouse.MetricError as e:
        raise HTTPException(503, "analytics store unavailable") from e
    except OSError as e:  # httpx connect errors when the data profile is down
        raise HTTPException(503, "analytics store unavailable") from e
    return MetricOut(
        metric=name, description=clickhouse.metrics()[name]["description"], sql=res["sql"], rows=res["rows"]
    )


class NotificationOut(BaseModel):
    id: str
    topic: str
    type: str
    recipient: str
    subject: str
    body: str
    refs: dict[str, Any]
    received_at: str


@router.get("/notifications")
async def notifications(c: TenantDep, limit: int = Query(50, le=200)) -> list[NotificationOut]:
    """The notifier's mock sink: what would have been sent (dispute and customer delay emails)."""
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text("""select id, topic, type, recipient, subject, body, refs, received_at from notifications
                    order by received_at desc limit :n"""),
            {"n": limit},
        )
        return [
            NotificationOut(**{**r._asdict(), "id": str(r.id), "received_at": r.received_at.isoformat()})
            for r in rows
        ]


class LagOut(BaseModel):
    group_id: str
    topic: str
    lag: int
    alerting: bool
    measured_at: str


@router.get("/ops/kafka-lag")
async def kafka_lag(c: TenantDep) -> list[LagOut]:
    """Consumer lag per group + topic (ops data, no tenant rows in it)."""
    await authorize(c, "can_view_analytics", f"tenant:{c.tenant_id}")
    async with db.session() as s:
        rows = await s.execute(
            text("select group_id, topic, lag, alerting, measured_at from kafka_lag order by group_id, topic")
        )
        return [LagOut(**{**r._asdict(), "measured_at": r.measured_at.isoformat()}) for r in rows]
