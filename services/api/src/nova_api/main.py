import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text

from nova_api import clauses, errors
from nova_api.authz import TenantCaller, allowed
from nova_api.deps import CallerDep
from nova_api.routers import (
    admin,
    apps,
    audit,
    catalog,
    config,
    documents,
    exceptions,
    runs,
    tasks,
    workflows,
)
from nova_core import db, schedules
from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.telemetry import configure_tracing

settings = get_settings()
configure_logging(settings.log_level)
tracing = configure_tracing("nova-api", settings.otel_exporter_otlp_endpoint)


async def _reconcile_schedules(attempts: int = 30) -> None:
    for n in range(attempts):  # Temporal may still be coming up
        try:
            await schedules.reconcile()
            return
        except Exception as e:  # noqa: BLE001 (startup best effort; publish syncs again)
            if n == attempts - 1:
                structlog.get_logger().warning("schedules_reconcile_failed", error=str(e))
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(clauses.index_all()), asyncio.create_task(_reconcile_schedules())]
    if settings.llm_key_secret:
        tasks.append(asyncio.create_task(admin.provision_all()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(
    title="Nova API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
if tracing:
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
errors.install(app)
errors.install_api_error(app)
access_log = structlog.get_logger("access")


@app.middleware("http")
async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = rid
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=rid)
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    # JSON access log (uvicorn's text one is off); tenant_id is set by the auth dependency
    access_log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ms=round((time.perf_counter() - t0) * 1000, 1),
        tenant_id=getattr(request.state, "tenant_id", None),
    )
    return response


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz() -> dict[str, str]:
    async with db.session() as s:
        await s.execute(text("select 1"))
    return {"status": "ready"}


v1 = APIRouter(prefix="/api/v1")
CAPABILITIES = (
    "can_view",
    "can_operate",
    "can_design",
    "can_edit_config",
    "can_manage_budgets",
    "can_manage_users",
    "can_view_analytics",
    "can_read_audit",
)


@v1.get("/me")
async def me(c: CallerDep) -> dict[str, Any]:
    p = c.principal
    tenant = None
    if c.tenant_id:
        async with db.tenant_session(c.tenant_id) as s:
            # users is a thin mirror of Keycloak, refreshed whenever the shell loads /me
            await s.execute(
                text("""
                insert into users (id, tenant_id, email, name) values (:id, :t, :e, :n)
                on conflict (id) do update set email = excluded.email, name = excluded.name"""),
                {"id": p.sub, "t": c.tenant_id, "e": p.email, "n": p.name},
            )
            name = (
                await s.execute(text("select name from tenants where id = :t"), {"t": c.tenant_id})
            ).scalar()
        tenant = {"id": str(c.tenant_id), "slug": c.tenant_slug, "name": name}
    caps: list[str] = []
    if c.tenant_id:  # the shell hides what the caller can't do; the API still enforces every route
        tc, obj = TenantCaller(c, c.tenant_id), f"tenant:{c.tenant_id}"
        ok = await allowed(tc, [(cap, obj, [], None) for cap in CAPABILITIES])
        caps = [cap for cap, y in zip(CAPABILITIES, ok, strict=True) if y]
    return {
        "sub": p.sub,
        "email": p.email,
        "name": p.name,
        "tenant": tenant,
        "roles": sorted(p.roles),
        "capabilities": caps,
    }


routers = (workflows, runs, tasks, config, documents, apps, catalog, audit, admin, exceptions)
for r in (m.router for m in routers):
    v1.include_router(r)
app.include_router(v1)
