"""Micro-app definitions (LLD §9): layout + bindings + output_schema. Schemas a FieldForm names are
inlined so the client renders from one response."""

from typing import Any

from fastapi import APIRouter

from nova_api import definitions
from nova_api.authz import TenantDep, authorize

router = APIRouter(prefix="/apps", tags=["apps"])


def _schema_refs(node: dict[str, Any]) -> set[str]:
    refs = {node["schema"]} if isinstance(node.get("schema"), str) else set()
    for child in node.get("children", []):
        refs |= _schema_refs(child)
    return refs


@router.get("/{key}")
async def get_app(c: TenantDep, key: str) -> dict[str, Any]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    app = definitions.app(key)
    return {**app, "schemas": {k: definitions.schema(k) for k in _schema_refs(app["layout"])}}
