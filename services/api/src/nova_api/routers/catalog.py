"""Studio palettes (LLD §8): the agents, actions and micro-apps a workflow may reference."""

from typing import Any

from fastapi import APIRouter

from nova_api import definitions
from nova_api.authz import TenantDep, authorize

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/agents")
async def agents(c: TenantDep) -> list[dict[str, Any]]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    return definitions.catalog()["agents"]


@router.get("/actions")
async def actions(c: TenantDep) -> list[dict[str, Any]]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    return definitions.catalog()["actions"]


@router.get("/apps")
async def apps(c: TenantDep) -> list[dict[str, Any]]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    return definitions.catalog()["apps"]
