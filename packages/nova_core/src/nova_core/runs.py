"""Run creation, shared by the API (POST /runs) and the engine (subflow nodes)."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class NewRun:
    run_id: uuid.UUID
    definition_id: uuid.UUID
    version: int
    config_version: int
    workflow_id: str


async def create_run(
    s: AsyncSession,
    tenant_id: uuid.UUID,
    key: str,
    input: dict[str, Any],
    version: int | None = None,
    config_version: int | None = None,
    subject: tuple[str, str] | None = None,
) -> NewRun:
    """Insert a `pending` run pinned to a published version and a TenantConfig version (latest by
    default). Raises LookupError when there's nothing published."""
    d = (
        await s.execute(
            text("""select id, version from workflow_definitions
                    where key = :k and status = 'published' and (cast(:v as int) is null or version = :v)
                    order by version desc limit 1"""),
            {"k": key, "v": version},
        )
    ).one_or_none()
    if d is None:
        raise LookupError(f"no published workflow {key!r}" + (f" v{version}" if version else ""))
    if config_version is None:
        config_version = (await s.execute(text("select max(version) from tenant_configs"))).scalar()
        if config_version is None:
            raise LookupError("tenant has no TenantConfig")
    run_id = uuid.uuid4()
    wf_id = f"run-{run_id}"
    await s.execute(
        text("""insert into workflow_runs (id, tenant_id, definition_id, config_version, subject_type,
                  subject_id, temporal_workflow_id, input)
                values (:id, :t, :d, :c, :st, :si, :w, cast(:i as jsonb))"""),
        {
            "id": run_id,
            "t": tenant_id,
            "d": d.id,
            "c": config_version,
            "st": subject and subject[0],
            "si": subject and subject[1],
            "w": wf_id,
            "i": json.dumps(input),
        },
    )
    return NewRun(run_id, d.id, d.version, config_version, wf_id)
