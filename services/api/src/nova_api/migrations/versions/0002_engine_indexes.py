"""Indexes for the M1 read paths (inbox, run list, run detail).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"

INDEXES = {
    "human_tasks_inbox": "human_tasks (tenant_id, status, due_at)",
    "human_tasks_run": "human_tasks (run_id)",
    "run_steps_run": "run_steps (run_id, started_at)",
    "workflow_runs_recent": "workflow_runs (tenant_id, started_at desc)",
}


def upgrade() -> None:
    for name, target in INDEXES.items():
        op.execute(f"create index {name} on {target}")


def downgrade() -> None:
    for name in INDEXES:
        op.execute(f"drop index {name}")
