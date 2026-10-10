"""TenantConfig: every PUT is a new immutable version; runs pin the version they started on."""

import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from nova_api.authz import TenantDep, authorize
from nova_api.routers import admin, audit
from nova_core import db
from nova_dsl import TenantConfig

router = APIRouter(prefix="/tenant-config", tags=["config"])
log = structlog.get_logger()


class ConfigOut(BaseModel):
    version: int
    config: dict[str, Any]
    published_by: str
    published_at: str


async def _latest(c: TenantDep) -> ConfigOut:
    async with db.tenant_session(c.tenant_id) as s:
        r = (
            await s.execute(
                text("""select version, config, published_by, published_at from tenant_configs
                        order by version desc limit 1""")
            )
        ).one_or_none()
    if r is None:
        raise HTTPException(404, "tenant has no config")
    return ConfigOut(
        version=r.version,
        config=r.config,
        published_by=r.published_by,
        published_at=r.published_at.isoformat(),
    )


@router.get("")
async def get_config(c: TenantDep) -> ConfigOut:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    return await _latest(c)


@router.put("")
async def put_config(c: TenantDep, body: TenantConfig) -> ConfigOut:
    await authorize(c, "can_edit_config", f"tenant:{c.tenant_id}")
    try:
        async with db.tenant_session(c.tenant_id) as s:
            version = (
                await s.execute(
                    text("""insert into tenant_configs (tenant_id, version, config, published_by)
                            select :t, coalesce(max(version), 0) + 1, cast(:c as jsonb), :by
                            from tenant_configs returning version"""),
                    {"t": c.tenant_id, "c": json.dumps(body.model_dump(mode="json")), "by": c.sub},
                )
            ).scalar_one()
            await audit.record(s, c, "config.published", f"tenant:{c.tenant_id}", config_version=version)
    except IntegrityError as e:
        raise HTTPException(409, "another config was just published; retry") from e
    try:  # the budget applies to the next model call; the gateway being down doesn't block the publish
        await admin.provision(
            c.tenant_id, c.caller.tenant_slug or "", body.model_dump().get("llm_budget_usd")
        )
    except httpx.HTTPError as e:
        log.warning("llm_key_provision_failed", error=str(e))
    return await _latest(c)
