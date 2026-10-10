"""M6 (W3): an exception is raised once per shipment + type, whatever its status. The 0001 partial index
only guarded open rows, so a resolved exception that still breached its threshold was re-detected on the
next cycle and triaged again. `run_id` links the triage run; `resolved_at` + `note` record the close. The
trigger router starts one run per exception (any version), guarded by a unique subject on workflow_runs.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"

STATEMENTS = [
    "drop index exceptions_one_open",
    "alter table exceptions add constraint exceptions_once unique (tenant_id, shipment_id, type)",
    """alter table exceptions add column run_id uuid references workflow_runs,
         add column resolved_at timestamptz, add column note text""",
    "create index exceptions_recent on exceptions (tenant_id, detected_at desc)",
    """create unique index workflow_runs_one_per_exception on workflow_runs (tenant_id, subject_id)
         where subject_type = 'exception'""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("drop index workflow_runs_one_per_exception")
    op.execute("drop index exceptions_recent")
    op.execute("alter table exceptions drop column run_id, drop column resolved_at, drop column note")
    op.execute("alter table exceptions drop constraint exceptions_once")
    op.execute(
        "create unique index exceptions_one_open on exceptions (tenant_id, shipment_id, type) where status = 'open'"
    )
