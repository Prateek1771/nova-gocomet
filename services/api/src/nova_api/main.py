import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text

from nova_api import errors
from nova_api.deps import CallerDep
from nova_api.routers import apps, catalog, config, documents, runs, tasks, workflows
from nova_core import db
from nova_core.logging import configure_logging
from nova_core.settings import get_settings
from nova_core.telemetry import configure_tracing

settings = get_settings()
configure_logging(settings.log_level)
tracing = configure_tracing("nova-api", settings.otel_exporter_otlp_endpoint)

app = FastAPI(title="Nova API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
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
    # ponytail: capabilities (OpenFGA batch check) join this payload in M4
    return {"sub": p.sub, "email": p.email, "name": p.name, "tenant": tenant, "roles": sorted(p.roles)}


routers = (workflows, runs, tasks, config, documents, apps, catalog)
for r in (m.router for m in routers):
    v1.include_router(r)
app.include_router(v1)
