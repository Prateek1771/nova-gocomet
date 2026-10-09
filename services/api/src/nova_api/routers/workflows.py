"""Workflow definitions: draft (version 0) → validate → publish (immutable version N)."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from nova_api import definitions
from nova_api.authz import TenantDep, authorize
from nova_api.errors import ApiError
from nova_core import db
from nova_dsl import Issue, parse_workflow
from nova_dsl.models import ActionNode, AgentNode, HumanTaskNode, SubflowNode

router = APIRouter(prefix="/workflows", tags=["workflows"])
DRAFT = 0
KEY = r"^[a-z][a-z0-9_]*$"
# node class, the attribute naming what it references, the catalog section it must be in
REFS = ((AgentNode, "agent", "agents"), (ActionNode, "action", "actions"), (HumanTaskNode, "app", "apps"))


class DraftIn(BaseModel):
    yaml: str = Field(max_length=500_000)


class CreateIn(DraftIn):
    key: str = Field(pattern=KEY, max_length=64)


class Validation(BaseModel):
    valid: bool
    issues: list[dict[str, Any]]


class DraftOut(Validation):
    key: str
    yaml: str


class Published(BaseModel):
    key: str
    version: int


class Version(BaseModel):
    key: str
    version: int
    status: str
    yaml: str
    published_by: str | None
    published_at: str | None


class WorkflowSummary(BaseModel):
    key: str
    title: str | None
    latest_version: int | None
    has_draft: bool


async def _check(c: TenantDep, key: str, yaml: str) -> tuple[Any, list[Issue]]:
    """DSL + graph + CEL (nova_dsl), then references that need the store."""
    wf, issues = parse_workflow(yaml)
    if wf is None:
        return None, issues
    if wf.metadata.key != key:
        issues.append(Issue("key_mismatch", f"metadata.key is {wf.metadata.key!r}, expected {key!r}"))
    subflows = {n.workflow.split("@")[0] for n in wf.nodes if isinstance(n, SubflowNode)}
    if subflows:
        async with db.tenant_session(c.tenant_id) as s:
            known = set(
                (
                    await s.execute(
                        text(
                            "select distinct key from workflow_definitions"
                            " where status = 'published' and key = any(:k)"
                        ),
                        {"k": list(subflows)},
                    )
                ).scalars()
            )
        issues += [Issue("unknown_subflow", f"no published workflow {k!r}") for k in sorted(subflows - known)]
    cat = {k: {e["key"] for e in v} for k, v in definitions.catalog().items()}
    for n in wf.nodes:
        for cls, kind, ref in REFS:
            name = getattr(n, kind, None)
            if isinstance(n, cls) and name not in cat[ref]:
                issues.append(Issue(f"unknown_{kind}", f"no {kind} {name!r} in the catalog", n.id))
    return wf, issues


def _validation(issues: list[Issue]) -> dict[str, Any]:
    return {"valid": not issues, "issues": [i.dict() for i in issues]}


@router.get("")
async def list_workflows(c: TenantDep) -> list[WorkflowSummary]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text("""select key,
                      max(version) filter (where status = 'published') as latest,
                      bool_or(status = 'draft') as has_draft,
                      (array_agg(compiled -> 'metadata' ->> 'title' order by version desc)
                         filter (where status = 'published'))[1] as title
                    from workflow_definitions group by key order by key""")
        )
        return [
            WorkflowSummary(key=r.key, title=r.title, latest_version=r.latest, has_draft=r.has_draft)
            for r in rows
        ]


@router.post("", status_code=201)
async def create_workflow(c: TenantDep, body: CreateIn) -> DraftOut:
    await authorize(c, "can_design", f"tenant:{c.tenant_id}")
    _, issues = await _check(c, body.key, body.yaml)
    try:
        async with db.tenant_session(c.tenant_id) as s:
            await s.execute(
                text("""insert into workflow_definitions (tenant_id, key, version, status, yaml)
                        values (:t, :k, 0, 'draft', :y)"""),
                {"t": c.tenant_id, "k": body.key, "y": body.yaml},
            )
    except IntegrityError as e:
        raise HTTPException(409, f"workflow {body.key!r} already has a draft") from e
    return DraftOut(key=body.key, yaml=body.yaml, **_validation(issues))


@router.get("/{key}/draft")
async def get_draft(c: TenantDep, key: str) -> DraftOut:
    await authorize(c, "can_view", f"workflow:{c.tenant_id}/{key}")
    async with db.tenant_session(c.tenant_id) as s:
        yaml = (
            await s.execute(
                text("select yaml from workflow_definitions where key = :k and version = 0"), {"k": key}
            )
        ).scalar()
    if yaml is None:
        raise HTTPException(404, f"workflow {key!r} has no draft")
    _, issues = await _check(c, key, yaml)
    return DraftOut(key=key, yaml=yaml, **_validation(issues))


@router.put("/{key}/draft")
async def put_draft(c: TenantDep, key: str, body: DraftIn) -> DraftOut:
    """Saves even when invalid: a draft is work in progress; publish is the gate."""
    await authorize(c, "can_edit", f"workflow:{c.tenant_id}/{key}")
    _, issues = await _check(c, key, body.yaml)
    async with db.tenant_session(c.tenant_id) as s:
        await s.execute(
            text("""insert into workflow_definitions (tenant_id, key, version, status, yaml)
                    values (:t, :k, 0, 'draft', :y)
                    on conflict (tenant_id, key, version) do update set yaml = excluded.yaml"""),
            {"t": c.tenant_id, "k": key, "y": body.yaml},
        )
    return DraftOut(key=key, yaml=body.yaml, **_validation(issues))


@router.post("/{key}/validate")
async def validate(c: TenantDep, key: str, body: DraftIn | None = None) -> Validation:
    """Validates the posted YAML, or the saved draft when there's no body."""
    yaml = body.yaml if body else (await get_draft(c, key)).yaml
    _, issues = await _check(c, key, yaml)
    return Validation(**_validation(issues))


