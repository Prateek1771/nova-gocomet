"""Tenant admin: LLM budget (FR-2.4). Each tenant has its own LiteLLM virtual key, derived from the tenant
id (ADR-028) and provisioned here with `TenantConfig.llm_budget_usd`. LiteLLM enforces the budget; the
agents turn its refusal into a needs_attention task. Provisioning runs at API start and on every config
publish, so a budget change applies to the next model call."""

import asyncio
import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from nova_api.authz import TenantDep, authorize
from nova_core import db
from nova_core.llm_keys import tenant_key
from nova_core.settings import get_settings

router = APIRouter(prefix="/admin", tags=["admin"])
log = structlog.get_logger()
BUDGET_PERIOD = "30d"  # LiteLLM resets spend each period


def _gateway() -> httpx.AsyncClient:
    s = get_settings()  # the gateway (master) key manages tenant keys; it never leaves the backend
    return httpx.AsyncClient(
        base_url=s.llm_base_url, headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=10
    )


async def _info(http: httpx.AsyncClient, key: str) -> dict[str, Any] | None:
    r = await http.get("/key/info", params={"key": key})
    if r.status_code in (400, 404):
        return None
    info: dict[str, Any] = r.raise_for_status().json().get("info") or {}
    return info


async def provision(tenant: uuid.UUID, slug: str, budget: float | None) -> None:
    key = tenant_key(str(tenant))
    if key is None:
        return
    async with _gateway() as http:
        if await _info(http, key) is None:
            body = {
                "key": key,
                "key_alias": f"tenant-{slug}",
                "max_budget": budget,
                "budget_duration": BUDGET_PERIOD,
                "metadata": {"tenant_id": str(tenant)},
            }
            (await http.post("/key/generate", json=body)).raise_for_status()
        else:
            (await http.post("/key/update", json={"key": key, "max_budget": budget})).raise_for_status()
    log.info("llm_key_provisioned", tenant=slug, max_budget=budget)


async def provision_all(attempts: int = 30) -> None:
    """Every tenant's key with its latest configured budget. Retries while the gateway starts."""
    async with db.session() as s:
        tenants = (await s.execute(text("select id, slug from tenants"))).all()
    for t in tenants:
        async with db.tenant_session(t.id) as s:
            cfg = (
                await s.execute(text("select config from tenant_configs order by version desc limit 1"))
            ).scalar()
        for n in range(attempts):
            try:
                await provision(t.id, t.slug, (cfg or {}).get("llm_budget_usd"))
                break
            except httpx.HTTPError as e:
                if n == attempts - 1:
                    log.warning("llm_key_provision_failed", tenant=t.slug, error=str(e))
                await asyncio.sleep(2)


class Budget(BaseModel):
    provisioned: bool
    max_budget: float | None
    spend: float
    period: str
    resets_at: str | None


@router.get("/llm-budget")
async def llm_budget(c: TenantDep) -> Budget:
    await authorize(c, "can_manage_budgets", f"tenant:{c.tenant_id}")
    key = tenant_key(str(c.tenant_id))
    info = None
    if key:
        async with _gateway() as http:
            try:
                info = await _info(http, key)
            except httpx.HTTPError as e:
                log.warning("llm_budget_unavailable", error=str(e))
    return Budget(
        provisioned=info is not None,
        max_budget=(info or {}).get("max_budget"),
        spend=float((info or {}).get("spend") or 0),
        period=BUDGET_PERIOD,
        resets_at=(info or {}).get("budget_reset_at"),
    )
