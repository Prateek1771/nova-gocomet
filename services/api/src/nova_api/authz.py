"""The one place every route asks "may this caller do this?" (CLAUDE.md rule 10).

ponytail: M1 enforces tenant scope only (RLS enforces it again in the store). M4 swaps the body for
an OpenFGA Check with the caller's roles as contextual tuples; routes don't change. Never branch on
JWT roles here.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException

from nova_api.deps import Caller, CallerDep


@dataclass(frozen=True)
class TenantCaller:
    caller: Caller
    tenant_id: uuid.UUID

    @property
    def sub(self) -> str:
        return self.caller.principal.sub


async def tenant_caller(c: CallerDep) -> TenantCaller:
    if c.tenant_id is None:
        raise HTTPException(403, "this route needs a tenant (log in through an organization)")
    return TenantCaller(c, c.tenant_id)


TenantDep = Annotated[TenantCaller, Depends(tenant_caller)]


async def authorize(c: TenantCaller, relation: str, obj: str) -> None:
    """`relation`/`obj` use the FGA model's names (LLD §7): ("can_publish", "workflow:<tenant>/<key>")."""
    _ = (relation, obj)  # M4: await fga.check(user=f"user:{c.sub}", relation=relation, object=obj, context=…)