@router.post("/{key}/publish", status_code=201)
async def publish(c: TenantDep, key: str) -> Published:
    await authorize(c, "can_publish", f"workflow:{c.tenant_id}/{key}")
    draft = await get_draft(c, key)
    wf, issues = await _check(c, key, draft.yaml)
    if issues or wf is None:
        raise ApiError(
            422, "invalid_workflow", "fix the issues before publishing", [i.dict() for i in issues]
        )
    async with db.tenant_session(c.tenant_id) as s:
        version = (
            await s.execute(
                text("select coalesce(max(version), 0) + 1 from workflow_definitions where key = :k"),
                {"k": key},
            )
        ).scalar_one()
        wf.metadata.version = version
        try:
            await s.execute(
                text("""insert into workflow_definitions
                          (tenant_id, key, version, status, yaml, compiled, published_by, published_at)
                        values (:t, :k, :v, 'published', :y, cast(:c as jsonb), :by, now())"""),
                {
                    "t": c.tenant_id,
                    "k": key,
                    "v": version,
                    "y": draft.yaml,
                    "c": json.dumps(wf.model_dump(mode="json", by_alias=True)),
                    "by": c.sub,
                },
            )
        except IntegrityError as e:  # two publishes raced for the same version number
            raise HTTPException(409, "another publish just happened; retry") from e
    return Published(key=key, version=version)


class VersionBrief(BaseModel):
    version: int
    published_by: str | None
    published_at: str | None


@router.get("/{key}/versions")
async def list_versions(c: TenantDep, key: str) -> list[VersionBrief]:
    await authorize(c, "can_view", f"workflow:{c.tenant_id}/{key}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text("""select version, published_by, published_at from workflow_definitions
                    where key = :k and status <> 'draft' order by version desc"""),
            {"k": key},
        )
        return [
            VersionBrief(
                version=r.version,
                published_by=r.published_by,
                published_at=r.published_at.isoformat() if r.published_at else None,
            )
            for r in rows
        ]


@router.get("/{key}/versions/{version}")
async def get_version(c: TenantDep, key: str, version: int) -> Version:
    await authorize(c, "can_view", f"workflow:{c.tenant_id}/{key}")
    async with db.tenant_session(c.tenant_id) as s:
        r = (
            await s.execute(
                text("""select version, status, yaml, published_by, published_at from workflow_definitions
                        where key = :k and version = :v and status <> 'draft'"""),
                {"k": key, "v": version},
            )
        ).one_or_none()
    if r is None:
        raise HTTPException(404, f"{key} v{version} not found")
    return Version(
        key=key,
        version=r.version,
        status=r.status,
        yaml=r.yaml,
        published_by=r.published_by,
        published_at=r.published_at.isoformat() if r.published_at else None,
    )
