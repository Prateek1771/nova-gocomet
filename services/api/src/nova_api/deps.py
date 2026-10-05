import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from nova_core import db
from nova_core.auth import AuthError, Principal, TokenVerifier
from nova_core.settings import get_settings


@dataclass(frozen=True)
class Caller:
    principal: Principal
    tenant_id: uuid.UUID | None  # None only for platform_admin without an org
    tenant_slug: str | None


@lru_cache
def verifier() -> TokenVerifier:
    s = get_settings()
    return TokenVerifier(s.jwks_url, s.keycloak_issuer, s.oidc_audience, s.oidc_azp)


_bearer = HTTPBearer(auto_error=False)


async def caller(cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> Caller:
    if cred is None:
        raise HTTPException(401, "missing bearer token")
    try:
        p = await run_in_threadpool(verifier().verify, cred.credentials)  # JWKS fetch is blocking
    except AuthError as e:
        raise HTTPException(401, f"invalid token: {e}") from e
    if p.org_alias is None:
        return Caller(p, None, None)
    tenant_id = await _tenant_for(p)
    structlog.contextvars.bind_contextvars(tenant_id=str(tenant_id), sub=p.sub)
    return Caller(p, tenant_id, p.org_alias)


async def _tenant_for(p: Principal) -> uuid.UUID:
    """Organization alias == tenants.slug. The org id is bound on first sight and enforced after,
    so a recreated org with a reused alias can't inherit a tenant."""
    async with db.session() as s:
        row = (
            await s.execute(
                text("select id, keycloak_org_id from tenants where slug = :a"), {"a": p.org_alias}
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(403, "organization is not a Nova tenant")
        if p.org_id and row.keycloak_org_id is None:
            await s.execute(
                text("update tenants set keycloak_org_id = :o where id = :t"), {"o": p.org_id, "t": row.id}
            )
        elif p.org_id and row.keycloak_org_id != p.org_id:
            raise HTTPException(403, "organization id mismatch")
        tenant_id: uuid.UUID = row.id
        return tenant_id


CallerDep = Annotated[Caller, Depends(caller)]
