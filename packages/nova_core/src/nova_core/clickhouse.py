"""Governed ClickHouse reads (FR-4.2, ADR-035). `query_metric` is the only way agents, exception rules and the
API read analytics: it accepts a metric name from definitions/metrics.yaml (a dbt view), builds the SQL
itself with bound parameters (no model- or user-written SQL), and runs it as the read-only nova_reader with
SQL_tenant_id set, so the row policy returns that tenant's rows only. Plain httpx over the HTTP interface,
like nova_core.vectors. Tests pass a transport."""

import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from nova_core.settings import get_settings

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "!=": "!="}
MAX_ROWS = 500


class MetricError(Exception):
    pass


@lru_cache
def metrics() -> dict[str, dict[str, Any]]:
    return dict(yaml.safe_load((DEFINITIONS / "metrics.yaml").read_text(encoding="utf-8"))["metrics"])


def build(
    metric: str,
    shipment_id: str | None = None,
    op: str | None = None,
    threshold: float | None = None,
    limit: int = 100,
) -> tuple[str, dict[str, str]]:
    """The SQL and its bound parameters for one registered metric. Raises MetricError on anything else."""
    m = metrics().get(metric)
    if m is None:
        raise MetricError(f"unknown metric {metric!r}")
    where, params = ["1"], {}
    if shipment_id is not None:
        where.append("shipment_id = {shipment_id:UUID}")
        params["shipment_id"] = str(uuid.UUID(str(shipment_id)))
    if op is not None:
        if op not in OPS or threshold is None:
            raise MetricError(f"bad comparison {op!r} {threshold!r}")
        where.append(f"{m['value']} {OPS[op]} {{threshold:Float64}}")
        params["threshold"] = str(float(threshold))
    # identifiers come from the registry (trusted config); values are bound parameters
    cols, view, cond = ", ".join(m["columns"]), m.get("view", metric), " AND ".join(where)
    n = max(1, min(int(limit), MAX_ROWS))
    sql = f"SELECT {cols} FROM nova_metrics.{view} WHERE {cond} ORDER BY {m['order']} LIMIT {n}"  # noqa: S608
    return sql, params


class ClickHouse:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        self._http = httpx.AsyncClient(
            base_url=s.clickhouse_url,
            headers={
                "X-ClickHouse-User": s.clickhouse_reader,
                "X-ClickHouse-Key": s.clickhouse_reader_password,
            },
            timeout=30,
            transport=transport,
        )

    async def query(
        self, tenant_id: uuid.UUID | str, sql: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        q = {"SQL_tenant_id": str(uuid.UUID(str(tenant_id))), "default_format": "JSON", "readonly": "2"}
        q.update({f"param_{k}": v for k, v in params.items()})
        r = await self._http.post("/", params=q, content=sql)
        if r.status_code != 200:
            raise MetricError(f"ClickHouse {r.status_code}: {r.text[:300]}")
        return list(r.json().get("data") or [])


_client: ClickHouse | None = None


def client() -> ClickHouse:
    global _client
    if _client is None:
        _client = ClickHouse()
    return _client


def use(ch: ClickHouse | None) -> None:
    global _client
    _client = ch


async def query_metric(
    tenant_id: uuid.UUID | str,
    metric: str,
    shipment_id: str | None = None,
    op: str | None = None,
    threshold: float | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """{metric, sql, rows}: the SQL is kept with the rows, so it can be shown as evidence (W3 analyst)."""
    sql, params = build(metric, shipment_id, op, threshold, limit)
    rows = await client().query(tenant_id, sql, params)
    return {"metric": metric, "sql": sql, "params": params, "rows": rows}
